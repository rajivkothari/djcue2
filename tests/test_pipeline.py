"""Tests for the shared per-track planner and the intro offset."""

import pytest

from autocue.codec import encode_beat_data, encode_quick_cues, decode_quick_cues, CUE_POSITION_EMPTY
from autocue.pipeline import plan_track, apply_cues_to_blob, existing_cues
from autocue.templates import load_template, with_intro, phrase_template

SR = 44100.0
BPM = 128.0
SPB = SR * 60 / BPM
DUR = 240.0


def _beat_blob(start_s=1.0):
    n = int((SR * DUR - start_s * SR) // SPB)
    return encode_beat_data({
        "sample_rate": SR, "total_samples": SR * DUR, "is_beatgrid_set": True,
        "default_markers": [
            {"sample_offset": start_s * SR, "beat_number": 0, "number_of_beats": n, "unknown_value_1": 0},
            {"sample_offset": start_s * SR + n * SPB, "beat_number": n, "number_of_beats": 0, "unknown_value_1": 0}],
        "adjusted_markers": [], "extra_data": b""})


def _cues_blob(main=0.0, active=()):
    cues = []
    for i in range(8):
        if i in dict(active):
            cues.append({"index": i, "label": f"old{i+1}", "position_samples": dict(active)[i],
                         "color_a": 255, "color_r": 0xEA, "color_g": 0xC5, "color_b": 0x32})
        else:
            cues.append({"index": i, "label": "", "position_samples": CUE_POSITION_EMPTY,
                         "color_a": 0, "color_r": 0, "color_g": 0, "color_b": 0})
    return encode_quick_cues({"cues": cues, "adjusted_main_cue": main,
                              "is_main_cue_adjusted": main > 0, "default_main_cue": 0.0,
                              "extra_data": b""})


def _track(**kw):
    t = {"id": 1, "title": "T", "artist": "A", "path": "Music/t.mp3", "bpm": BPM,
         "beat_data_blob": _beat_blob(), "quick_cues_blob": _cues_blob(main=5.0 * SR),
         "track_data_blob": None}
    t.update(kw)
    return t


def _plan(track=None, template="phrase-16-4", **kw):
    return plan_track(track or _track(), load_template(template), sample_rate=SR, **kw)


class TestPlanTrack:
    def test_ok_plan_from_main_cue(self):
        p = _plan()
        assert p["status"] == "ok", p["reason"]
        assert p["anchor"]["source"] == "main cue"
        assert p["grid"]["source"] == "engine"
        assert [c["slot"] for c in p["proposed"]] == [1, 2, 3, 4]
        assert p["proposed"][0]["time_seconds"] == pytest.approx(5.0)
        assert p["proposed"][1]["time_seconds"] == pytest.approx(5.0 + 64 * 60 / BPM)
        assert p["needs_review"] is False and p["flags"] == []

    def test_skips_existing_unless_overwrite(self):
        t = _track(quick_cues_blob=_cues_blob(main=5 * SR, active=[(0, 5 * SR)]))
        p = _plan(t)
        assert p["status"] == "skip" and "existing" in p["reason"]
        assert p["existing"][0]["slot"] == 1 and p["existing"][0]["label"] == "old1"
        assert _plan(t, overwrite=True)["status"] == "ok"

    def test_duration_limits(self):
        assert _plan(max_duration=100)["status"] == "skip"
        assert "exceeds" in _plan(max_duration=100)["reason"]
        assert _plan(min_duration=1000)["status"] == "skip"

    def test_no_blob_and_no_path(self):
        assert "quickCues" in _plan(_track(quick_cues_blob=None))["reason"]
        assert _plan(_track(path=None))["status"] == "error"

    def test_grid_only_anchor_is_flagged(self):
        t = _track(quick_cues_blob=_cues_blob(main=0.0))       # no main cue
        p = _plan(t, ai_detector=lambda: None)
        assert p["status"] == "ok"
        assert p["anchor"]["source"] == "grid"
        assert any("grid only" in f for f in p["flags"]) and p["needs_review"]

    def test_cues_past_end_flagged(self):
        p = _plan(template="phrase-32-8")                     # 8 cues x 32 bars > 240 s
        assert p["status"] == "ok"
        assert len(p["proposed"]) < 8
        assert any("past the end" in f for f in p["flags"])

    def test_auto_mode_never_runs_ai_when_main_cue_exists(self):
        calls = []
        p = _plan(ai_detector=lambda: calls.append(1) or 5.0)
        assert p["anchor"]["source"] == "main cue" and calls == []

    def test_disagreement_between_main_cue_and_ai_flagged(self):
        p = _plan(anchor_mode="ai", ai_detector=lambda: 5.0 + 2 * 60 / BPM)
        assert p["anchor"]["source"] == "ai"
        assert any("disagree by" in f for f in p["flags"])
        assert p["needs_review"]

    def test_no_grid_in_engine_mode_is_skip(self):
        p = _plan(_track(beat_data_blob=None), grid_mode="engine")
        assert p["status"] == "skip" and "grid" in p["reason"]

    def test_injected_grid_is_used(self):
        g = {"source": "custom", "beats": [i * SPB for i in range(600)],
             "downbeats": [i * SPB for i in range(0, 600, 4)], "samples_per_beat": SPB,
             "tempo_bpm": BPM, "total_samples": SR * DUR, "first_downbeat": 0.0, "note": ""}
        p = _plan(_track(beat_data_blob=None), grid=g)
        assert p["status"] == "ok" and p["grid"]["source"] == "custom"

    def test_analysis_template_without_analyzer_is_error(self):
        p = _plan(template="edm")                              # bar-based: fine
        assert p["status"] == "ok"
        t = {"cues": {1: {"detect": "first_chorus", "label": "x", "color": "red"}}}
        p = plan_track(_track(), t, sample_rate=SR)
        assert p["status"] == "error" and "analysis" in p["reason"]


class TestApplyCues:
    def test_writes_and_clears(self):
        cd = decode_quick_cues(_cues_blob(active=[(4, 99.0)]))
        cd = apply_cues_to_blob(cd, [{"slot": 1, "label": "In", "color_name": "teal",
                                      "time_seconds": 2.0}], SR, clear_missing=True)
        assert cd["cues"][0]["label"] == "In"
        assert cd["cues"][0]["position_samples"] == pytest.approx(2.0 * SR)
        assert cd["cues"][0]["color_r"] == 0x20
        assert cd["cues"][4]["position_samples"] == CUE_POSITION_EMPTY

    def test_keeps_others_without_clear(self):
        cd = decode_quick_cues(_cues_blob(active=[(4, 99.0)]))
        cd = apply_cues_to_blob(cd, [{"slot": 1, "position_samples": 10.0}], SR)
        assert cd["cues"][4]["position_samples"] == 99.0


class TestIntroOffset:
    def test_phrase_intro(self):
        t = phrase_template(16, 4, intro_bars=8)
        assert [t["cues"][k]["detect"] for k in range(1, 5)] == [
            "bar_1", "bar_9", "bar_25", "bar_41"]
        assert "8-bar intro" in t["name"]

    def test_with_intro_rebuilds_phrase_template(self):
        t = with_intro(load_template("phrase-16-3"), 4)
        assert [c["detect"] for c in t["cues"].values()] == ["bar_1", "bar_5", "bar_21"]

    def test_with_intro_shifts_named_template_except_cue_1(self):
        t = with_intro(load_template("edm"), 8)
        assert t["cues"][1]["detect"] == "bar_1"
        assert t["cues"][2]["detect"] == "bar_25"              # 17 + 8
        assert t["cues"][6]["detect"] == "bar_89"

    def test_zero_is_identity(self):
        t = load_template("edm")
        assert with_intro(t, 0) is t

    def test_plan_uses_intro(self):
        p = plan_track(_track(), with_intro(load_template("phrase-16-3"), 8), sample_rate=SR)
        spb = 60 / BPM
        assert [c["time_seconds"] for c in p["proposed"]] == pytest.approx(
            [5.0, 5.0 + 32 * spb, 5.0 + 96 * spb], abs=1e-6)
