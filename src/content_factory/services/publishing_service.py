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
(content_factory/retry.py) closes PHASE1_AUDIT_v2.md's F19 for this
codebase's first real external HTTP integration.
"""

import time
from dataclasses import dataclass
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
from content_factory.retry import call_with_retry
from content_factory.services import token_encryption

logger = get_logger(__name__)

_BLOCKED_HEALTH_TIERS = (AccountHealthTier.AT_RISK, AccountHealthTier.RESTRICTED)


class PublishingDisabled(Exception):
    pass


class AccountNotEligibleToPublish(Exception):
    pass


class CadenceCapExceeded(Exception):
    pass


class AssetNotPubliclyHosted(Exception):
    """Raised instead of ever handing a platform provider a local
    filesystem path it cannot reach. A real publish (TikTok's
    PULL_FROM_URL, Instagram/YouTube's media-upload-by-URL flows) requires
    a real http(s) URL the platform's own servers can fetch — see
    services/media_backup.py's public_url wiring. Local paths never even
    reach `provider.publish()`; this is the one enforcement point every
    publish path (manual and automatic) goes through."""

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

    if not (video.asset_url or "").startswith(("http://", "https://")):
        raise AssetNotPubliclyHosted(
            f"Video #{video.id}'s asset is not hosted at a public URL (asset_url={video.asset_url!r}); "
            "a platform can never reach a local filesystem path. Configure MEDIA_BACKUP_ENABLED, "
            "MEDIA_BACKUP_S3_BUCKET, and MEDIA_BACKUP_PUBLIC_BASE_URL, then re-render this video."
        )

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
        # commit(), not flush() - same P0-1/P0-2 commit-boundary reasoning
        # as production_service.render_video's own except block. Currently
        # masked in practice: the two current callers either wrap this in
        # idempotency.run_idempotent() (api/routers/publications.py, whose
        # own except block already commits) or swallow the exception
        # without re-raising (attempt_auto_publish below) - but committing
        # here too removes the implicit dependency on either behavior.
        publication.status = PublicationStatus.FAILED
        db.commit()
        log.error("video_publish_failed", exc_info=True)
        raise

    return publication


@dataclass
class AutoPublishOutcome:
    """Result of the Review -> Publish automatic cascade
    (`attempt_auto_publish`, called from api/routers/review.py right after
    an "approved" decision). `status` is always one of "published",
    "scheduled" (the honest default outcome with ManualPublishingProvider —
    a human still has to actually post it, per that provider's own
    docstring), "skipped" (a deliberate, safe non-guess — no eligible
    account, or more than one with no unambiguous match), or "failed" (the
    provider call itself raised). Never silent: `detail` always explains
    what happened and, on "skipped"/"failed", what to do about it."""

    status: str
    detail: str
    publication: Publication | None = None


def _niche_id_for_video(db: Session, *, video: Video) -> int | None:
    if video.script and video.script.idea and video.script.idea.campaign:
        return video.script.idea.campaign.niche_id
    if video.clip and video.clip.source_video and video.clip.source_video.campaign:
        return video.clip.source_video.campaign.niche_id
    return None


def _default_publish_content(video: Video) -> tuple[str, str]:
    if video.script:
        title = (video.script.hook_text or f"Video #{video.id}")[:300]
        description = (video.script.full_text or "")[:2000]
        return title, description
    if video.clip:
        title = (video.clip.hook_text or f"Clip #{video.id}")[:300]
        return title, ""
    return f"Video #{video.id}", ""


def _select_auto_publish_account(db: Session, *, niche_id: int | None) -> tuple[OwnedAccount | None, str | None]:
    """Only ever auto-selects an account when the choice is genuinely
    unambiguous — this is a real operational decision (which account, i.e.
    which real audience, gets this content), not something to guess at
    when multiple candidates exist."""
    candidates = (
        db.query(OwnedAccount)
        .filter(OwnedAccount.status == AccountStatus.ACTIVE, OwnedAccount.health_tier.notin_(_BLOCKED_HEALTH_TIERS))
        .all()
    )
    if not candidates:
        return None, "no eligible (active, non-at-risk/restricted) account is registered"

    if niche_id is not None:
        niche_matched = [a for a in candidates if a.niche_focus_id == niche_id]
        if len(niche_matched) == 1:
            return niche_matched[0], None
        if len(niche_matched) > 1:
            return None, (
                f"{len(niche_matched)} eligible accounts are focused on niche {niche_id}; ambiguous which one "
                "should publish this - publish manually via POST /videos/{id}/publish with an explicit account_id"
            )

    if len(candidates) == 1:
        return candidates[0], None
    return None, (
        f"{len(candidates)} eligible accounts exist with no unambiguous niche match; ambiguous which one should "
        "publish this - publish manually via POST /videos/{id}/publish with an explicit account_id"
    )


def attempt_auto_publish(db: Session, *, video: Video, settings: Settings) -> AutoPublishOutcome:
    """Best-effort: never raises. A failure here must not undo the human
    review decision that triggered it (review_service.submit_review has
    already committed-worthy state by the time this runs) — every outcome,
    including an unexpected exception, is reported back as a normal,
    non-throwing result instead."""
    from content_factory.publishing.factory import get_publishing_provider

    niche_id = _niche_id_for_video(db, video=video)
    account, skip_reason = _select_auto_publish_account(db, niche_id=niche_id)
    if account is None:
        logger.info("auto_publish_skipped", video_id=video.id, reason=skip_reason)
        return AutoPublishOutcome(status="skipped", detail=skip_reason)

    title, description = _default_publish_content(video)
    access_token = token_encryption.resolve_access_token_or_none(account, settings)
    provider = get_publishing_provider(
        account.platform, settings, access_token=access_token, account_id=account.platform_account_id
    )

    try:
        publication = publish_video(
            db,
            video=video,
            account=account,
            provider=provider,
            settings=settings,
            title=title,
            description=description,
            hashtags=[],
        )
    except (PublishingDisabled, AccountNotEligibleToPublish, CadenceCapExceeded, AssetNotPubliclyHosted) as exc:
        logger.info("auto_publish_skipped", video_id=video.id, reason=str(exc))
        return AutoPublishOutcome(status="skipped", detail=str(exc))
    except Exception as exc:
        logger.error("auto_publish_failed", video_id=video.id, exc_info=True)
        return AutoPublishOutcome(status="failed", detail=f"Auto-publish failed: {exc}")

    status = "published" if publication.status == PublicationStatus.PUBLISHED else "scheduled"
    detail = (
        f"Publication #{publication.id} via account #{account.id} ({account.platform.value}): "
        f"{publication.status.value}"
    )
    if status == "scheduled":
        detail += " - no real platform credentials configured; a human still needs to post this manually."
    return AutoPublishOutcome(status=status, detail=detail, publication=publication)
