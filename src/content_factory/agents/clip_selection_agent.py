"""Clip Selection Agent — the "understand the long video and pick the best
moments" step of the clip factory (montage) pipeline. Given a source
video's timestamped transcript, asks the LLM to identify the segments most
likely to perform well as standalone short-form clips, each with a hook and
a predicted score — the same structured-JSON-over-LLMClient shape as
ScriptAgent, never calling a provider directly (adjustment #6).
"""

from sqlalchemy.orm import Session

from content_factory.agents.base import agent_run, parse_json_response
from content_factory.db.models.clip import Clip
from content_factory.db.models.enums import ClipStatus
from content_factory.db.models.source_video import SourceVideo
from content_factory.llm.base import LLMClient
from content_factory.logging_config import get_logger
from content_factory.transcription.base import TranscriptSegment
from content_factory.video_clipping.scene_detection import snap_to_nearest_scene_change

logger = get_logger(__name__)

_SYSTEM_PROMPT = (
    "You are the Clip Selection Agent of an AI content production system. "
    "You are given a timestamped transcript of a long-form video and must "
    "identify the specific moments most likely to perform well as "
    "standalone short-form clips (a strong hook, a self-contained idea, "
    "high emotional or informational density). You never invent content "
    "that isn't in the transcript, and every start/end time you return "
    "must fall within the transcript's own timestamp range. Respond with a "
    "single JSON array only, no prose outside the JSON array."
)

_RESPONSE_SCHEMA_HINT = """
Return a JSON array with up to {max_clips} objects, ordered best-first, each shaped as:
{{
  "start_s": <float, seconds into the source video>,
  "end_s": <float, seconds into the source video, typically 15-90 seconds after start_s>,
  "hook_text": "<a short, punchy hook to overlay on the clip, drawn from or inspired by this moment>",
  "predicted_score": <float 0-1, your estimate of how well this clip will perform>,
  "reason": "<one sentence on why this moment stands out>"
}}
"""


class ClipSelectionAgent:
    def __init__(self, llm_client: LLMClient) -> None:
        self._llm = llm_client

    def select_clips(
        self,
        db: Session,
        *,
        source_video: SourceVideo,
        segments: list[TranscriptSegment],
        max_clips: int = 5,
        scene_changes: list[float] | None = None,
    ) -> list[Clip]:
        log = logger.bind(source_video_id=source_video.id)
        log.info("clip_selection_agent_started", segment_count=len(segments), max_clips=max_clips)
        scene_changes = scene_changes or []

        prompt = self._build_prompt(segments=segments, max_clips=max_clips)

        try:
            with agent_run(
                db,
                agent_name="clip_selection_agent",
                scope="source_video.analyze",
                entity_type="source_video",
                entity_id=source_video.id,
                input_summary={"segment_count": len(segments), "max_clips": max_clips},
            ) as handle:
                response = self._llm.complete(system=_SYSTEM_PROMPT, prompt=prompt, max_tokens=2000)
                handle.record_output(
                    provider=response.provider,
                    model=response.model,
                    model_version=response.model_version,
                    prompt=prompt,
                    output_summary={"raw_text_chars": len(response.text)},
                    cost_usd=response.cost_usd,
                    duration_ms=response.duration_ms,
                )

            candidates = parse_json_response(response.text, default=[])
            if not isinstance(candidates, list) or not candidates:
                log.warning("clip_selection_agent_empty_or_unparseable_response")
                candidates = []

            max_end = max((s.end_s for s in segments), default=0.0)
            clips: list[Clip] = []
            for candidate in candidates:
                try:
                    start_s = float(candidate["start_s"])
                    end_s = float(candidate["end_s"])
                except (KeyError, TypeError, ValueError):
                    continue
                # Fail-closed bounds check: an LLM-hallucinated timestamp
                # outside the real transcript can't become a clip request
                # against the ffmpeg renderer.
                if start_s < 0 or end_s <= start_s or (max_end and end_s > max_end + 1.0):
                    continue
                if scene_changes:
                    # Pull the LLM's suggested boundaries onto a real
                    # visual cut when one is close by, so the rendered
                    # clip doesn't start/end mid-shot - purely a
                    # refinement of the LLM's own choice, never a
                    # requirement (falls back to the LLM's exact
                    # timestamps whenever no nearby cut exists).
                    snapped_start = snap_to_nearest_scene_change(start_s, scene_changes)
                    snapped_end = snap_to_nearest_scene_change(end_s, scene_changes)
                    if snapped_end > snapped_start:
                        start_s, end_s = snapped_start, snapped_end
                clip = Clip(
                    source_video_id=source_video.id,
                    start_s=start_s,
                    end_s=end_s,
                    hook_text=candidate.get("hook_text"),
                    predicted_score=candidate.get("predicted_score"),
                    reason=candidate.get("reason"),
                    status=ClipStatus.SUGGESTED,
                )
                db.add(clip)
                clips.append(clip)
            db.flush()

            log.info("clip_selection_agent_completed", clip_count=len(clips))
        except Exception:
            log.error("clip_selection_agent_failed", exc_info=True)
            raise

        return clips

    @staticmethod
    def _build_prompt(*, segments: list[TranscriptSegment], max_clips: int) -> str:
        transcript_block = "\n".join(f"[{s.start_s:.1f}-{s.end_s:.1f}] {s.text}" for s in segments) or (
            "(empty transcript)"
        )
        return (
            f"Timestamped transcript:\n{transcript_block}\n\n"
            f"{_RESPONSE_SCHEMA_HINT.format(max_clips=max_clips)}"
        )
