"""Small shared response-model assembly helpers, used by more than one
router so the "attach the latest quality score to a video" logic lives in
exactly one place."""

from sqlalchemy.orm import Session

from content_factory.db.models.account import OwnedAccount
from content_factory.db.models.quality import QualityScore
from content_factory.db.models.video import Video
from content_factory.schemas.account import OwnedAccountOut
from content_factory.schemas.video import QualityScoreOut, VideoOut


def to_video_out(db: Session, video: Video) -> VideoOut:
    quality = db.query(QualityScore).filter(QualityScore.video_id == video.id).one_or_none()
    out = VideoOut.model_validate(video)
    if quality is not None:
        out.quality_score = QualityScoreOut.model_validate(quality)
    return out


def to_account_out(account: OwnedAccount) -> OwnedAccountOut:
    """`has_credentials` is derived, never the raw token — the token itself
    (encrypted or not) never leaves this function."""
    return OwnedAccountOut(
        id=account.id,
        platform=account.platform,
        handle=account.handle,
        has_credentials=bool(account.encrypted_oauth_token),
        niche_focus_id=account.niche_focus_id,
        health_score=account.health_score,
        health_tier=account.health_tier,
        warmup_status=account.warmup_status,
        daily_post_cap=account.daily_post_cap,
        status=account.status,
        created_at=account.created_at,
        updated_at=account.updated_at,
    )
