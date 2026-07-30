"""pyannote.audio is a heavy, PyTorch-based optional dependency (see
pyannote_provider.py's own docstring for why it's excluded from this
project's default installs) - genuinely not installed in this test
environment, same as this codebase's other "not installed by default"
extras. This test exercises the real ImportError path (not a mock),
matching the F13-audit-driven convention of at least covering a real
provider's request/response handling without ever needing live
credentials or a live model download."""

import pytest

from content_factory.diarization.providers.pyannote_provider import PyannoteDiarizationProvider


def test_raises_a_clear_runtime_error_when_the_extra_is_not_installed():
    provider = PyannoteDiarizationProvider(hf_token="hf_test_token")

    with pytest.raises(RuntimeError, match="diarization"):
        provider.diarize("/tmp/does-not-matter.mp4")
