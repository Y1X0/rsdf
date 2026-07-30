"""Real speaker diarization via pyannote.audio's pretrained pipeline.
`pyannote.audio` (and its own PyTorch dependency) is only imported here,
lazily, so the core install has no hard dependency on it - install with
`pip install '.[diarization]'` to use this provider. Selected by
diarization/factory.py only when DIARIZATION_PROVIDER=pyannote and a real
HUGGINGFACE_TOKEN is configured (config.Settings.resolved_diarization_provider
falls back to "null" otherwise) - the same opt-in shape as every other
optional real provider in this codebase.

NOT enabled by default, and deliberately NOT installed by the Dockerfile's
default `production` extra: pyannote.audio pulls in PyTorch and loads a
multi-hundred-MB neural pipeline into memory, which needs meaningfully
more RAM and CPU than this specific app's free-hosting tier has been shown
to survive. This same tier already needed FfmpegClipRenderer/
TemplatePillowRenderer's frame size cut in half (1080x1920 -> 540x960) and
`-preset ultrafast -threads 1` just to stop the *encode-only* ffmpeg path
from OOM-crashing the container (a real, measured production incident,
not a hypothetical one) - a PyTorch model load on top of that same host is
expected to make that failure mode worse, not better. Turning this
provider on is a decision for a deployment with real, adequate memory
headroom, made explicitly via DIARIZATION_PROVIDER/HUGGINGFACE_TOKEN, not
a default anyone gets by accident.

Requires a Hugging Face access token (pyannote's pretrained pipelines are
gated models requiring the user to accept their terms on huggingface.co
first) - see https://huggingface.co/pyannote/speaker-diarization-3.1.
"""

import time

from content_factory.diarization.base import DiarizationResult, SpeakerDiarizationProvider, SpeakerTurn

_PIPELINE_NAME = "pyannote/speaker-diarization-3.1"


class PyannoteDiarizationProvider(SpeakerDiarizationProvider):
    def __init__(self, hf_token: str) -> None:
        self._hf_token = hf_token

    def diarize(self, audio_path: str) -> DiarizationResult:
        try:
            from pyannote.audio import Pipeline
        except ImportError as exc:  # pragma: no cover - exercised only without the extra
            raise RuntimeError(
                "PyannoteDiarizationProvider requires the 'diarization' extra: "
                "pip install '.[diarization]' - and a real, adequately-resourced "
                "host; see this module's own docstring before enabling it."
            ) from exc

        start = time.monotonic()
        pipeline = Pipeline.from_pretrained(_PIPELINE_NAME, use_auth_token=self._hf_token)
        diarization = pipeline(audio_path)

        turns: list[SpeakerTurn] = []
        speakers: set[str] = set()
        for turn, _, speaker in diarization.itertracks(yield_label=True):
            turns.append(SpeakerTurn(start_s=turn.start, end_s=turn.end, speaker_label=speaker))
            speakers.add(speaker)

        duration_ms = int((time.monotonic() - start) * 1000)
        return DiarizationResult(
            turns=turns, speaker_count=len(speakers) or 1, provider="pyannote", duration_ms=duration_ms
        )
