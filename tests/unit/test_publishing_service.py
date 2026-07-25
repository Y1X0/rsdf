"""Phase 2 M4: publishing_service's guardrails (account health tier,
cadence cap, kill-switch) and the durable Publication row it leaves behind
on both success and failure."""

from datetime import UTC, datetime

import pytest

from content_factory.config import Settings
from content_factory.db.models.account import OwnedAccount
from content_factory.db.models.campaign import Campaign
from content_factory.db.models.content import ContentIdea, Script
from content_factory.db.models.enums import (
    AccountHealthTier,
    AccountPlatform,
    AccountStatus,
    ProcessingStatus,
    PublicationStatus,
)
from content_factory.db.models.video import Video
from content_factory.publishing.base import PublishingProvider, PublishRequest, PublishResult
from content_factory.services import publishing_service
from content_factory.services.publishing_service import (
    AccountNotEligibleToPublish,
    CadenceCapExceeded,
    PublishingDisabled,
)


class _FakePublishingProvider(PublishingProvider):
    def __init__(self, *, published: bool = True, raises: Exception | None = None):
        self._published = published
        self._raises = raises
        self.calls: list[PublishRequest] = []

    def publish(self, request: PublishRequest) -> PublishResult:
        self.calls.append(request)
        if self._raises:
            raise self._raises
        return PublishResult(provider="fake", published=self._published, external_post_id="ext-1")


def _make_video(db_session) -> Video:
    campaign = Campaign(brand_name="Acme")
    db_session.add(campaign)
    db_session.flush()
    idea = ContentIdea(campaign_id=campaign.id, concept_summary="idea")
    db_session.add(idea)
    db_session.flush()
    script = Script(
        idea_id=idea.id, variant_label="v1", hook_text="hook", full_text="full text",
        generation_status=ProcessingStatus.COMPLETED,
    )
    db_session.add(script)
    db_session.flush()
    video = Video(script_id=script.id, asset_url="/tmp/video.mp4", contains_ai_voice=True, contains_ai_visual=True)
    db_session.add(video)
    db_session.flush()
    return video


def _make_account(db_session, **overrides) -> OwnedAccount:
    account = OwnedAccount(platform=AccountPlatform.TIKTOK, handle="creator1", daily_post_cap=2)
    for field, value in overrides.items():
        setattr(account, field, value)
    db_session.add(account)
    db_session.flush()
    return account


def test_publish_video_succeeds_and_creates_publication(db_session):
    video = _make_video(db_session)
    account = _make_account(db_session)
    provider = _FakePublishingProvider(published=True)
    settings = Settings()

    publication = publishing_service.publish_video(
        db_session, video=video, account=account, provider=provider, settings=settings,
        title="Title", description="Desc", hashtags=["a", "b"],
    )

    assert publication.status == PublicationStatus.PUBLISHED
    assert publication.external_post_id == "ext-1"
    assert publication.published_at is not None
    assert video.status.value == "published"
    assert provider.calls[0].contains_ai_voice is True
    assert provider.calls[0].contains_ai_visual is True


def test_publish_video_with_manual_style_result_is_scheduled_not_published(db_session):
    video = _make_video(db_session)
    account = _make_account(db_session)
    provider = _FakePublishingProvider(published=False)
    settings = Settings()

    publication = publishing_service.publish_video(
        db_session, video=video, account=account, provider=provider, settings=settings,
        title="Title", description="Desc", hashtags=[],
    )

    assert publication.status == PublicationStatus.SCHEDULED
    assert publication.published_at is None


def test_publish_video_raises_when_disabled(db_session):
    video = _make_video(db_session)
    account = _make_account(db_session)
    provider = _FakePublishingProvider()
    settings = Settings(publishing_enabled=False)

    with pytest.raises(PublishingDisabled):
        publishing_service.publish_video(
            db_session, video=video, account=account, provider=provider, settings=settings,
            title="Title", description="Desc", hashtags=[],
        )


def test_publish_video_blocked_for_at_risk_account(db_session):
    video = _make_video(db_session)
    account = _make_account(db_session, health_tier=AccountHealthTier.AT_RISK)
    provider = _FakePublishingProvider()
    settings = Settings()

    with pytest.raises(AccountNotEligibleToPublish):
        publishing_service.publish_video(
            db_session, video=video, account=account, provider=provider, settings=settings,
            title="Title", description="Desc", hashtags=[],
        )
    assert provider.calls == []  # never reached the provider


def test_publish_video_blocked_for_restricted_account(db_session):
    video = _make_video(db_session)
    account = _make_account(db_session, health_tier=AccountHealthTier.RESTRICTED)
    provider = _FakePublishingProvider()
    settings = Settings()

    with pytest.raises(AccountNotEligibleToPublish):
        publishing_service.publish_video(
            db_session, video=video, account=account, provider=provider, settings=settings,
            title="Title", description="Desc", hashtags=[],
        )


def test_publish_video_blocked_for_paused_account_status(db_session):
    video = _make_video(db_session)
    account = _make_account(db_session, status=AccountStatus.PAUSED)
    provider = _FakePublishingProvider()
    settings = Settings()

    with pytest.raises(AccountNotEligibleToPublish):
        publishing_service.publish_video(
            db_session, video=video, account=account, provider=provider, settings=settings,
            title="Title", description="Desc", hashtags=[],
        )


def test_publish_video_blocked_by_cadence_cap(db_session):
    account = _make_account(db_session, daily_post_cap=1)
    settings = Settings()

    video1 = _make_video(db_session)
    publishing_service.publish_video(
        db_session, video=video1, account=account, provider=_FakePublishingProvider(published=True),
        settings=settings, title="Title", description="Desc", hashtags=[],
    )

    video2 = _make_video(db_session)
    with pytest.raises(CadenceCapExceeded):
        publishing_service.publish_video(
            db_session, video=video2, account=account, provider=_FakePublishingProvider(published=True),
            settings=settings, title="Title", description="Desc", hashtags=[],
        )


def test_publish_video_leaves_a_failed_publication_row_on_provider_error(db_session):
    video = _make_video(db_session)
    account = _make_account(db_session)
    provider = _FakePublishingProvider(raises=RuntimeError("boom"))
    settings = Settings()

    with pytest.raises(RuntimeError):
        publishing_service.publish_video(
            db_session, video=video, account=account, provider=provider, settings=settings,
            title="Title", description="Desc", hashtags=[],
        )

    from content_factory.db.models.publication import Publication

    pub = db_session.query(Publication).filter(Publication.video_id == video.id).one()
    assert pub.status == PublicationStatus.FAILED
