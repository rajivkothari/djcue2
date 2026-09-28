"""Per-track cue planning shared by the CLI batch and the GUI batch.

plan_track() decides everything about one track without writing anything:
whether to skip it and why, which grid and bar-1 anchor to use, where each
cue lands, and which of those decisions a human should double-check
(`flags` / `needs_review`). Both front ends print or display the same
structure, and apply_cues_to_blob() turns the plan into Engine's blob.
"""

from autocue.anchor import pick_anchor, resolve_bar_position
from autocue.codec import (
    decode_beat_data, decode_quick_cues, is_cue_active, get_main_cue,
    snap_to_downbeat, CUE_POSITION_EMPTY,
)
from autocue.constants import ENGINE_COLORS, ENGINE_COLORS_HEX, DEFAULT_CUE_COLORS
from autocue.grid import resolve_grid

LOW_CONFIDENCE = 0.4
MIN_DURATION = 30.0


def needs_analysis(template: dict) -> bool:
    return any(not c["detect"].startswith("bar_") for c in template["cues"].values())


def _duration_seconds(track: dict) -> float | None:
    if not track.get("beat_data_blob"):
        return None
    bd = decode_beat_data(track["beat_data_blob"])
    return bd["total_samples"] / bd["sample_rate"] if bd["sample_rate"] > 0 else None


def existing_cues(track: dict, sample_rate: float) -> list[dict]:
    """Active hot cues currently in Engine for this track."""
    if not track.get("quick_cues_blob"):
        return []
    rgb_to_name = {(r, g, b): n for n, (a, r, g, b) in ENGINE_COLORS.items()}
    out = []
    for c in decode_quick_cues(track["quick_cues_blob"])["cues"]:
        if not is_cue_active(c):
            continue
        rgb = (c["color_r"], c["color_g"], c["color_b"])
        out.append({"slot": c["index"] + 1, "label": c["label"],
                    "color_name": rgb_to_name.get(rgb),
                    "color_hex": f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}",
                    "time_seconds": c["position_samples"] / sample_rate})
    return out


