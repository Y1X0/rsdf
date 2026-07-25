"""Script Agent (ARCHITECTURE.md §2.2) — Phase 1 version.

Generates multiple hook/script variants for A/B testing (goal #3), grounded
in hooks retrieved from the Content Intelligence Layer (goal #4) rather than
generated from nothing. Never calls Anthropic directly (adjustment #6) —
only through the injected LLMClient.
"""

from datetime import UTC, datetime
from string import ascii_uppercase

from sqlalchemy.orm import Session

from content_factory.agents.base import agent_run, parse_json_response
from content_factory.db.models.content import ContentIdea, Script
from content_factory.db.models.enums import ProcessingStatus
from content_factory.db.models.hook import HookLibrary
from content_factory.llm.base import LLMClient
from content_factory.logging_config import get_logger

logger = get_logger(__name__)

_SYSTEM_PROMPT = (
    "You are the Script Agent of an AI content production system. You write "
    "short-form video hooks and scripts optimized for watch time, completion "
    "rate, shares, and comments. You never fabricate statistics or claims "
    "about the sponsoring brand. Respond with a single JSON array only, no "
    "prose outside the JSON array."
)

_RESPONSE_SCHEMA_HINT = """
Return a JSON array with exactly {num_variants} objects, each shaped as:
{{
  "variant_label": "<short label>",
  "hook_text": "<the first 1-3 seconds of spoken hook>",
  "full_text": "<the full script body, including the hook>",
  "cta_text": "<call to action>",
  "target_duration_s": <integer seconds, typically 15-45>
}}
"""


class ScriptAgent:
    def __init__(self, llm_client: LLMClient) -> None:
        self._llm = llm_client

    def generate_variants(
        self,
        db: Session,
        *,
        idea: ContentIdea,
        retrieved_hooks: list[HookLibrary],
        num_variants: int = 3,
    ) -> list[Script]:
        log = logger.bind(idea_id=idea.id, campaign_id=idea.campaign_id)
        log.info("script_agent_started", num_variants=num_variants, retrieved_hook_count=len(retrieved_hooks))

        prompt = self._build_prompt(idea=idea, retrieved_hooks=retrieved_hooks, num_variants=num_variants)

        try:
            with agent_run(
                db,
                agent_name="script_agent",
                scope="idea.scripts",
                entity_type="content_idea",
                entity_id=idea.id,
                input_summary={"num_variants": num_variants, "retrieved_hook_count": len(retrieved_hooks)},
            ) as handle:
                response = self._llm.complete(system=_SYSTEM_PROMPT, prompt=prompt, max_tokens=3000)
                handle.record_output(
                    provider=response.provider,
                    model=response.model,
                    model_version=response.model_version,
                    prompt=prompt,
                    output_summary={"raw_text_chars": len(response.text)},
                    cost_usd=response.cost_usd,
                    duration_ms=response.duration_ms,
                )

            variants_data = parse_json_response(response.text, default=[])
            if not isinstance(variants_data, list) or not variants_data:
                log.warning("script_agent_empty_or_unparseable_response")
                variants_data = []

            scripts: list[Script] = []
            for i, variant in enumerate(variants_data):
                script = Script(
                    idea_id=idea.id,
                    variant_label=variant.get("variant_label") or f"variant_{i + 1}",
                    experiment_group=ascii_uppercase[i] if i < len(ascii_uppercase) else f"group_{i}",
                    hook_text=variant["hook_text"],
                    full_text=variant["full_text"],
                    cta_text=variant.get("cta_text"),
                    target_duration_s=variant.get("target_duration_s"),
                    generation_status=ProcessingStatus.COMPLETED,
                    agent_run_id=handle.run.id,
                )
                db.add(script)
                scripts.append(script)
            db.flush()

            log.info("script_agent_completed", variant_count=len(scripts))
        except Exception:
            log.error("script_agent_failed", exc_info=True)
            raise

        return scripts

    @staticmethod
    def _build_prompt(*, idea: ContentIdea, retrieved_hooks: list[HookLibrary], num_variants: int) -> str:
        hooks_block = "\n".join(
            f"- ({h.hook_type or 'unknown'}, score={h.best_viral_score}): {h.hook_text}"
            for h in retrieved_hooks
        ) or "(no prior hook data yet for this niche)"

        return (
            f"Content idea: {idea.concept_summary}\n\n"
            f"Highest-performing hooks previously observed for this niche:\n{hooks_block}\n\n"
            f"{_RESPONSE_SCHEMA_HINT.format(num_variants=num_variants)}"
        )
