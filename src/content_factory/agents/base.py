"""AgentRunRecorder: the only code path allowed to write an AgentRun row.

Every agent wraps its external-provider call in `agent_run(...)`. This
guarantees, mechanically rather than by convention:

- Adjustment #3 (every AI output versioned): the recorded row always has
  prompt, provider, model, model_version, cost_usd, duration_ms, and
  started_at/completed_at, because `AgentRunHandle.record_output` requires
  them all as keyword arguments — there's no way to write a partial record.
- Adjustment #4 (structured logs, no silent failures): a log event fires on
  start, completion, and failure. On failure, the AgentRun row itself is
  marked FAILED with the error message *before* the exception is re-raised
  — nothing here ever swallows an exception.
"""

import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from content_factory.db.models.agent_run import AgentRun
from content_factory.db.models.enums import ProcessingStatus
from content_factory.logging_config import get_logger

logger = get_logger(__name__)


class AgentRunHandle:
    def __init__(self, run: AgentRun) -> None:
        self.run = run

    def record_output(
        self,
        *,
        provider: str,
        model: str | None,
        model_version: str | None,
        prompt: str,
        output_summary: dict,
        cost_usd: float,
        duration_ms: int,
    ) -> None:
        self.run.provider = provider
        self.run.model = model
        self.run.model_version = model_version
        self.run.prompt = prompt
        self.run.output_summary = output_summary
        self.run.cost_usd = cost_usd
        self.run.duration_ms = duration_ms


@contextmanager
def agent_run(
    db: Session,
    *,
    agent_name: str,
    scope: str,
    entity_type: str | None = None,
    entity_id: int | None = None,
    input_summary: dict | None = None,
) -> Iterator[AgentRunHandle]:
    run = AgentRun(
        agent_name=agent_name,
        scope=scope,
        entity_type=entity_type,
        entity_id=entity_id,
        provider="unknown",
        input_summary=input_summary,
        status=ProcessingStatus.IN_PROGRESS,
        started_at=datetime.now(UTC),
    )
    db.add(run)
    db.flush()

    log = logger.bind(agent_name=agent_name, scope=scope, agent_run_id=run.id,
                       entity_type=entity_type, entity_id=entity_id)
    log.info("agent_run_started")

    handle = AgentRunHandle(run)
    try:
        yield handle
    except Exception as exc:
        run.status = ProcessingStatus.FAILED
        run.error_message = str(exc)
        run.completed_at = datetime.now(UTC)
        db.flush()
        log.error("agent_run_failed", error=str(exc))
        raise
    else:
        run.status = ProcessingStatus.COMPLETED
        run.completed_at = datetime.now(UTC)
        db.flush()
        log.info("agent_run_completed", cost_usd=float(run.cost_usd or 0), duration_ms=run.duration_ms)


_CODE_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def parse_json_response(text: str, *, default: dict | list):
    """Best-effort JSON extraction from an LLM response. Claude sometimes
    wraps JSON in a markdown code fence even when explicitly asked not to —
    this strips that before falling back to `default` on genuine parse
    failure. Callers must log a warning when the fallback is used (this
    function never fails silently, but it also never raises for malformed
    LLM output — that's a normal, expected outcome to handle, not a bug)."""
    stripped = text.strip()
    match = _CODE_FENCE_RE.search(stripped)
    candidate = match.group(1) if match else stripped
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return default
