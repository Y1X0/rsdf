"""Real, end-to-end verification of the single mandated journey: a
long-form video -> transcribe -> AI-selects best moments -> real ffmpeg cut
-> hook overlay -> burned captions -> 9:16 Reel/Short output -> human
review -> automatic publish cascade.

This is deliberately NOT a mocked-happy-path test: the source video is a
genuinely ffmpeg-encoded 70s file (real scene cut at 40s, real audio bursts
separated by real silence, built the same way scene_detection.py's and
ffmpeg_clip_renderer.py's own docstrings describe verifying their logic),
and the actual FfmpegClipRenderer (real imageio-ffmpeg binary, real
subprocess calls, real subtitle/hook burn-in, real 9:16 scale+pad, real
loudness normalization) renders both clips for real - nothing about the
cutting/captioning/hook/aspect-ratio stages is faked.

Only the two calls this sandbox's network policy cannot reach at all
(Groq's transcription API, any real LLM's completion API) are faked at
their own service-layer boundary - GroqWhisperProvider/GroqLLMClient's own
HTTP parsing and retry logic are already covered by
test_groq_whisper_provider.py/test_groq_provider.py; what has never been
exercised before is whether clip_service's actual orchestration (silence
trim -> real render -> QC -> DB state -> idempotency -> review -> auto
publish cascade) holds together for a clip-factory-produced Video, as
opposed to the script-pipeline Videos every existing publish-cascade test
uses."""

import subprocess

import pytest

from content_factory.llm.base import LLMResponse
from content_factory.transcription.base import TranscriptionResult, TranscriptSegment, TranscriptWord


