"""Publishing Agent (ARCHITECTURE.md §2.4) — Phase 2 M4.

Guardrails run in code, not just agent judgment (the same principle
quality_scoring's auto-reject gate already established): account health
tier and the daily cadence cap are checked here, before any provider call,
and block regardless of queue pressure. AI-disclosure fields are
propagated straight from the Video row (`contains_ai_voice`/
`contains_ai_visual`, computed by production_service.py) — never
re-derived or left for the operator to remember to set.

Mirrors production_service.py's shape: the Publication row is created
first (so a failure still leaves a durable, auditable row), the risky
provider call is wrapped in `agent_run` for the same versioning/logging
guarantee every other external call gets, and a retry-with-backoff wrapper
(publishing/retry.py) closes PHASE1_AUDIT_v2.md's F19 for this codebase's
first real external HTTP integration.
"""

import time
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from content_factory.agents.base import agent_run
from content_factory.config import Settings
from content_factory.db.models.account import OwnedAccount
from content_factory.db.models.enums import AccountHealthTier, AccountStatus, PublicationStatus, VideoStatus
from content_factory.db.models.publication import Publication
from content_factory.db.models.video import Video
from content_factory.logging_config import get_logger
from content_factory.publishing.base import PublishingProvider, PublishRequest
from content_factory.publishing.retry import call_with_retry

logger = get_logger(__name__)

_BLOCKED_HEALTH_TIERS = (AccountHealthTier.AT_RISK, AccountHealthTier.RESTRICTED)


class PublishingDisabled(Exception):
    pass


class AccountNotEligibleToPublish(Exception):
    pass


class CadenceCapExceeded(Exception):
    pass


def _todays_publication_count(db: Session, *, account_id: int) -> int:
    start_of_day = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return (
        db.query(Publication)
        .filter(
            Publication.account_id == account_id,
            Publication.status == PublicationStatus.PUBLISHED,
            Publication.published_at >= start_of_day,
        )
        .count()
    )


def publish_video(
    db: Session,
    *,
    video: Video,
    account: OwnedAccount,
    provider: PublishingProvider,
    settings: Settings,
    title: str,
    description: str,
    hashtags: list[str],
    scheduled_at: datetime | None = None,
) -> Publication:
    if not settings.publishing_enabled:
        raise PublishingDisabled("Publishing is disabled (PUBLISHING_ENABLED=false).")

    if account.status != AccountStatus.ACTIVE:
        raise AccountNotEligibleToPublish(f"Account status is {account.status.value}, not active.")

    if account.health_tier in _BLOCKED_HEALTH_TIERS:
        raise AccountNotEligibleToPublish(
            f"Account health tier is {account.health_tier.value}; publishing is blocked until it recovers."
        )

    posts_today = _todays_publication_count(db, account_id=account.id)
    if posts_today >= account.daily_post_cap:
        raise CadenceCapExceeded(f"Account has already published {posts_today}/{account.daily_post_cap} today.")

    campaign_id = video.script.idea.campaign_id if video.script and video.script.idea else None
    now = datetime.now(UTC)

    # Created before the provider call so a failure still leaves a durable,
    # auditable row (matching production_service.py's Video-row precedent),
    # not just a log line.
    publication = Publication(
        video_id=video.id,
        campaign_id=campaign_id,
        account_id=account.id,
        platform=account.platform,
        title=title,
        description=description,
        hashtags=hashtags,
        scheduled_at=scheduled_at or now,
        status=PublicationStatus.FAILED,
    )
    db.add(publication)
    db.flush()

    log = logger.bind(video_id=video.id, account_id=account.id, publication_id=publication.id)

    publish_request = PublishRequest(
        video_id=video.id,
        asset_url=video.asset_url or "",
        title=title,
        description=description,
        hashtags=hashtags,
        contains_ai_voice=video.contains_ai_voice,
        contains_ai_visual=video.contains_ai_visual,
        scheduled_at=scheduled_at,
    )

    try:
        with agent_run(
            db,
            agent_name="publishing_provider",
            scope="video.publish",
            entity_type="video",
            entity_id=video.id,
            cost_video_id=video.id,
            cost_campaign_id=campaign_id,
        ) as handle:
            start = time.monotonic()
            result = call_with_retry(lambda: provider.publish(publish_request))
            duration_ms = max(int((time.monotonic() - start) * 1000), 1)
            handle.record_output(
                provider=result.provider,
                model=None,
                model_version=None,
                prompt=f"{title}\n{description}",
                output_summary={"external_post_id": result.external_post_id, "published": result.published},
                cost_usd=0.0,
                duration_ms=duration_ms,
            )

        publication.publish_agent_run_id = handle.run.id
        publication.external_post_id = result.external_post_id
        publication.published_at = now if result.published else None
        publication.status = PublicationStatus.PUBLISHED if result.published else PublicationStatus.SCHEDULED
        if result.published:
            video.status = VideoStatus.PUBLISHED
        db.flush()
        log.info("video_published", status=publication.status.value, provider=result.provider)
    except Exception:
        publication.status = PublicationStatus.FAILED
        db.flush()
        log.error("video_publish_failed", exc_info=True)
        raise

    return publication
