"""Research Agent (goal #2), Script Agent (goal #3), Content Intelligence
read access (goal #4), and the production pipeline trigger (goal #5).
Research/script-generation/render triggers are all idempotency-protected
(adjustment #5) since they invoke paid external providers."""

from dataclasses import dataclass, field

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from content_factory.agents.research_agent import ResearchAgent
from content_factory.agents.script_agent import ScriptAgent
from content_factory.api.deps import (
    get_db,
    get_llm_client,
    get_notification_provider,
    get_tts_provider,
    get_video_renderer,
)
from content_factory.api.serializers import to_video_out
from content_factory.auth.dependencies import require_auth, require_operator
from content_factory.config import get_settings
from content_factory.db.models.campaign import Campaign
from content_factory.db.models.content import ContentIdea, ResearchBrief, Script
from content_factory.db.models.enums import ReviewDecisionType, VideoStatus
from content_factory.db.models.video import Video
from content_factory.llm.base import LLMClient
from content_factory.logging_config import get_logger
from content_factory.notifications.base import NotificationProvider
from content_factory.schemas.content import (
    ContentIdeaCreate,
    ContentIdeaOut,
    ResearchBriefOut,
    ResearchRequest,
    ScriptGenerateRequest,
    ScriptOut,
)
from content_factory.schemas.hook import HookOut, LearningPatternOut
from content_factory.schemas.video import RenderRequestBody, VideoOut
from content_factory.services import (
    budget_governor,
    content_intelligence,
    idempotency,
    production_service,
    quality_scoring,
    review_service,
)
from content_factory.video_production.renderer.base import VideoRenderer
from content_factory.video_production.tts.base import TTSProvider

logger = get_logger(__name__)
router = APIRouter(tags=["content"])


@router.post("/campaigns/{campaign_id}/research", response_model=ResearchBriefOut)
def run_research(
    campaign_id: int,
    payload: ResearchRequest,
    db: Session = Depends(get_db),
    llm_client: LLMClient = Depends(get_llm_client),
    notification_provider: NotificationProvider = Depends(get_notification_provider),
    _principal: dict = Depends(require_operator),
) -> ResearchBriefOut:
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found")

    budget_governor.enforce_budget(db, niche_id=campaign.niche_id, notification_provider=notification_provider)

    def _load_existing(brief_id: int) -> ResearchBrief:
        return db.get(ResearchBrief, brief_id)

    def _work() -> ResearchBrief:
        agent = ResearchAgent(llm_client)
        brief = agent.generate_brief(db, campaign=campaign, raw_notes=payload.raw_notes)
        content_intelligence.ingest_research_brief(db, brief=brief, niche_id=campaign.niche_id)
        return brief

    brief, _created = idempotency.run_idempotent(
        db,
        scope="campaign.research",
        idempotency_key=payload.idempotency_key,
        payload={"campaign_id": campaign_id, "raw_notes": payload.raw_notes},
        entity_type="research_brief",
        work_fn=_work,
        load_existing=_load_existing,
    )
    return ResearchBriefOut.model_validate(brief)


@router.get("/campaigns/{campaign_id}/research", response_model=list[ResearchBriefOut])
def list_research_briefs(
    campaign_id: int, db: Session = Depends(get_db), _principal: dict = Depends(require_auth)
) -> list[ResearchBriefOut]:
    briefs = (
        db.query(ResearchBrief)
        .filter(ResearchBrief.campaign_id == campaign_id)
        .order_by(ResearchBrief.created_at.desc())
        .all()
    )
    return [ResearchBriefOut.model_validate(b) for b in briefs]


@router.post("/campaigns/{campaign_id}/ideas", response_model=ContentIdeaOut)
def create_idea(
    campaign_id: int,
    payload: ContentIdeaCreate,
    db: Session = Depends(get_db),
    _principal: dict = Depends(require_operator),
) -> ContentIdeaOut:
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    idea = ContentIdea(
        campaign_id=campaign_id,
        concept_summary=payload.concept_summary,
        predicted_score=payload.predicted_score,
        source=payload.source,
    )
    db.add(idea)
    db.flush()
    return ContentIdeaOut.model_validate(idea)


@router.get("/campaigns/{campaign_id}/ideas", response_model=list[ContentIdeaOut])
def list_ideas(
    campaign_id: int, db: Session = Depends(get_db), _principal: dict = Depends(require_auth)
) -> list[ContentIdeaOut]:
    ideas = (
        db.query(ContentIdea)
        .filter(ContentIdea.campaign_id == campaign_id)
        .order_by(ContentIdea.created_at.desc())
        .all()
    )
    return [ContentIdeaOut.model_validate(i) for i in ideas]


@dataclass
class _ScriptBatchAnchor:
    """Idempotency's storage model expects one entity with an `.id`, but
    script generation produces several rows at once. This anchors the
    idempotency record to the content_idea and always carries the resulting
    scripts alongside it, so both the first call and a replayed call return
    the same shape."""

    id: int
    scripts: list[Script] = field(default_factory=list)