def _build_real_source_video(path) -> None:
    """A real 70s, 1920x1080 16:9 file: a genuine hard scene cut at 40s (two
    visually distinct flat-color segments, exactly like scene_detection.py's
    own docstring verifies), and real audio - tone bursts separated by real
    silence - whose timing exactly matches the fabricated transcript below,
    so every downstream stage (silence trim, word-grouped captions, hook
    overlay, scene-snap) operates on genuinely consistent data."""
    import imageio_ffmpeg

    ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
    video_inputs = [
        "-f", "lavfi", "-i", "color=c=red:s=1920x1080:r=25:d=40",
        "-f", "lavfi", "-i", "color=c=blue:s=1920x1080:r=25:d=30",
    ]
    audio_segments = [
        ("anullsrc=r=44100:cl=mono", 3),
        ("sine=frequency=220:sample_rate=44100", 5),
        ("anullsrc=r=44100:cl=mono", 2),
        ("sine=frequency=330:sample_rate=44100", 6),
        ("anullsrc=r=44100:cl=mono", 2),
        ("sine=frequency=440:sample_rate=44100", 7),
        ("anullsrc=r=44100:cl=mono", 15),
        ("sine=frequency=550:sample_rate=44100", 8),
        ("anullsrc=r=44100:cl=mono", 2),
        ("sine=frequency=660:sample_rate=44100", 8),
        ("anullsrc=r=44100:cl=mono", 12),
    ]
    audio_inputs = []
    for filt, dur in audio_segments:
        audio_inputs += ["-f", "lavfi", "-i", f"{filt}:d={dur}"]
    n_audio = len(audio_segments)
    filter_complex = (
        "[0:v][1:v]concat=n=2:v=1:a=0[vout];"
        + "".join(f"[{i + 2}:a]" for i in range(n_audio))
        + f"concat=n={n_audio}:v=0:a=1[aout]"
    )
    cmd = [
        ffmpeg_bin, "-y",
        *video_inputs, *audio_inputs,
        "-filter_complex", filter_complex,
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
        str(path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def _ffprobe_streams(path) -> str:
    import imageio_ffmpeg

    ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
    result = subprocess.run([ffmpeg_bin, "-i", str(path)], capture_output=True, text=True)
    return result.stderr


# Fabricated "speech" matching the real audio bursts above exactly - segment
# 4 lands after the real 40s scene cut, so the clip selection agent's
# suggested boundary (39.0s, deliberately not exactly on the cut) is
# expected to snap onto the real detected cut at 40.0s.
_SEGMENTS = [
    (3.0, 8.0, "this is the most important tip you will ever hear about saving money"),
    (10.0, 16.0, "nobody tells you this secret because it actually works every single time"),
    (18.0, 25.0, "here is exactly how you can do it starting today without any extra effort"),
    (40.0, 48.0, "after the scene changes we reveal the real method that changes everything"),
    (50.0, 58.0, "try this for one week and watch what happens to your bank account"),
]


def _words_for_segment(start_s: float, end_s: float, text: str) -> list[TranscriptWord]:
    words = text.split()
    step = (end_s - start_s) / len(words)
    out = []
    for i, w in enumerate(words):
        out.append(TranscriptWord(start_s=round(start_s + i * step, 3), end_s=round(start_s + (i + 1) * step, 3), word=w))
    return out


class _FakeGroqWhisper:
    """Stands in only for the literal network hop this sandbox's proxy
    blocks outright (api.groq.com) - GroqWhisperProvider's own HTTP
    parsing/retry code is already covered by
    tests/unit/test_groq_whisper_provider.py."""

    def transcribe(self, audio_path: str) -> TranscriptionResult:
        assert audio_path  # a real path was actually passed through
        segments = [TranscriptSegment(start_s=s, end_s=e, text=t) for s, e, t in _SEGMENTS]
        words: list[TranscriptWord] = []
        for s, e, t in _SEGMENTS:
            words.extend(_words_for_segment(s, e, t))
        return TranscriptionResult(
            text=" ".join(t for _, _, t in _SEGMENTS),
            segments=segments,
            words=words,
            provider="groq",
            model="whisper-large-v3",
            duration_s=70.0,
            cost_usd=0.01,
            duration_ms=500,
        )


class _FakeClipSelectionLLM:
    """Stands in only for the literal network hop this sandbox cannot
    reach for either real LLM option (Groq: blocked by proxy policy;
    Anthropic: blocked by the account's own credit balance, confirmed via
    a real direct call - see docs/PILOT_ENVIRONMENT_STATUS.md). Returns
    exactly the JSON shape ClipSelectionAgent._build_prompt/parses,
    referencing real transcript text, so the parsing/bounds-check/
    scene-snap logic downstream is genuinely exercised."""

    def complete(self, *, system: str, prompt: str, max_tokens: int = 2000) -> LLMResponse:
        assert "this is the most important tip" in prompt  # the real transcript reached the agent
        import json as _json

        candidates = [
            {
                "start_s": 2.0,
                "end_s": 27.0,
                "hook_framework": "curiosity_gap",
                "hook_text": "the one saving money trick nobody tells you",
                "predicted_score": 0.82,
                "reason": "Strong, self-contained tip with a clear payoff.",
            },
            {
                "start_s": 39.0,
                "end_s": 59.0,
                "hook_framework": "bold_claim",
                "hook_text": "this method changes everything about your bank account",
                "predicted_score": 0.76,
                "reason": "Follows the scene change into a concrete claim.",
            },
        ]
        return LLMResponse(
            text=_json.dumps(candidates),
            provider="fake",
            model="fake-model",
            model_version="1",
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.001,
            duration_ms=10,
        )


@pytest.fixture()
def real_source_video_file(tmp_path):
    path = tmp_path / "long_form_source.mp4"
    _build_real_source_video(path)
    return path


def test_full_clip_factory_pipeline_end_to_end_on_a_real_video(client, real_source_video_file, tmp_path):
    from content_factory.api import deps
    from content_factory.api.main import app
    from content_factory.services.media_backup import MediaBackupProvider, MediaBackupResult
    from content_factory.video_clipping.providers.ffmpeg_clip_renderer import FfmpegClipRenderer

    app.dependency_overrides[deps.get_transcription_provider] = lambda: _FakeGroqWhisper()
    app.dependency_overrides[deps.get_llm_client] = lambda: _FakeClipSelectionLLM()
    real_clip_renderer = FfmpegClipRenderer(storage_dir=tmp_path / "clips")
    app.dependency_overrides[deps.get_clip_renderer] = lambda: real_clip_renderer

    try:
        # Stage 1: a real long-form video, uploaded exactly like an operator
        # would through the dashboard's file input.
        with real_source_video_file.open("rb") as f:
            upload_resp = client.post(
                "/source-videos",
                data={"title": "Real long-form test upload"},
                files={"file": ("long_form_source.mp4", f, "video/mp4")},
            )
        assert upload_resp.status_code == 200, upload_resp.text
        source_video_id = upload_resp.json()["id"]

        # Stage 2: transcribe (real orchestration; only the literal Groq
        # network hop is faked).
        transcribe_resp = client.post(f"/source-videos/{source_video_id}/transcribe", json={})
        assert transcribe_resp.status_code == 200, transcribe_resp.text
        transcribed = transcribe_resp.json()
        assert transcribed["transcription_status"] == "completed"
        assert "saving money" in transcribed["transcript_text"]

        # Stage 3: analyze -> AI selects the best moments (real scene
        # detection runs against the real video; only the literal LLM
        # network hop is faked).
        analyze_resp = client.post(f"/source-videos/{source_video_id}/analyze", json={"max_clips": 5})
        assert analyze_resp.status_code == 200, analyze_resp.text
        clips = analyze_resp.json()
        assert len(clips) == 2
        for clip in clips:
            assert clip["hook_text"]
            assert clip["hook_framework"] in ("curiosity_gap", "bold_claim")
            # hook_strength_score is computed synchronously from hook_text
            # (services/hook_scoring.py) - never left null when a hook exists.
            assert clip["hook_strength_score"] is not None

        # The second clip's suggested end_s=59.0 must have snapped onto the
        # real detected scene cut at 40.0 for start_s (39.0 -> 40.0), proving
        # detect_scene_changes()/snap_to_nearest_scene_change() actually ran
        # against the real video rather than being silently skipped.
        second_clip = clips[1]
        assert second_clip["start_s"] == pytest.approx(40.0, abs=0.01)

        # Stage 4-7 (cut, hook, captions, produce Reel/Short): render both
        # clips for real via the actual FfmpegClipRenderer. Public hosting
        # (media_backup) is faked at the same boundary
        # test_pipeline_automation_api.py already uses for this - real
        # Supabase/S3 upload was independently verified in a previous
        # session and isn't what this test re-proves - but it must be
        # active *during* the render calls themselves, since
        # clip_service.render_clip sets Video.asset_url from whatever
        # backup provider was wired in at that exact moment.
        from content_factory.services.media_backup import MediaBackupProvider, MediaBackupResult

        class _FakePubliclyHostedBackupProvider(MediaBackupProvider):
            def backup(self, local_path: str) -> MediaBackupResult:
                return MediaBackupResult(
                    backed_up=True,
                    location=f"s3://test-bucket{local_path}",
                    public_url=f"https://cdn.test.example{local_path}",
                )

        app.dependency_overrides[deps.get_media_backup_provider] = lambda: _FakePubliclyHostedBackupProvider()

        rendered_videos = []
        for clip in clips:
            render_resp = client.post(f"/clips/{clip['id']}/render", json={})
            assert render_resp.status_code == 200, render_resp.text
            rendered_videos.append(render_resp.json())

        for clip, video in zip(clips, rendered_videos):
            assert video["status"] == "pending_review"
            assert video["render_status"] == "completed"
            assert video["qc_status"] == "passed", video["qc_notes"]
            assert video["contains_ai_voice"] is False
            assert video["contains_ai_visual"] is False
            requested_duration = clip["end_s"] - clip["start_s"]
            # Silence-trim may shrink the actual rendered duration relative
            # to the LLM's raw suggestion - QC already accounts for that
            # (compares against the post-trim request), so only a loose
            # sanity bound is asserted here.
            assert video["duration_s"] <= requested_duration + 1.0

            # video["asset_url"] is now the fake *public* URL (media_backup
            # replaces the local path outright, matching production_service's
            # own precedent) - the real rendered file itself lives where
            # FfmpegClipRenderer actually wrote it, named by clip_id.
            assert video["asset_url"] == f"https://cdn.test.example{tmp_path}/clips/clip_{clip['id']}.mp4"
            real_local_path = tmp_path / "clips" / f"clip_{clip['id']}.mp4"
            assert real_local_path.exists()
            streams = _ffprobe_streams(real_local_path)
            # Real 9:16 short-form output - not a manifest, an actual
            # playable file with both a video and an audio stream.
            assert "540x960" in streams
            assert "Video:" in streams
            assert "Audio:" in streams

        # Stage 8 (publish): register one real (credential-less, safe
        # ManualPublishingProvider) account, then confirm a single
        # review-approval click cascades all the way to a Publication row
        # with zero further manual calls - this exact path (a
        # clip-factory-produced Video, not a script-pipeline one) has never
        # been exercised by any existing test.
        account_resp = client.post("/accounts", json={"platform": "tiktok", "handle": "@clip_factory_test"})
        assert account_resp.status_code == 200, account_resp.text
        account_id = account_resp.json()["id"]

        first_video_id = rendered_videos[0]["id"]
        review_resp = client.post(f"/videos/{first_video_id}/review", json={"decision": "approved"})
        assert review_resp.status_code == 200, review_resp.text
        review_body = review_resp.json()
        assert review_body["auto_publish_status"] == "scheduled"
        assert f"account #{account_id}" in review_body["auto_publish_detail"]

        publications = client.get("/publications").json()
        assert any(p["video_id"] == first_video_id for p in publications)
    finally:
        del app.dependency_overrides[deps.get_transcription_provider]
        del app.dependency_overrides[deps.get_llm_client]
        del app.dependency_overrides[deps.get_clip_renderer]
        app.dependency_overrides.pop(deps.get_media_backup_provider, None)
