import json
from pathlib import Path

import pytest

from content_factory.db.models.enums import ClipStatus, ProcessingStatus, VideoStatus
from content_factory.db.models.niche import Niche
from content_factory.diarization.base import DiarizationResult, SpeakerTurn
from content_factory.llm.providers.fake_provider import FakeLLMClient
from content_factory.services import clip_service, content_intelligence
from content_factory.transcription.base import TranscriptionResult, TranscriptSegment, TranscriptWord
from content_factory.video_clipping.base import ClipRenderResult
from content_factory.video_clipping.providers.null_clip_renderer import NullClipRenderer


def _make_niche(db_session, name="finance") -> Niche:
    niche = Niche(name=name)
    db_session.add(niche)
    db_session.flush()
    return niche


class _FakeTranscriptionProvider:
    def __init__(self, result: TranscriptionResult) -> None:
        self._result = result

    def transcribe(self, audio_path: str) -> TranscriptionResult:
        return self._result


class _FakeDiarizationProvider:
    def __init__(self, result: DiarizationResult) -> None:
        self._result = result

    def diarize(self, audio_path: str) -> DiarizationResult:
        return self._result


class _BoomDiarizationProvider:
    def diarize(self, audio_path: str) -> DiarizationResult:
        raise RuntimeError("diarization model unavailable")


def test_register_source_video_creates_row(db_session):
    source_video = clip_service.register_source_video(
        db_session, campaign_id=None, title="My Video", storage_path="/tmp/x.mp4"
    )
    assert source_video.id is not None
    assert source_video.transcription_status == ProcessingStatus.PENDING
    assert source_video.analysis_status == ProcessingStatus.PENDING


def test_transcribe_source_video_populates_transcript_and_segments(db_session):
    source_video = clip_service.register_source_video(
        db_session, campaign_id=None, title="My Video", storage_path="/tmp/x.mp4"
    )
    provider = _FakeTranscriptionProvider(
        TranscriptionResult(
            text="hello world",
            segments=[TranscriptSegment(start_s=0.0, end_s=3.0, text="hello world")],
            words=[
                TranscriptWord(start_s=0.0, end_s=1.4, word="hello"),
                TranscriptWord(start_s=1.4, end_s=3.0, word="world"),
            ],
            provider="fake",
            model="fake-model",
            duration_s=3.0,
        )
    )

    result = clip_service.transcribe_source_video(db_session, source_video=source_video, transcription_provider=provider)

    assert result.transcription_status == ProcessingStatus.COMPLETED
    assert result.transcript_text == "hello world"
    assert result.transcript_segments == [{"start": 0.0, "end": 3.0, "text": "hello world"}]
    assert result.transcript_words == [
        {"start": 0.0, "end": 1.4, "word": "hello"},
        {"start": 1.4, "end": 3.0, "word": "world"},
    ]
    assert result.duration_s == 3.0
    assert result.transcription_agent_run_id is not None


def test_transcribe_source_video_marks_failed_on_error(db_session):
    class _BoomProvider:
        def transcribe(self, audio_path):
            raise RuntimeError("provider down")

    source_video = clip_service.register_source_video(
        db_session, campaign_id=None, title="My Video", storage_path="/tmp/x.mp4"
    )
    with pytest.raises(RuntimeError):
        clip_service.transcribe_source_video(db_session, source_video=source_video, transcription_provider=_BoomProvider())
    assert source_video.transcription_status == ProcessingStatus.FAILED


def test_transcribe_source_video_sends_a_compact_extracted_audio_file_not_the_raw_video(db_session, tmp_path):
    """Regression test for a real production risk found via code audit:
    the raw source video file was always sent directly to the
    transcription provider - fine for a small test clip, but a genuinely
    long real recording can be several GB, almost certainly over any
    hosted Whisper-class API's real request-size limit. Proves the
    provider actually receives a *different*, real, audio-only file - not
    just that the pipeline still works when it happens to receive the raw
    video path."""
    pytest.importorskip("imageio_ffmpeg")
    import subprocess

    import imageio_ffmpeg

    real_video_path = tmp_path / "real_source.mp4"
    ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
    subprocess.run(
        [
            ffmpeg_bin, "-y",
            "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
            "-t", "3", "-pix_fmt", "yuv420p", str(real_video_path),
        ],
        check=True, capture_output=True,
    )

    received_paths = []

    class _SpyTranscriptionProvider:
        def transcribe(self, audio_path: str) -> TranscriptionResult:
            received_paths.append(audio_path)
            return TranscriptionResult(text="ok", provider="fake", model="fake-model")

    source_video = clip_service.register_source_video(
        db_session, campaign_id=None, title="My Video", storage_path=str(real_video_path)
    )
    clip_service.transcribe_source_video(
        db_session, source_video=source_video, transcription_provider=_SpyTranscriptionProvider()
    )

    assert len(received_paths) == 1
    received_path = received_paths[0]
    assert received_path != str(real_video_path)
    assert received_path.endswith(".m4a")
    # The temp extracted file must be cleaned up once transcription completes.
    assert not Path(received_path).exists()


