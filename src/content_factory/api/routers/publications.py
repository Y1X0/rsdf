"""Publishing Agent (ARCHITECTURE.md §2.4) and Metrics Ingestion Automation
(ARCHITECTURE.md §2.5) — Phase 2 M4/M5.

`POST /videos/{id}/publish` requires the video to already be `approved`
(the human review gate, §7.1, is a hard prerequisite here — publishing
never bypasses it). Idempotency-protected like every other
workflow-triggering action (adjustment #5): publishing is irreversible in
a way that duplicating it would be a real, visible problem, not just
wasted spend.

`POST /publications/{id}/metrics/sync` feeds a fetched result through the
*existing*, unchanged `analytics_service.record_metrics` — the manual
`POST /videos/{id}/metrics` endpoint (api/routers/analytics.py) is
untouched and stays the fallback path forever, not just during a
transition, since ManualAnalyticsProvider has no automated data to offer.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from content_factory.analytics_ingestion.base import MetricsNotAutomated
from content_factory.analytics_ingestion.factory import get_analytics_provider
from content_factory.api.deps import get_db
from content_factory.api.pagination import Pagination, pagination_params
from content_factory.auth.dependencies import require_auth, require_operator
from content_factory.config import Settings, get_settings
from content_factory.db.models.account import OwnedAccount
from content_factory.db.models.enums import PublicationStatus, VideoStatus
from content_factory.db.models.publication import Publication
from content_factory.db.models.video import Video
from content_factory.publishing.factory import get_publishing_provider
from content_factory.schemas.analytics import MetricsResponse, ViralScoreOut
from content_factory.schemas.publication import PublicationOut, PublishRequestBody
from content_factory.services import analytics_service, idempotency, publishing_service, token_encryption
from content_factory.services.publishing_service import (
    AccountNotEligibleToPublish,
    AssetNotPubliclyHosted,
    CadenceCapExceeded,
    PublishingDisabled,
)

router = APIRouter(tags=["publications"])


def _decrypt_access_token(account: OwnedAccount, settings: Settings) -> str | None:
    if not account.encrypted_oauth_token:
        return None
    try:
        return token_encryption.decrypt_token(account.encrypted_oauth_token, settings)
    except (ValueError, token_encryption.TokenEncryptionError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from None


@router.post("/videos/{video_id}/publish", response_model=PublicationOut)
def publish_video(
    video_id: int,
    payload: PublishRequestBody,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    _principal: dict = Depends(require_operator),
) -> PublicationOut:
    video = db.get(Video, video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")
    if video.status != VideoStatus.APPROVED:
        raise HTTPException(
            status_code=409, detail=f"Video status is {video.status.value!r}; must be approved before publishing"
        )

    account = db.get(OwnedAccount, payload.account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")

    access_token = _decrypt_access_token(account, settings)

    def _load_existing(publication_id: int) -> Publication:
        return db.get(Publication, publication_id)

    def _work() -> Publication:
        provider = get_publishing_provider(
            account.platform, settings, access_token=access_token, account_id=account.platform_account_id
        )
        return publishing_service.publish_video(
            db,
            video=video,
            account=account,
            provider=provider,
            settings=settings,
            title=payload.title,
            description=payload.description,
            hashtags=payload.hashtags,
            scheduled_at=payload.scheduled_at,
        )

    try:
        publication, _created = idempotency.run_idempotent(
            db,
            scope="video.publish",
            idempotency_key=payload.idempotency_key,
            payload={"video_id": video_id, "account_id": payload.account_id, "title": payload.title},
            entity_type="publication",
            work_fn=_work,
            load_existing=_load_existing,
        )
    except PublishingDisabled as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    except AccountNotEligibleToPublish as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except AssetNotPubliclyHosted as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except CadenceCapExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from None

    return PublicationOut.model_validate(publication)


@router.get("/publications", response_model=list[PublicationOut])
def list_publications(
    db: Session = Depends(get_db),
    pagination: Pagination = Depends(pagination_params),
    _principal: dict = Depends(require_auth),
) -> list[PublicationOut]:
    pubs = (
        db.query(Publication)
        .order_by(Publication.created_at.desc())
        .offset(pagination.offset)
        .limit(pagination.limit)
        .all()
    )
    return [PublicationOut.model_validate(p) for p in pubs]


@router.get("/publications/{publication_id}", response_model=PublicationOut)
def get_publication(
    publication_id: int, db: Session = Depends(get_db), _principal: dict = Depends(require_auth)
) -> PublicationOut:
    publication = db.get(Publication, publication_id)
    if publication is None:
        raise HTTPException(status_code=404, detail="Publication not found")
    return PublicationOut.model_validate(publication)


@router.post("/publications/{publication_id}/metrics/sync", response_model=MetricsResponse)
def sync_publication_metrics(
    publication_id: int,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    _principal: dict = Depends(require_operator),
) -> MetricsResponse:
    publication = db.get(Publication, publication_id)
    if publication is None:
        raise HTTPException(status_code=404, detail="Publication not found")
    if publication.status != PublicationStatus.PUBLISHED or not publication.external_post_id:
        raise HTTPException(status_code=409, detail="Publication has not actually been published yet; nothing to sync")

    account = db.get(OwnedAccount, publication.account_id)
    video = db.get(Video, publication.video_id)
    access_token = _decrypt_access_token(account, settings)

    provider = get_analytics_provider(publication.platform, settings, access_token=access_token)
    try:
        fetched = provider.fetch_metrics(external_post_id=publication.external_post_id)
    except MetricsNotAutomated as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from None

    snapshot, score = analytics_service.record_metrics(
        db,
        video=video,
        views=fetched.views,
        avg_watch_time_s=fetched.avg_watch_time_s,
        completion_rate=fetched.completion_rate,
        rewatch_rate=fetched.rewatch_rate,
        shares=fetched.shares,
        comments=fetched.comments,
        likes=fetched.likes,
        saves=fetched.saves,
        source=publication.platform.value,
    )
    return MetricsResponse(
        video_id=video.id,
        views=snapshot.views,
        captured_at=snapshot.captured_at,
        viral_score=ViralScoreOut.model_validate(score),
    )