@router.post("/ideas/{idea_id}/scripts", response_model=list[ScriptOut])
def generate_scripts(
    idea_id: int,
    payload: ScriptGenerateRequest,
    db: Session = Depends(get_db),
    llm_client: LLMClient = Depends(get_llm_client),
    notification_provider: NotificationProvider = Depends(get_notification_provider),
    _principal: dict = Depends(require_operator),
) -> list[ScriptOut]:
    idea = db.get(ContentIdea, idea_id)
    if idea is None:
        raise HTTPException(status_code=404, detail="Content idea not found")
    niche_id = idea.campaign.niche_id if idea.campaign else None

    budget_governor.enforce_budget(db, niche_id=niche_id, notification_provider=notification_provider)

    def _load_existing(anchor_id: int) -> _ScriptBatchAnchor:
        scripts = db.query(Script).filter(Script.idea_id == anchor_id).order_by(Script.id).all()
        return _ScriptBatchAnchor(id=anchor_id, scripts=scripts)

    def _work() -> _ScriptBatchAnchor:
        agent = ScriptAgent(llm_client)
        hooks = content_intelligence.get_top_hooks(db, niche_id=niche_id, limit=5)
        scripts = agent.generate_variants(db, idea=idea, retrieved_hooks=hooks, num_variants=payload.num_variants)
        for script in scripts:
            content_intelligence.record_hook_usage(db, niche_id=niche_id, hook_text=script.hook_text)
        return _ScriptBatchAnchor(id=idea_id, scripts=scripts)

    batch, _created = idempotency.run_idempotent(
        db,
        scope="idea.scripts.generate",
        idempotency_key=payload.idempotency_key,
        payload={"idea_id": idea_id, "num_variants": payload.num_variants},
        entity_type="content_idea",
        work_fn=_work,
        load_existing=_load_existing,
    )
    return [ScriptOut.model_validate(s) for s in batch.scripts]


@router.get("/ideas/{idea_id}/scripts", response_model=list[ScriptOut])
def list_scripts(
    idea_id: int, db: Session = Depends(get_db), _principal: dict = Depends(require_auth)
) -> list[ScriptOut]:
    scripts = db.query(Script).filter(Script.idea_id == idea_id).order_by(Script.created_at.desc()).all()
    return [ScriptOut.model_validate(s) for s in scripts]


@router.get("/scripts/{script_id}", response_model=ScriptOut)
def get_script(
    script_id: int, db: Session = Depends(get_db), _principal: dict = Depends(require_auth)
) -> ScriptOut:
    script = db.get(Script, script_id)
    if script is None:
        raise HTTPException(status_code=404, detail="Script not found")
    return ScriptOut.model_validate(script)


@router.post("/scripts/{script_id}/render", response_model=VideoOut)
def render_script(
    script_id: int,
    payload: RenderRequestBody,
    db: Session = Depends(get_db),
    tts_provider: TTSProvider = Depends(get_tts_provider),
    video_renderer: VideoRenderer = Depends(get_video_renderer),
    notification_provider: NotificationProvider = Depends(get_notification_provider),
    _principal: dict = Depends(require_operator),
) -> VideoOut:
    script = db.get(Script, script_id)
    if script is None:
        raise HTTPException(status_code=404, detail="Script not found")
    campaign = script.idea.campaign

    budget_governor.enforce_budget(
        db, niche_id=campaign.niche_id if campaign else None, notification_provider=notification_provider
    )

    def _load_existing(video_id: int) -> Video:
        return db.get(Video, video_id)

    def _work() -> Video:
        video = Video(script_id=script.id, status=VideoStatus.PENDING_RENDER)
        db.add(video)
        db.flush()

        production_service.render_video(
            db,
            video=video,
            script=script,
            tts_provider=tts_provider,
            video_renderer=video_renderer,
            template_id=payload.template_id or production_service.DEFAULT_TEMPLATE_ID,
        )
        quality = quality_scoring.score_video(
            db, video=video, script=script, campaign=campaign, niche_id=campaign.niche_id
        )

        settings = get_settings()
        reject_reason = quality_scoring.determine_auto_reject_reason(quality, settings)
        if reject_reason is not None:
            review_service.submit_review(
                db,
                video=video,
                reviewer_id="system:quality_gate",
                decision=ReviewDecisionType.REJECTED,
                reason_code=reject_reason,
                notes="Auto-rejected by the quality gate (Phase 2 M2).",
            )
        else:
            video.status = VideoStatus.PENDING_REVIEW
        db.flush()
        return video

    video, _created = idempotency.run_idempotent(
        db,
        scope="script.render",
        idempotency_key=payload.idempotency_key,
        payload={"script_id": script_id, "template_id": payload.template_id},
        entity_type="video",
        work_fn=_work,
        load_existing=_load_existing,
    )
    return to_video_out(db, video)


@router.get("/hooks", response_model=list[HookOut])
def list_hooks(
    niche_id: int | None = None,
    limit: int = 20,
    db: Session = Depends(get_db),
    _principal: dict = Depends(require_auth),
) -> list[HookOut]:
    hooks = content_intelligence.get_top_hooks(db, niche_id=niche_id, limit=limit)
    return [HookOut.model_validate(h) for h in hooks]


@router.get("/patterns", response_model=list[LearningPatternOut])
def list_patterns(
    niche_id: int | None = None, db: Session = Depends(get_db), _principal: dict = Depends(require_auth)
) -> list[LearningPatternOut]:
    patterns = content_intelligence.get_patterns(db, niche_id=niche_id)
    return [LearningPatternOut.model_validate(p) for p in patterns]
