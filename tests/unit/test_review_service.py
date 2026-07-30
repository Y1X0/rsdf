from uuid import uuid4

from content_factory.db.models.campaign import Campaign
from content_factory.db.models.content import ContentIdea, Script
from content_factory.db.models.enums import ProcessingStatus, ReviewDecisionType, VideoStatus
from content_factory.db.models.niche import Niche
from content_factory.db.models.video import Video
from content_factory.services import review_service


def _make_video(db_session, niche_id: int | None = None) -> Video:
    if niche_id is None:
        niche = Niche(name=f"niche-{uuid4()}")
        db_session.add(niche)
        db_session.flush()
        niche_id = niche.id
    campaign = Campaign(brand_name="Acme", niche_id=niche_id)
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
    video = Video(script_id=script.id, status=VideoStatus.PENDING_REVIEW)
    db_session.add(video)
    db_session.flush()
    return video


def test_approve_sets_video_status_approved(db_session):
    video = _make_video(db_session)
    review_service.submit_review(
        db_session, video=video, reviewer_id="alice", decision=ReviewDecisionType.APPROVED
    )
    assert video.status == VideoStatus.APPROVED


def test_reject_sets_status_and_records_reason(db_session):
    video = _make_video(db_session)
    decision = review_service.submit_review(
        db_session, video=video, reviewer_id="alice", decision=ReviewDecisionType.REJECTED,
        reason_code="off_brand_tone", notes="too aggressive",
    )
    assert video.status == VideoStatus.REJECTED
    assert decision.reason_code == "off_brand_tone"
    assert decision.notes == "too aggressive"


def test_revision_requested_sets_status(db_session):
    video = _make_video(db_session)
    review_service.submit_review(
        db_session, video=video, reviewer_id="alice", decision=ReviewDecisionType.REVISION_REQUESTED,
        reason_code="hook_too_aggressive",
    )
    assert video.status == VideoStatus.REVISION_REQUESTED


def test_repeated_rejection_reason_flows_into_content_intelligence(db_session):
    niche = Niche(name="shared-niche")
    db_session.add(niche)
    db_session.flush()
    video1 = _make_video(db_session, niche_id=niche.id)
    video2 = _make_video(db_session, niche_id=niche.id)

    review_service.submit_review(
        db_session, video=video1, reviewer_id="alice", decision=ReviewDecisionType.REJECTED,
        reason_code="unverified_claim",
    )
    review_service.submit_review(
        db_session, video=video2, reviewer_id="bob", decision=ReviewDecisionType.REJECTED,
        reason_code="unverified_claim",
    )

    from content_factory.services import content_intelligence

    patterns = content_intelligence.get_patterns(db_session)
    matching = [p for p in patterns if p.description == "repeated_rejection:unverified_claim"]
    assert len(matching) == 1
    assert matching[0].outcome_tag == "known_bad"


def test_review_decisions_are_appended_not_overwritten(db_session):
    video = _make_video(db_session)
    review_service.submit_review(
        db_session, video=video, reviewer_id="alice", decision=ReviewDecisionType.REJECTED,
        reason_code="off_brand_tone",
    )
    review_service.submit_review(
        db_session, video=video, reviewer_id="alice", decision=ReviewDecisionType.APPROVED,
    )

    from content_factory.db.models.review import ReviewDecision

    decisions = db_session.query(ReviewDecision).filter(ReviewDecision.video_id == video.id).all()
    assert len(decisions) == 2
    assert video.status == VideoStatus.APPROVED
