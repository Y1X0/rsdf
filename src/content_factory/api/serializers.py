"""Small shared response-model assembly helpers, used by more than one
router so the "attach the latest quality score to a video" logic lives in
exactly one place."""

from sqlalchemy.orm import Session

from content_factory.db.models.quality import QualityScore
from content_factory.db.models.video import Video
from content_factory.schemas.video import QualityScoreOut, VideoOut


def to_video_out(db: Session, video: Video) -> VideoOut:
    quality = db.query(QualityScore).filter(QualityScore.video_id == video.id).one_or_none()
    out = VideoOut.model_validate(video)
    if quality is not None:
        out.quality_score = QualityScoreOut.model_validate(quality)
    return out