def test_transcribe_source_video_stores_diarization_result_when_provider_given(db_session):
    source_video = clip_service.register_source_video(
        db_session, campaign_id=None, title="My Video", storage_path="/tmp/x.mp4"
    )
    transcription_provider = _FakeTranscriptionProvider(TranscriptionResult(text="hi", provider="fake", model="fake"))
    diarization_provider = _FakeDiarizationProvider(
        DiarizationResult(
            turns=[
                SpeakerTurn(start_s=0.0, end_s=2.0, speaker_label="SPEAKER_00"),
                SpeakerTurn(start_s=2.0, end_s=4.0, speaker_label="SPEAKER_01"),
            ],
            speaker_count=2,
            provider="pyannote",
        )
    )

    result = clip_service.transcribe_source_video(
        db_session,
        source_video=source_video,
        transcription_provider=transcription_provider,
        diarization_provider=diarization_provider,
    )

    assert result.speaker_turns == [
        {"start": 0.0, "end": 2.0, "speaker": "SPEAKER_00"},
        {"start": 2.0, "end": 4.0, "speaker": "SPEAKER_01"},
    ]


def test_transcribe_source_video_survives_a_diarization_failure_without_losing_transcription(db_session):
    """Diarization is optional and best-effort - a real diarization
    provider (pyannote) can fail in ways transcription never does (model
    load OOM, missing weights). That failure must never be reported as a
    transcription failure, and the already-succeeded transcription must
    survive it intact."""
    source_video = clip_service.register_source_video(
        db_session, campaign_id=None, title="My Video", storage_path="/tmp/x.mp4"
    )
    transcription_provider = _FakeTranscriptionProvider(
        TranscriptionResult(text="hello world", provider="fake", model="fake")
    )

    result = clip_service.transcribe_source_video(
        db_session,
        source_video=source_video,
        transcription_provider=transcription_provider,
        diarization_provider=_BoomDiarizationProvider(),
    )

    assert result.transcription_status == ProcessingStatus.COMPLETED
    assert result.transcript_text == "hello world"
    assert result.speaker_turns is None
    # The session itself must still be usable afterward - proves the
    # diarization failure didn't leave the transaction in a poisoned state.
    db_session.flush()


def test_analyze_source_video_creates_clips_from_transcript(db_session):
    source_video = clip_service.register_source_video(
        db_session, campaign_id=None, title="My Video", storage_path="/tmp/x.mp4"
    )
    source_video.transcript_segments = [{"start": 0.0, "end": 10.0, "text": "an interesting moment happens here"}]
    db_session.flush()

    canned = [{"start_s": 1.0, "end_s": 8.0, "hook_text": "hook", "predicted_score": 0.8, "reason": "why"}]
    llm = FakeLLMClient(response_builder=lambda system, prompt: json.dumps(canned))

    clips = clip_service.analyze_source_video(db_session, source_video=source_video, llm_client=llm, max_clips=5)

    assert len(clips) == 1
    assert source_video.analysis_status == ProcessingStatus.COMPLETED
    assert source_video.analysis_agent_run_id is not None


