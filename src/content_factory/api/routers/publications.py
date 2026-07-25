"""Publishing Agent (ARCHITECTURE.md §2.4) — Phase 2 M4.

`POST /videos/{id}/publish` requires the video to already be `approved`
(the human review gate, §7.1, is a hard prerequisite here — publishing
never bypasses it). Idempotency-protected like every other
workflow-triggering action (adjustment #5): publishing is irreversible in
a way that duplicating it would be a real, visible problem, not just
wasted spend.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from content_factory.api.deps import get_db
from content_factory.auth.dependencies import require_auth, require_operator
from content_factory.config import Settings, get_settings
from content_factory.db.models.account import OwnedAccount
from content_factory.db.models.enums import VideoStatus
from content_factory.db.models.publication import Publication
from content_factory.db.models.video import Video
from content_factory.publishing.factory import get_publishing_provider
from content_factory.schemas.publication import PublicationOut, PublishRequestBody
from content_factory.services import idempotency, publishing_service, token_encryption
from content_factory.services.publishing_service import (
    AccountNotEligibleToPublish,
    CadenceCapExceeded,
    PublishingDisabled,
)

router = APIRouter(tags=["publications"])


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

    access_token = None
    if account.encrypted_oauth_token:
        try:
            access_token = token_encryption.decrypt_token(account.encrypted_oauth_token, settings)
        except (ValueError, token_encryption.TokenEncryptionNotConfigured) as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from None

    def _load_existing(publication_id: int) -> Publication:
        return db.get(Publication, publication_id)

    def _work() -> Publication:
        provider = get_publishing_provider(account.platform, settings, access_token=access_token)
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
    except CadenceCapExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from None

    return PublicationOut.model_validate(publication)


@router.get("/publications", response_model=list[PublicationOut])
def list_publications(
    db: Session = Depends(get_db), _principal: dict = Depends(require_auth)
) -> list[PublicationOut]:
    pubs = db.query(Publication).order_by(Publication.created_at.desc()).all()
    return [PublicationOut.model_validate(p) for p in pubs]


@router.get("/publications/{publication_id}", response_model=PublicationOut)
def get_publication(
    publication_id: int, db: Session = Depends(get_db), _principal: dict = Depends(require_auth)
) -> PublicationOut:
    publication = db.get(Publication, publication_id)
    if publication is None:
        raise HTTPException(status_code=404, detail="Publication not found")
    return PublicationOut.model_validate(publication)
