"""Human Review workflow (ARCHITECTURE.md §7.1, goal #6): approve / reject /
request_revision, with every decision and reason stored (append-only —
review_decisions is a log, not a single mutable field on Video)."""

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from content_factory.db.models.enums import ReviewDecisionType, VideoStatus
from content_factory.db.models.review import ReviewDecision
from content_factory.db.models.video import Video
from content_factory.logging_config import get_logger
from content_factory.services import content_intelligence

logger = get_logger(__name__)

_STATUS_BY_DECISION = {
    ReviewDecisionType.APPROVED: VideoStatus.APPROVED,
    ReviewDecisionType.REJECTED: VideoStatus.REJECTED,
    ReviewDecisionType.REVISION_REQUESTED: VideoStatus.REVISION_REQUESTED,
}


def submit_review(
    db: Session,
    *,
    video: Video,
    reviewer_id: str,
    decision: ReviewDecisionType,
    reason_code: str | None = None,
    notes: str | None = None,
) -> ReviewDecision:
    record = ReviewDecision(
        video_id=video.id,
        reviewer_id=reviewer_id,
        decision=decision,
        reason_code=reason_code,
        notes=notes,
        decided_at=datetime.now(UTC),
    )
    db.add(record)

    video.status = _STATUS_BY_DECISION[decision]
    db.flush()

    niche_id = None
    if video.script and video.script.idea and video.script.idea.campaign:
        niche_id = video.script.idea.campaign.niche_id

    content_intelligence.record_review_pattern(
        db, niche_id=niche_id, decision=decision, reason_code=reason_code, video_id=video.id
    )

    logger.info(
        "review_submitted",
        video_id=video.id,
        decision=decision.value,
        reason_code=reason_code,
        reviewer_id=reviewer_id,
    )
    return record