def test_analyze_source_video_feeds_real_get_top_hooks_into_the_prompt_not_a_static_list(db_session):
    """Regression test for a real gap found via code audit: get_top_hooks
    (the retrieval that surfaces which real hooks/frameworks have actually
    earned a viral score for this niche) was called for the Script
    pipeline (api/routers/content.py) but never for ClipSelectionAgent -
    every clip-selection LLM call only ever saw the static HOOK_FRAMEWORKS
    menu, never a real, niche-specific example. This proves the prompt is
    built from the real HookLibrary retrieval, not a hardcoded string."""
    niche = _make_niche(db_session)
    content_intelligence.record_hook_outcome(
        db_session, niche_id=niche.id, hook_text="The one mistake that's costing you followers",
        viral_score=0.95,
    )

    source_video = clip_service.register_source_video(
        db_session, campaign_id=None, title="My Video", storage_path="/tmp/x.mp4"
    )
    source_video.transcript_segments = [{"start": 0.0, "end": 10.0, "text": "an interesting moment happens here"}]
    db_session.flush()

    captured_prompts = []

    def _capture(system, prompt):
        captured_prompts.append(prompt)
        return json.dumps([{"start_s": 1.0, "end_s": 8.0, "hook_text": "hook", "predicted_score": 0.8, "reason": "why"}])

    llm = FakeLLMClient(response_builder=_capture)

    clip_service.analyze_source_video(
        db_session, source_video=source_video, llm_client=llm, max_clips=5, niche_id=niche.id
    )

    assert len(captured_prompts) == 1
    assert "Highest-performing hooks previously observed for this niche:" in captured_prompts[0]
    assert "The one mistake that's costing you followers" in captured_prompts[0]


def test_analyze_source_video_prompt_falls_back_cleanly_when_the_niche_has_no_hook_data_yet(db_session):
    niche = _make_niche(db_session)
    source_video = clip_service.register_source_video(
        db_session, campaign_id=None, title="My Video", storage_path="/tmp/x.mp4"
    )
    source_video.transcript_segments = [{"start": 0.0, "end": 10.0, "text": "an interesting moment happens here"}]
    db_session.flush()

    captured_prompts = []

    def _capture(system, prompt):
        captured_prompts.append(prompt)
        return json.dumps([{"start_s": 1.0, "end_s": 8.0, "hook_text": "hook", "predicted_score": 0.8, "reason": "why"}])

    llm = FakeLLMClient(response_builder=_capture)

    clips = clip_service.analyze_source_video(
        db_session, source_video=source_video, llm_client=llm, max_clips=5, niche_id=niche.id
    )

    assert "(no prior hook data yet)" in captured_prompts[0]
    # The rest of clip selection must work identically either way - hook
    # retrieval is additive context, never a gate on the pipeline itself.
    assert len(clips) == 1
    assert source_video.analysis_status == ProcessingStatus.COMPLETED


def test_analyze_source_video_calls_get_top_hooks_exactly_once_regardless_of_data(db_session, monkeypatch):
    """No unnecessary extra query: whether or not the niche has any prior
    hook data, get_top_hooks is called exactly once - no pre-check query
    added on top of it."""
    niche = _make_niche(db_session)
    source_video = clip_service.register_source_video(
        db_session, campaign_id=None, title="My Video", storage_path="/tmp/x.mp4"
    )
    source_video.transcript_segments = [{"start": 0.0, "end": 10.0, "text": "an interesting moment happens here"}]
    db_session.flush()

    call_count = 0
    real_get_top_hooks = content_intelligence.get_top_hooks

    def _counting_get_top_hooks(db, **kwargs):
        nonlocal call_count
        call_count += 1
        return real_get_top_hooks(db, **kwargs)

    monkeypatch.setattr(clip_service.content_intelligence, "get_top_hooks", _counting_get_top_hooks)

    canned = [{"start_s": 1.0, "end_s": 8.0, "hook_text": "hook", "predicted_score": 0.8, "reason": "why"}]
    llm = FakeLLMClient(response_builder=lambda system, prompt: json.dumps(canned))

    clip_service.analyze_source_video(
        db_session, source_video=source_video, llm_client=llm, max_clips=5, niche_id=niche.id
    )

    assert call_count == 1


def test_render_clip_creates_video_row_reusing_existing_pipeline_fields(db_session, tmp_media_dir):
    source_video = clip_service.register_source_video(
        db_session, campaign_id=None, title="My Video", storage_path="/tmp/x.mp4"
    )
    source_video.transcript_segments = [{"start": 0.0, "end": 10.0, "text": "hello"}]
    db_session.flush()

    from content_factory.db.models.clip import Clip

    clip = Clip(source_video_id=source_video.id, start_s=1.0, end_s=6.0, hook_text="hook", status=ClipStatus.SUGGESTED)
    db_session.add(clip)
    db_session.flush()

    renderer = NullClipRenderer(storage_dir=tmp_media_dir / "clips")
    video = clip_service.render_clip(db_session, clip=clip, source_video=source_video, clip_renderer=renderer)

    assert video.clip_id == clip.id
    assert video.script_id is None
    assert video.status == VideoStatus.PENDING_REVIEW
    assert video.render_status == ProcessingStatus.COMPLETED
    assert video.contains_ai_voice is False
    assert video.contains_ai_visual is False
    assert video.qc_status == "passed"
    assert clip.status == ClipStatus.RENDERED


