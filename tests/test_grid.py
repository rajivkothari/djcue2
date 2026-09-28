"""Tests for AI grid fitting, grid selection and Engine write-back."""

import pytest

np = pytest.importorskip("numpy")

from autocue.beats import fit_grid                                   # noqa: E402
from autocue.grid import resolve_grid, engine_beat_data_from_grid    # noqa: E402
from autocue.codec import (                                          # noqa: E402
    encode_beat_data, decode_beat_data, get_beat_positions,
    get_downbeat_positions, get_samples_per_beat,
)

SR = 44100.0


def _detections(bpm=128.0, start=2.0, n=300, jitter_ms=4.0, seed=1,
                first_downbeat_offset_beats=0):
    """Synthetic Beat This!-style output: jittered beats, downbeats every 4."""
    rng = np.random.default_rng(seed)
    spb = 60 / bpm
    ideal = start + np.arange(n) * spb
    beats = ideal + rng.normal(0, jitter_ms / 1000, n)
    downbeats = [beats[i] for i in range(first_downbeat_offset_beats, n, 4)]
    duration = ideal[-1] + 3.0
    return beats, downbeats, duration


class TestFitGrid:
    def test_recovers_tempo_and_bar1(self):
        beats, dbs, dur = _detections()
        g = fit_grid(beats, dbs, dur, audio_start=1.9)
        assert g["tempo_bpm"] == pytest.approx(128.0, abs=0.05)
        assert g["first_downbeat"] == pytest.approx(2.0, abs=0.005)
        assert g["variable_tempo"] is False
        assert g["rms_residual_ms"] < 8
        assert g["n_detected"] == 300

    def test_frame_quantized_detections_do_not_skew_tempo(self):
        # Beat This! reports times on a 20 ms grid. Over a few minutes the
        # median gap is biased (0.46 vs 0.46875 s at 128 BPM); the fit must
        # still land on the true tempo instead of drifting to ~130 BPM.
        spb = 60 / 128.0
        ideal = 1.0 + np.arange(500) * spb
        beats = np.round(ideal / 0.02) * 0.02
        dbs = list(beats[::4])
        g = fit_grid(beats, dbs, ideal[-1] + 2, audio_start=0.95)
        assert g["tempo_bpm"] == pytest.approx(128.0, abs=0.02)
        assert g["variable_tempo"] is False
        assert g["rms_residual_ms"] < 12
        assert g["first_downbeat"] == pytest.approx(1.0, abs=0.01)

    def test_missed_beats_do_not_break_indexing(self):
        beats, dbs, dur = _detections(n=200)
        beats = np.delete(beats, [30, 31, 90])          # tracker dropped some
        g = fit_grid(beats, dbs, dur)
        assert g["tempo_bpm"] == pytest.approx(128.0, abs=0.05)

    def test_grid_covers_whole_track_and_downbeats_every_4(self):
        beats, dbs, dur = _detections(start=2.0)
        g = fit_grid(beats, dbs, dur)
        spb = g["seconds_per_beat"]
        assert g["beats"][0] >= 0 and g["beats"][0] < spb          # from track start
        assert g["beats"][-1] <= dur
        assert g["first_beat_index"] < 0                             # beats before bar 1
        assert np.allclose(np.diff(g["downbeats"]), 4 * spb)
        assert min(g["downbeats"], key=lambda d: abs(d - 2.0)) == pytest.approx(2.0, abs=0.005)

    def test_ignores_hallucinated_downbeat_in_leading_silence(self):
        beats, dbs, dur = _detections(start=2.0)
        dbs = [2.0 - 60 / 128] + dbs                 # one beat before sound
        g = fit_grid(beats, dbs, dur, audio_start=2.0)
        assert g["first_downbeat"] == pytest.approx(2.0, abs=0.005)

    def test_bpm_hint_fixes_half_tempo(self):
        beats, dbs, dur = _detections(bpm=64.0)      # tracker answered at half
        g = fit_grid(beats, dbs, dur, bpm_hint=128.0)
        assert g["tempo_bpm"] == pytest.approx(128.0, abs=0.1)

    def test_hint_ignored_when_far_off(self):
        beats, dbs, dur = _detections(bpm=100.0)
        g = fit_grid(beats, dbs, dur, bpm_hint=128.0)
        assert g["tempo_bpm"] == pytest.approx(100.0, abs=0.1)

    def test_variable_tempo_flagged(self):
        beats, dbs, dur = _detections(jitter_ms=45.0)
        assert fit_grid(beats, dbs, dur)["variable_tempo"] is True

    def test_no_downbeats_falls_back_to_first_beat(self):
        beats, _, dur = _detections(start=3.0)
        g = fit_grid(beats, [], dur, audio_start=2.95)
        assert g["first_downbeat"] == pytest.approx(3.0, abs=0.01)
        assert g["downbeat_confidence"] < 1.0

    def test_too_few_beats(self):
        assert fit_grid([1, 2, 3], [1], 10.0) is None


