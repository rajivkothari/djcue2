"""Bar-1 anchor selection and bar-position resolution.

Pure functions with no ML or Flask imports, so the CLI and the web
server share one implementation and it stays unit-testable.

The bar_N cue scheme only borrows *tempo* from Engine DJ's beat grid.
The *phase* (which beat is bar 1) comes from an anchor, chosen here:

    auto      main cue -> AI downbeat -> grid first downbeat
    main-cue  the track's main/load cue (falls back to grid)
    ai        Beat This! first detected downbeat (falls back to grid)
    grid      Engine DJ's own first downbeat (the old behaviour)
"""

BEATS_PER_BAR = 4
ANCHOR_MODES = ("auto", "main-cue", "ai", "grid")

# Snap resolved bar positions to a grid beat only when they are this close
# (in beats). A larger gap means the grid is phase-shifted, and snapping
# would re-apply exactly the error the anchor is meant to escape.
SNAP_TOLERANCE_BEATS = 0.25


def snap_to_nearest(position: float, candidates: list[float]) -> float:
    if not candidates:
        return position
    return min(candidates, key=lambda c: abs(c - position))


def resolve_bar_position(detect_key: str, anchor: float | None,
                         samples_per_beat: float | None, beats: list[float],
                         beat_offset: int = 0):
    """Resolve a 'bar_N' detect key to a sample position.

    Returns None for keys that aren't bar-based, (None, 0.0) when the bar
    can't be resolved, otherwise (position_samples, confidence).
    """
    if not detect_key.startswith("bar_"):
        return None
    if anchor is None or samples_per_beat is None:
        return None, 0.0
    bar_num = int(detect_key.split("_")[1])
    ideal = anchor + ((bar_num - 1) * BEATS_PER_BAR + beat_offset) * samples_per_beat
    if beats:
        nearest = snap_to_nearest(ideal, beats)
        if abs(nearest - ideal) <= samples_per_beat * SNAP_TOLERANCE_BEATS:
            ideal = nearest
    if ideal < 0:
        return None, 0.0
    return float(ideal), 1.0


def pick_anchor(mode: str, *, main_cue: float | None, downbeats: list[float],
                beats: list[float], samples_per_beat: float | None,
                sample_rate: float, detect_first_downbeat=None) -> dict:
    """Choose the bar-1 anchor for a track.

    detect_first_downbeat is a zero-argument callable returning the first
    AI-detected downbeat in seconds (or None). It is only invoked when the
    chosen mode actually needs it, so the model never loads for tracks
    that already have a main cue in auto mode. Any exception it raises is
    caught and reported in the note rather than aborting a batch.

    Returns {"anchor", "source", "candidates", "note"} where candidates
    holds every anchor we computed (samples), for diagnostics.
    """
    if mode not in ANCHOR_MODES:
        raise ValueError(f"Unknown anchor mode '{mode}'. "
                         f"Choose from: {', '.join(ANCHOR_MODES)}")

    grid = downbeats[0] if downbeats else (beats[0] if beats else None)
    candidates = {"main_cue": main_cue, "ai": None, "grid": grid}
    notes = []

    want_ai = mode == "ai" or (mode == "auto" and main_cue is None)
    if want_ai and detect_first_downbeat is not None:
        try:
            secs = detect_first_downbeat()
        except Exception as e:  # missing extra, bad audio, model download…
            notes.append(f"AI unavailable: {e}")
            secs = None
        if secs is not None:
            ai = float(secs) * sample_rate
            # The grid's beat spacing is trustworthy; only its phase isn't.
            # Locking the AI time to the nearest grid beat removes the
            # model's few-ms jitter without letting the grid's phase back in.
            if beats:
                ai = snap_to_nearest(ai, beats)
            candidates["ai"] = ai

    if mode == "main-cue":
        order = ["main_cue", "grid"]
    elif mode == "ai":
        order = ["ai", "grid"]
    elif mode == "grid":
        order = ["grid"]
    else:
        order = ["main_cue", "ai", "grid"]

    anchor, source = None, "none"
    for name in order:
        if candidates[name] is not None:
            anchor, source = candidates[name], name
            break

    if mode != "auto" and source != order[0]:
        notes.append(f"no {order[0].replace('_', ' ')}; fell back to {source}")

    ai, mc = candidates["ai"], candidates["main_cue"]
    if ai is not None and mc is not None and samples_per_beat:
        gap_beats = abs(ai - mc) / samples_per_beat
        if gap_beats > 0.5:
            notes.append(f"main cue and AI disagree by {gap_beats:.1f} beats")

    return {
        "anchor": anchor,
        "source": source.replace("_", " "),
        "candidates": candidates,
        "note": "; ".join(notes),
    }
