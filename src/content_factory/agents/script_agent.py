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
from content_factory.services.hook_scoring import (
    HOOK_FRAMEWORKS,
    format_hook_frameworks_for_prompt,
    score_hook_strength,
)

logger = get_logger(__name__)

# Read from the model itself, not duplicated as a magic number: a real LLM
# has no reason to respect "keep this under 50 characters" just because
# the prompt schema hint says "a short label" - a verbose variant_label
# (e.g. a whole descriptive sentence instead of "A"/"variant_1") is a
# genuine, observed real-provider behavior, not a hypothetical. Postgres
# enforces the column's VARCHAR(50) bound at INSERT time regardless of
# what Python does, so silently letting an oversized value through was a
# real, reproduced production 500 - truncating here is the fix, not just
# defense in depth.
_VARIANT_LABEL_MAX_LENGTH = Script.__table__.columns["variant_label"].type.length

_SYSTEM_PROMPT = (
    "You are the Script Agent of an AI content production system. You write "
    "short-form video hooks and scripts optimized for watch time, completion "
    "rate, shares, and comments. Every hook must use one of the named, "
    "proven hook frameworks you are given - pick the one that best fits the "
    "idea, don't invent a new one. You never fabricate statistics or claims "
    "about the sponsoring brand. Respond with a single JSON array only, no "
    "prose outside the JSON array."
)

_RESPONSE_SCHEMA_HINT = """
Proven short-form hook frameworks (pick exactly one per variant - use its
key as "hook_framework"):
{hook_frameworks}

The first 1-3 seconds are what decides whether the viewer keeps watching
at all - the hook must land immediately, before any setup or context.

Return a JSON array with exactly {num_variants} objects, each shaped as:
{{
  "variant_label": "<short label>",
  "hook_framework": "<one of the framework keys above>",
  "hook_text": "<the first 1-3 seconds of spoken hook, using that framework>",
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
                cost_campaign_id=idea.campaign_id,
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
            skipped_variants = 0
            for i, variant in enumerate(variants_data):
                # A real LLM's JSON array isn't guaranteed to match the
                # requested schema item-by-item (a missing field, a plain
                # string instead of an object, etc.) even when the overall
                # response parses fine - that's a normal, expected variance
                # in real provider output, not a bug to crash the whole
                # batch over. Skip just the malformed item and keep the
                # rest, the same "degrade gracefully, log why" treatment
                # already given to a wholly empty/unparseable response.
                if not isinstance(variant, dict) or not variant.get("hook_text") or not variant.get("full_text"):
                    skipped_variants += 1
                    continue
                variant_label = str(variant.get("variant_label") or f"variant_{i + 1}")[:_VARIANT_LABEL_MAX_LENGTH]
                # A real LLM can return a framework key that isn't in our
                # own taxonomy (typo, invented one despite the prompt) -
                # store it only if it's actually one we recognize, rather
                # than let junk accumulate in a column meant to be a
                # stable, machine-usable identifier.
                hook_framework = variant.get("hook_framework")
                if hook_framework not in HOOK_FRAMEWORKS:
                    hook_framework = None
                hook_strength_score = score_hook_strength(variant["hook_text"]).overall
                script = Script(
                    idea_id=idea.id,
                    variant_label=variant_label,
                    experiment_group=ascii_uppercase[i] if i < len(ascii_uppercase) else f"group_{i}",
                    hook_text=variant["hook_text"],
                    hook_framework=hook_framework,
                    hook_strength_score=hook_strength_score,
                    full_text=variant["full_text"],
                    cta_text=variant.get("cta_text"),
                    target_duration_s=variant.get("target_duration_s"),
                    generation_status=ProcessingStatus.COMPLETED,
                    agent_run_id=handle.run.id,
                )
                db.add(script)
                scripts.append(script)
            db.flush()

            if skipped_variants:
                log.warning(
                    "script_agent_skipped_malformed_variants",
                    skipped_count=skipped_variants,
                    total_count=len(variants_data),
                )

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
            f"{_RESPONSE_SCHEMA_HINT.format(num_variants=num_variants, hook_frameworks=format_hook_frameworks_for_prompt())}"
        )