def test_render_clip_refuses_to_render_an_already_rendered_clip_again(db_session, tmp_media_dir):
    """Real bug found via a live end-to-end run: the renderer names its
    output file from clip.id alone (clip_{id}.mp4), not video.id - calling
    render_clip twice for the same clip (a UI double-click, or a retry
    that didn't reuse the original idempotency key) used to silently
    create a second Video row whose asset_url pointed at the exact same
    path the first Video row already claims, and the second render would
    overwrite that file in place - even after the first Video had already
    been reviewed or published."""
    from content_factory.db.models.clip import Clip
    from content_factory.services.clip_service import ClipAlreadyRendered

    source_video = clip_service.register_source_video(
        db_session, campaign_id=None, title="My Video", storage_path="/tmp/x.mp4"
    )
    source_video.transcript_segments = [{"start": 0.0, "end": 10.0, "text": "hello"}]
    db_session.flush()

    clip = Clip(source_video_id=source_video.id, start_s=1.0, end_s=6.0, hook_text="hook", status=ClipStatus.SUGGESTED)
    db_session.add(clip)
    db_session.flush()

    renderer = NullClipRenderer(storage_dir=tmp_media_dir / "clips")
    first_video = clip_service.render_clip(db_session, clip=clip, source_video=source_video, clip_renderer=renderer)

    with pytest.raises(ClipAlreadyRendered):
        clip_service.render_clip(db_session, clip=clip, source_video=source_video, clip_renderer=renderer)

    # The first video's row must be completely unaffected by the refused
    # second attempt.
    db_session.refresh(first_video)
    assert first_video.render_status == ProcessingStatus.COMPLETED


def test_render_clip_replaces_asset_url_with_public_url_when_backup_provides_one(db_session, tmp_media_dir):
    """This is what actually closes the profit loop's storage blocker for
    the Clip Factory pipeline specifically: Video.asset_url must become
    the real public URL, since publishing_service.py reads that field
    directly and a platform can never reach a local filesystem path."""
    from content_factory.db.models.clip import Clip
    from content_factory.services.media_backup import MediaBackupProvider, MediaBackupResult

    source_video = clip_service.register_source_video(
        db_session, campaign_id=None, title="My Video", storage_path="/tmp/x.mp4"
    )
    source_video.transcript_segments = [{"start": 0.0, "end": 10.0, "text": "hello"}]
    db_session.flush()

    clip = Clip(source_video_id=source_video.id, start_s=1.0, end_s=6.0, hook_text="hook", status=ClipStatus.SUGGESTED)
    db_session.add(clip)
    db_session.flush()

    class _PubliclyHostedBackupProvider(MediaBackupProvider):
        def backup(self, local_path: str) -> MediaBackupResult:
            return MediaBackupResult(
                backed_up=True, location=f"s3://bucket/{local_path}", public_url=f"https://cdn.test/{local_path}"
            )

    renderer = NullClipRenderer(storage_dir=tmp_media_dir / "clips")
    video = clip_service.render_clip(
        db_session,
        clip=clip,
        source_video=source_video,
        clip_renderer=renderer,
        media_backup_provider=_PubliclyHostedBackupProvider(),
    )

    assert video.asset_url.startswith("https://cdn.test/")


def test_render_clip_passes_transcript_words_through_to_the_renderer(db_session, tmp_media_dir):
    source_video = clip_service.register_source_video(
        db_session, campaign_id=None, title="My Video", storage_path="/tmp/x.mp4"
    )
    source_video.transcript_segments = [{"start": 0.0, "end": 10.0, "text": "hello world"}]
    source_video.transcript_words = [
        {"start": 0.0, "end": 1.0, "word": "hello"},
        {"start": 1.0, "end": 2.0, "word": "world"},
    ]
    db_session.flush()

    from content_factory.db.models.clip import Clip

    clip = Clip(source_video_id=source_video.id, start_s=0.0, end_s=5.0, hook_text="hook", status=ClipStatus.SUGGESTED)
    db_session.add(clip)
    db_session.flush()

    captured = {}

    class _CapturingRenderer:
        def render(self, request):
            captured["request"] = request
            return ClipRenderResult(asset_url=str(tmp_media_dir / "clips" / "x.mp4"), duration_s=5.0, provider="fake")

    clip_service.render_clip(db_session, clip=clip, source_video=source_video, clip_renderer=_CapturingRenderer())

    assert captured["request"].transcript_words == [
        TranscriptWord(start_s=0.0, end_s=1.0, word="hello"),
        TranscriptWord(start_s=1.0, end_s=2.0, word="world"),
    ]