def plan_track(track: dict, template: dict, *, sample_rate: float,
               audio_path=None, grid_mode: str = "auto", anchor_mode: str = "auto",
               beat_offset: int = 0, overwrite: bool = False,
               max_duration: float | None = None, min_duration: float = MIN_DURATION,
               analyze=None, grid: dict | None = None, ai_detector=None) -> dict:
    """Plan cues for one track. Never writes.

    grid        an already-resolved grid (skips resolve_grid); e.g. a
                tap-tempo grid from the editor.
    analyze     analyze_structure callable, only needed for templates that
                use audio-analysis detect keys.
    ai_detector zero-arg callable giving the AI first downbeat (seconds).

    Returns {"status": ok|skip|error, "reason", "grid", "anchor", "proposed",
             "flags", "needs_review", "duration", "existing"}.
    """
    plan = {"status": "ok", "reason": "", "grid": None, "anchor": None,
            "proposed": [], "flags": [], "needs_review": False,
            "duration": _duration_seconds(track), "existing": []}

    def skip(reason):
        plan.update(status="skip", reason=reason)
        return plan

    def error(reason):
        plan.update(status="error", reason=reason)
        return plan

    if not track.get("quick_cues_blob"):
        return skip("no quickCues blob (analyze the track in Engine DJ first)")
    cue_data = decode_quick_cues(track["quick_cues_blob"])
    plan["existing"] = existing_cues(track, sample_rate)
    if plan["existing"] and not overwrite:
        return skip(f"{len(plan['existing'])} existing cues (use overwrite)")

    if not track.get("path"):
        return error("no file path")

    dur = plan["duration"]
    if dur is not None:
        if max_duration and dur > max_duration:
            return skip(f"{dur/60:.0f} min exceeds the {max_duration/60:.0f} min limit")
        if dur < min_duration:
            return skip(f"too short ({dur:.0f} s)")

    if grid is None:
        try:
            grid = resolve_grid(track, grid_mode, audio_path, sample_rate)
        except Exception as e:
            return error(f"AI grid failed: {e}")
    if grid is None or not grid.get("beats"):
        return skip((grid or {}).get("note") or "no beat grid")
    plan["grid"] = grid
    if grid.get("note"):
        plan["flags"].append(f"grid: {grid['note']}")

    picked = pick_anchor(
        anchor_mode, main_cue=get_main_cue(cue_data),
        downbeats=grid["downbeats"], beats=grid["beats"],
        samples_per_beat=grid["samples_per_beat"], sample_rate=sample_rate,
        detect_first_downbeat=ai_detector, grid_anchor=grid.get("first_downbeat"))
    plan["anchor"] = picked
    if picked["anchor"] is None:
        return skip("no usable bar-1 anchor "
                    f"({picked['note'] or 'no main cue, AI result, or grid'})")
    if picked["note"]:
        plan["flags"].append(f"bar 1: {picked['note']}")
    if picked["source"] == "grid" and anchor_mode == "auto":
        plan["flags"].append("bar 1 taken from the grid only (no main cue, no AI)")

    result, sr_scale = None, 1.0
    if needs_analysis(template):
        if analyze is None:
            return error("template needs audio analysis (pip install autocue[analysis])")
        if audio_path is None:
            return error("audio file not found (needed for analysis)")
        try:
            result = analyze(str(audio_path), **template.get("analysis", {}))
        except Exception as e:
            return error(f"analysis failed: {e}")
        analysis_sr = result["sample_rate"]
        sr_scale = sample_rate / analysis_sr if analysis_sr != sample_rate else 1.0

    total = grid.get("total_samples")
    for slot_key, cue_def in sorted(template["cues"].items(), key=lambda x: int(x[0])):
        slot = int(slot_key)
        detect_key = cue_def["detect"]
        label = cue_def.get("label", "")
        color_name = cue_def.get("color", DEFAULT_CUE_COLORS.get(slot, "yellow")).lower()
        optional = cue_def.get("optional", False)

        bar = resolve_bar_position(detect_key, picked["anchor"], grid["samples_per_beat"],
                                   grid["beats"], beat_offset=beat_offset)
        if bar is not None:
            pos, confidence = bar
            if pos is None:
                plan["flags"].append(f"cue {slot}: {detect_key} unresolved")
                continue
            if total and pos >= total:
                plan["flags"].append(f"cue {slot} ({detect_key}) falls past the end of the track")
                continue
        else:
            if result is None:
                continue
            raw = result["positions"].get(detect_key)
            confidence = result["confidences"].get(detect_key, 0.0)
            if raw is None:
                if not optional:
                    plan["flags"].append(f"cue {slot}: {detect_key} not detected")
                continue
            pos = snap_to_downbeat(raw * sr_scale, grid["downbeats"])

        if any(e["slot"] == slot for e in plan["existing"]) and not overwrite:
            continue
        if confidence < LOW_CONFIDENCE:
            plan["flags"].append(f"cue {slot}: low confidence ({confidence:.0%})")

        plan["proposed"].append({
            "slot": slot, "label": label, "color_name": color_name,
            "color_hex": ENGINE_COLORS_HEX[color_name],
            "position_samples": float(pos), "time_seconds": pos / sample_rate,
            "confidence": confidence,
        })

    if not plan["proposed"]:
        return skip("no cues to set")
    plan["needs_review"] = bool(plan["flags"])
    return plan


def apply_cues_to_blob(cue_data: dict, cues: list[dict], sample_rate: float,
                       clear_missing: bool = False) -> dict:
    """Return cue_data with `cues` (slot/label/color_name/time_seconds or
    position_samples) written into their slots. With clear_missing, slots
    not in `cues` are emptied."""
    by_slot = {int(c["slot"]): c for c in cues}
    for idx in range(len(cue_data["cues"])):
        slot = idx + 1
        if slot in by_slot:
            c = by_slot[slot]
            a, r, g, b = ENGINE_COLORS[c.get("color_name", "yellow").lower()]
            pos = c.get("position_samples")
            if pos is None:
                pos = float(c["time_seconds"]) * sample_rate
            cue_data["cues"][idx] = {
                "index": idx, "label": c.get("label", ""),
                "position_samples": float(pos),
                "color_a": a, "color_r": r, "color_g": g, "color_b": b,
            }
        elif clear_missing:
            cue_data["cues"][idx] = {
                "index": idx, "label": "", "position_samples": CUE_POSITION_EMPTY,
                "color_a": 0, "color_r": 0, "color_g": 0, "color_b": 0,
            }
    return cue_data