def _engine_blob(bpm=128.0, start_s=1.0, duration_s=120.0):
    spb = SR * 60 / bpm
    n = int((SR * duration_s - start_s * SR) // spb)
    return encode_beat_data({
        "sample_rate": SR, "total_samples": SR * duration_s, "is_beatgrid_set": True,
        "default_markers": [
            {"sample_offset": start_s * SR, "beat_number": 0, "number_of_beats": n, "unknown_value_1": 0},
            {"sample_offset": start_s * SR + n * spb, "beat_number": n, "number_of_beats": 0, "unknown_value_1": 0}],
        "adjusted_markers": [], "extra_data": b"\x07\x07"})


def _fake_build(bpm=126.0, first_db=2.5, duration=120.0):
    def build(path, bpm_hint=None):
        spb = 60 / bpm
        import math
        kmin = math.ceil(-first_db / spb); kmax = math.floor((duration - first_db) / spb)
        beats = [first_db + k * spb for k in range(kmin, kmax + 1)]
        return {"tempo_bpm": bpm, "seconds_per_beat": spb, "first_downbeat": first_db,
                "first_beat_index": kmin, "beats": beats,
                "downbeats": [first_db + k * spb for k in range(kmin, kmax + 1) if k % 4 == 0],
                "rms_residual_ms": 3.0, "variable_tempo": False, "n_detected": 400,
                "downbeat_confidence": 1.0}
    return build


class TestResolveGrid:
    def test_auto_prefers_engine(self):
        track = {"beat_data_blob": _engine_blob(), "bpm": 128.0}
        g = resolve_grid(track, "auto", "x.mp3", SR, build=_fake_build())
        assert g["source"] == "engine"
        assert g["tempo_bpm"] == pytest.approx(128.0)
        assert g["samples_per_beat"] == pytest.approx(SR * 60 / 128)

    def test_auto_falls_back_to_ai_without_engine_grid(self):
        track = {"beat_data_blob": None, "bpm": None}
        g = resolve_grid(track, "auto", "x.mp3", SR, build=_fake_build())
        assert g["source"] == "ai"
        assert g["tempo_bpm"] == pytest.approx(126.0)
        assert g["first_downbeat"] == pytest.approx(2.5 * SR)
        assert g["beats"][0] >= 0

    def test_ai_mode_ignores_engine(self):
        track = {"beat_data_blob": _engine_blob(), "bpm": 128.0}
        g = resolve_grid(track, "ai", "x.mp3", SR, build=_fake_build())
        assert g["source"] == "ai"
        assert g["total_samples"] == pytest.approx(SR * 120)   # kept from Engine

    def test_engine_mode_returns_none_without_grid(self):
        assert resolve_grid({"beat_data_blob": None}, "engine", "x.mp3", SR) is None

    def test_auto_reports_ai_failure_instead_of_raising(self):
        g = resolve_grid({"beat_data_blob": None}, "auto", None, SR, build=_fake_build())
        assert g["source"] == "none" and "AI grid unavailable" in g["note"]

    def test_ai_mode_raises_on_failure(self):
        with pytest.raises(FileNotFoundError):
            resolve_grid({"beat_data_blob": None}, "ai", None, SR, build=_fake_build())

    def test_unknown_mode(self):
        with pytest.raises(ValueError):
            resolve_grid({}, "magic", None, SR)


class TestEngineWriteBack:
    def test_roundtrip_through_codec_matches_grid(self):
        track = {"beat_data_blob": _engine_blob(), "bpm": 128.0}
        ai = resolve_grid(track, "ai", "x.mp3", SR, build=_fake_build(bpm=126.0, first_db=2.5))
        bd = engine_beat_data_from_grid(ai, existing_blob=track["beat_data_blob"])
        blob = encode_beat_data(bd)
        back = decode_beat_data(blob)

        assert back["is_beatgrid_set"] is True
        assert back["extra_data"] == b"\x07\x07"                      # preserved
        assert get_samples_per_beat(back) == pytest.approx(SR * 60 / 126, rel=1e-9)
        beats = get_beat_positions(back)
        assert beats[0] >= 0 and beats[0] < SR * 60 / 126               # covers intro
        assert beats[-1] <= SR * 120
        # bar 1 is a downbeat in Engine's numbering (beat 0 % 4 == 0)
        dbs = get_downbeat_positions(back)
        assert min(dbs, key=lambda d: abs(d - 2.5 * SR)) == pytest.approx(2.5 * SR, abs=1e-6)
        assert back["default_markers"][0]["beat_number"] < 0
        assert back["default_markers"] == back["adjusted_markers"]

    def test_refuses_engine_grid(self):
        eng = resolve_grid({"beat_data_blob": _engine_blob()}, "engine", None, SR)
        with pytest.raises(ValueError):
            engine_beat_data_from_grid(eng)
