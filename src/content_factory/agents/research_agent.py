"""Research Agent (ARCHITECTURE.md §2.1) — Phase 1 version.

Phase 1 has no automated scraping (Whop/TikTok Creative Center access is
unconfirmed per ARCHITECTURE.md §0), so this agent's job is narrower than
the full architecture: *structure* human-supplied raw material (competitor
post transcripts, trend notes pasted in by the operator) into a brief plus
extractable hooks/patterns, via the LLM. It never calls Anthropic directly —
only through the injected LLMClient (adjustment #6).
"""

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from content_factory.agents.base import agent_run, parse_json_response
from content_factory.db.models.campaign import Campaign
from content_factory.db.models.content import ResearchBrief
from content_factory.db.models.enums import ProcessingStatus
from content_factory.llm.base import LLMClient
from content_factory.logging_config import get_logger

logger = get_logger(__name__)

_SYSTEM_PROMPT = (
    "You are the Research Agent of an AI content production system. You "
    "analyze short-form video campaigns and competitor content to produce "
    "a structured brief. You never fabricate specific view/engagement "
    "numbers you were not given. Respond with a single JSON object only, "
    "no prose outside the JSON."
)

_RESPONSE_SCHEMA_HINT = """
Return a JSON object with exactly these keys:
{
  "brief_text": "<2-4 sentence human-readable summary>",
  "niche_insights": "<what's working in this niche right now>",
  "competitor_patterns": [
    {"pattern_type": "hook"|"structure"|"timing", "description": "<pattern observed>"}
  ],
  "competitor_hooks": [
    {"hook_text": "<a hook, paraphrased/generalized, not verbatim-copied>", "hook_type": "<question|shock_stat|controversy|story_open|callout|countdown|other>"}
  ],
  "recommended_angles": ["<content angle to try>", "..."]
}
"""


class ResearchAgent:
    def __init__(self, llm_client: LLMClient) -> None:
        self._llm = llm_client

    def generate_brief(self, db: Session, *, campaign: Campaign, raw_notes: str) -> ResearchBrief:
        brief = ResearchBrief(
            campaign_id=campaign.id,
            status=ProcessingStatus.IN_PROGRESS,
            raw_input_notes=raw_notes,
            requested_at=datetime.now(UTC),
        )
        db.add(brief)
        db.flush()

        log = logger.bind(campaign_id=campaign.id, brief_id=brief.id)
        log.info("research_agent_started")

        prompt = self._build_prompt(campaign=campaign, raw_notes=raw_notes)

        try:
            with agent_run(
                db,
                agent_name="research_agent",
                scope="campaign.research",
                entity_type="campaign",
                entity_id=campaign.id,
                input_summary={"raw_notes_chars": len(raw_notes)},
                cost_campaign_id=campaign.id,
            ) as handle:
                response = self._llm.complete(system=_SYSTEM_PROMPT, prompt=prompt, max_tokens=2048)
                handle.record_output(
                    provider=response.provider,
                    model=response.model,
                    model_version=response.model_version,
                    prompt=prompt,
                    output_summary={"raw_text_chars": len(response.text)},
                    cost_usd=response.cost_usd,
                    duration_ms=response.duration_ms,
                )

            data = parse_json_response(response.text, default={})
            if not data:
                log.warning("research_agent_empty_or_unparseable_response")

            brief.brief_text = data.get("brief_text")
            brief.structured_data = data
            brief.agent_run_id = handle.run.id
            brief.status = ProcessingStatus.COMPLETED
            brief.completed_at = datetime.now(UTC)
            db.flush()

            log.info(
                "research_agent_completed",
                hook_count=len(data.get("competitor_hooks", [])),
                pattern_count=len(data.get("competitor_patterns", [])),
            )
        except Exception:
            # commit(), not flush() - same P0-1/P0-2 commit-boundary
            # reasoning as production_service.render_video's own except
            # block. Currently masked in practice (always called through
            # idempotency.run_idempotent(), whose own except block already
            # commits - see api/routers/content.py's run_research), but
            # committing here too removes the implicit dependency on that
            # caller behavior.
            brief.status = ProcessingStatus.FAILED
            brief.completed_at = datetime.now(UTC)
            db.commit()
            log.error("research_agent_failed", exc_info=True)
            raise

        return brief

    @staticmethod
    def _build_prompt(*, campaign: Campaign, raw_notes: str) -> str:
        return (
            f"Campaign brand: {campaign.brand_name}\n"
            f"Payout model: {campaign.payout_model or 'unknown'}\n"
            f"CPM rate: {campaign.cpm_rate if campaign.cpm_rate is not None else 'unknown'}\n"
            f"Campaign rules:\n{campaign.rules_text or '(none provided)'}\n\n"
            f"Raw competitor/trend notes supplied by the operator:\n{raw_notes or '(none provided)'}\n\n"
            f"{_RESPONSE_SCHEMA_HINT}"
        )
