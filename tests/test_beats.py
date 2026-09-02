"""Tests for the pure (model-free) parts of AI beat detection."""

import pytest

np = pytest.importorskip("numpy")

from autocue.beats import audio_start_seconds, first_downbeat_after  # noqa: E402


class TestAudioStart:
    def test_leading_silence_is_skipped(self):
        sr = 1000
        y = np.zeros(5000, dtype=np.float32)
        y[2000:] = 0.5                      # sound starts at 2.0s
        assert audio_start_seconds(y, sr) == 2.0

    def test_quiet_noise_below_threshold_is_silence(self):
        sr = 1000
        y = np.full(3000, 1e-4, dtype=np.float32)   # ~-80 dBFS hiss
        y[1500:] = 0.3
        assert audio_start_seconds(y, sr) == 1.5

    def test_all_silent_returns_zero(self):
        assert audio_start_seconds(np.zeros(100, dtype=np.float32), 1000) == 0.0


class TestFirstDownbeatAfter:
    def test_drops_downbeat_in_leading_silence(self):
        # Model hallucinated a downbeat one beat before the audio began.
        assert first_downbeat_after([1.54, 2.0, 3.875], audio_start=2.0) == 2.0

    def test_keeps_downbeat_slightly_before_start_within_tolerance(self):
        # 20 ms of model jitter must not push us to the *next* bar.
        assert first_downbeat_after([1.98, 3.855], audio_start=2.0) == 1.98

    def test_first_downbeat_after_intro_pad_is_kept(self):
        # Audio starts (soft pad) at 0.5s; first beat at 4.0s.
        assert first_downbeat_after([4.0, 5.875], audio_start=0.5) == 4.0

    def test_none_when_everything_is_in_silence(self):
        assert first_downbeat_after([0.5, 1.0], audio_start=10.0) is None

    def test_empty(self):
        assert first_downbeat_after([], audio_start=0.0) is None
