"""Tests for bar-1 anchor selection and bar position resolution."""

import pytest

from autocue.anchor import pick_anchor, resolve_bar_position, ANCHOR_MODES

SR = 44100.0
SPB = SR * 60 / 128          # 128 BPM
MAIN = 5.0 * SR              # main cue at 5.000s


def _grid(phase_samples=0.0, n=400):
    """Grid beats spaced SPB apart, starting at phase_samples."""
    return [phase_samples + i * SPB for i in range(n)]


class TestResolveBarPosition:
    def test_non_bar_key_returns_none(self):
        assert resolve_bar_position("mix_in", MAIN, SPB, _grid()) is None

    def test_missing_anchor_unresolved(self):
        assert resolve_bar_position("bar_9", None, SPB, _grid()) == (None, 0.0)

    def test_bar_math_from_anchor(self):
        pos, conf = resolve_bar_position("bar_9", MAIN, SPB, _grid(MAIN))
        assert pos == pytest.approx(MAIN + 32 * SPB)
        assert conf == 1.0

    def test_phase_shifted_grid_does_not_pull_anchor(self):
        # Grid is offset ~2/3 beat from the main cue: must honour the anchor.
        pos, _ = resolve_bar_position("bar_1", MAIN, SPB, _grid(0.0))
        assert pos == pytest.approx(MAIN)

    def test_micro_drift_snaps_to_grid(self):
        drift = SPB * 0.1
        pos, _ = resolve_bar_position("bar_1", MAIN, SPB, _grid(MAIN + drift))
        assert pos == pytest.approx(MAIN + drift)

    def test_beat_offset_shifts_all_bars(self):
        base, _ = resolve_bar_position("bar_9", MAIN, SPB, [])
        shifted, _ = resolve_bar_position("bar_9", MAIN, SPB, [], beat_offset=-1)
        assert shifted == pytest.approx(base - SPB)


class TestPickAnchor:
    def _call(self, mode, main_cue=MAIN, ai_secs=5.0, ai_raises=None,
              beats=None, downbeats=None):
        calls = {"n": 0}

        def detect():
            calls["n"] += 1
            if ai_raises:
                raise ai_raises
            return ai_secs

        beats = _grid(MAIN) if beats is None else beats
        downbeats = beats[::4] if downbeats is None else downbeats
        res = pick_anchor(mode, main_cue=main_cue, downbeats=downbeats,
                          beats=beats, samples_per_beat=SPB, sample_rate=SR,
                          detect_first_downbeat=detect)
        return res, calls["n"]

    def test_unknown_mode_raises(self):
        with pytest.raises(ValueError, match="Unknown anchor mode"):
            pick_anchor("magic", main_cue=None, downbeats=[], beats=[],
                        samples_per_beat=SPB, sample_rate=SR)

    def test_auto_prefers_main_cue_and_skips_ai(self):
        res, n = self._call("auto")
        assert res["source"] == "main cue"
        assert res["anchor"] == MAIN
        assert n == 0, "model must not run when a main cue exists"

    def test_auto_uses_ai_when_no_main_cue(self):
        res, n = self._call("auto", main_cue=None, ai_secs=5.01)
        assert n == 1
        assert res["source"] == "ai"
        # 5.01s is jitter; snapped onto the nearest grid beat at 5.000s
        assert res["anchor"] == pytest.approx(MAIN)

    def test_auto_falls_to_grid_when_ai_finds_nothing(self):
        res, _ = self._call("auto", main_cue=None, ai_secs=None)
        assert res["source"] == "grid"
        assert res["anchor"] == MAIN  # grid first downbeat

    def test_ai_failure_does_not_abort(self):
        res, _ = self._call("auto", main_cue=None,
                            ai_raises=ImportError("no torch"))
        assert res["source"] == "grid"
        assert "AI unavailable" in res["note"]

    def test_ai_mode_forces_model_and_reports_disagreement(self):
        # AI says bar 1 is one full beat after the main cue.
        res, n = self._call("ai", ai_secs=5.0 + SPB / SR)
        assert n == 1
        assert res["source"] == "ai"
        assert res["anchor"] == pytest.approx(MAIN + SPB)
        assert "disagree by 1.0 beats" in res["note"]

    def test_ai_mode_agreement_has_no_note(self):
        res, _ = self._call("ai", ai_secs=5.0)
        assert res["note"] == ""

    def test_main_cue_mode_falls_back_with_note(self):
        res, n = self._call("main-cue", main_cue=None)
        assert n == 0
        assert res["source"] == "grid"
        assert "fell back to grid" in res["note"]

    def test_grid_mode_ignores_everything_else(self):
        res, n = self._call("grid", ai_secs=9.0)
        assert n == 0
        assert res["source"] == "grid"
        assert res["candidates"]["main_cue"] == MAIN  # still reported

    def test_no_grid_uses_raw_ai_time(self):
        res, _ = self._call("ai", beats=[], downbeats=[], ai_secs=7.0)
        assert res["anchor"] == pytest.approx(7.0 * SR)

    def test_nothing_available(self):
        res, _ = self._call("auto", main_cue=None, ai_secs=None,
                            beats=[], downbeats=[])
        assert res["anchor"] is None
        assert res["source"] == "none"

    def test_all_modes_accepted(self):
        for mode in ANCHOR_MODES:
            self._call(mode)