def test_render_clip_trims_real_leading_silence_before_rendering(db_session, tmp_media_dir):
    """End-to-end (real ffmpeg silence detection, not a mock): a clip
    selected to start right at the top of a source video that actually
    opens with a second of silence should be rendered from the real
    speech onset instead, not from the literal requested start_s."""
    imageio_ffmpeg = pytest.importorskip("imageio_ffmpeg")
    import subprocess

    tmp_media_dir.mkdir(parents=True, exist_ok=True)
    source_path = tmp_media_dir / "silence_source.mp4"
    ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
    subprocess.run(
        [
            ffmpeg_bin, "-y",
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono:d=1",
            "-f", "lavfi", "-i", "sine=frequency=440:d=2",
            "-f", "lavfi", "-i", "color=c=blue:size=320x240:rate=10:duration=3",
            "-filter_complex", "[0:a][1:a]concat=n=2:v=0:a=1[a]",
            "-map", "[a]", "-map", "2:v", "-shortest", str(source_path),
        ],
        check=True, capture_output=True,
    )

    source_video = clip_service.register_source_video(
        db_session, campaign_id=None, title="Silence Source", storage_path=str(source_path)
    )
    db_session.flush()

    from content_factory.db.models.clip import Clip

    clip = Clip(source_video_id=source_video.id, start_s=0.0, end_s=3.0, hook_text=None, status=ClipStatus.SUGGESTED)
    db_session.add(clip)
    db_session.flush()

    captured = {}

    class _CapturingRenderer:
        def render(self, request):
            captured["request"] = request
            return ClipRenderResult(asset_url=str(tmp_media_dir / "clips" / "x.mp4"), duration_s=2.0, provider="fake")

    clip_service.render_clip(db_session, clip=clip, source_video=source_video, clip_renderer=_CapturingRenderer())

    assert captured["request"].start_s == pytest.approx(1.0, abs=0.1)
    # clip.start_s (the LLM's own selection) stays untouched - only the
    # actual render/QC target range is adjusted.
    assert clip.start_s == 0.0


def test_render_clip_marks_failed_on_renderer_error(db_session, tmp_media_dir):
    source_video = clip_service.register_source_video(
        db_session, campaign_id=None, title="My Video", storage_path="/tmp/x.mp4"
    )
    from content_factory.db.models.clip import Clip

    clip = Clip(source_video_id=source_video.id, start_s=1.0, end_s=6.0, status=ClipStatus.SUGGESTED)
    db_session.add(clip)
    db_session.flush()

    class _BoomRenderer:
        def render(self, request):
            raise RuntimeError("renderer exploded")

    with pytest.raises(RuntimeError):
        clip_service.render_clip(db_session, clip=clip, source_video=source_video, clip_renderer=_BoomRenderer())

    from content_factory.db.models.video import Video

    video = db_session.query(Video).filter(Video.clip_id == clip.id).one()
    assert video.render_status == ProcessingStatus.FAILED
    assert video.status == VideoStatus.RENDER_FAILED


def test_render_clip_flags_qc_failed_when_duration_mismatches(db_session, tmp_media_dir):
    """A renderer that lies about its own output duration must be caught,
    not silently trusted - same spirit as qc_service's existing duration
    check for the Script pipeline."""
    source_video = clip_service.register_source_video(
        db_session, campaign_id=None, title="My Video", storage_path="/tmp/x.mp4"
    )
    from content_factory.db.models.clip import Clip

    clip = Clip(source_video_id=source_video.id, start_s=0.0, end_s=10.0, status=ClipStatus.SUGGESTED)
    db_session.add(clip)
    db_session.flush()

    class _LyingRenderer:
        def render(self, request):
            return ClipRenderResult(asset_url=str(tmp_media_dir / "clip.mp4"), duration_s=1.0, provider="lying")

    (tmp_media_dir).mkdir(parents=True, exist_ok=True)
    (tmp_media_dir / "clip.mp4").write_bytes(b"fake")

    video = clip_service.render_clip(db_session, clip=clip, source_video=source_video, clip_renderer=_LyingRenderer())

    assert video.qc_status == "failed"
    assert "differs from the requested" in video.qc_notes
