"""Beat-grid selection: Engine DJ's stored grid or an AI-built one.

Everything downstream (bar_N cues, snapping, anchoring) only needs three
things from a grid: every beat position, the samples-per-beat, and the
downbeats. This module produces that from either source in the track's
own sample domain, and can turn an AI grid back into an Engine beatData
blob for the opt-in write-back.

    auto    Engine's grid when the track has one, otherwise AI
    engine  Engine's grid only (skip the track if it has none)
    ai      AI grid (needs the audio file and the [beats] extra)
"""

from autocue.codec import (
    decode_beat_data, decode_track_data,
    get_beat_positions, get_downbeat_positions, get_samples_per_beat,
)

GRID_MODES = ("auto", "engine", "ai")
BEATS_PER_BAR = 4


def _engine_grid(beat_blob) -> dict | None:
    if not beat_blob:
        return None
    bd = decode_beat_data(beat_blob)
    beats = get_beat_positions(bd)
    spb = get_samples_per_beat(bd)
    if not beats or spb is None:
        return None
    return {
        "source": "engine",
        "beats": beats,
        "downbeats": get_downbeat_positions(bd),
        "samples_per_beat": spb,
        "sample_rate": bd["sample_rate"],
        "total_samples": bd["total_samples"],
        "tempo_bpm": 60.0 * bd["sample_rate"] / spb,
        "note": "",
    }


def _ai_grid(audio_path, sample_rate: float, bpm_hint, total_samples,
             build=None) -> dict | None:
    if audio_path is None:
        raise FileNotFoundError("audio file not found")
    if build is None:
        from autocue.beats import build_grid as build
    g = build(str(audio_path), bpm_hint=bpm_hint)
    if g is None:
        return None
    notes = []
    if g["variable_tempo"]:
        notes.append(f"tempo drifts (±{g['rms_residual_ms']:.0f} ms); "
                     f"cues far from bar 1 may need nudging")
    if g["downbeat_confidence"] < 1.0:
        notes.append("no clear downbeat found; bar 1 = first beat")
    return {
        "source": "ai",
        "beats": [b * sample_rate for b in g["beats"]],
        "downbeats": [d * sample_rate for d in g["downbeats"]],
        "samples_per_beat": g["seconds_per_beat"] * sample_rate,
        "sample_rate": sample_rate,
        "total_samples": total_samples,
        "tempo_bpm": g["tempo_bpm"],
        "first_downbeat": g["first_downbeat"] * sample_rate,
        "first_beat_index": g["first_beat_index"],
        "rms_residual_ms": g["rms_residual_ms"],
        "variable_tempo": g["variable_tempo"],
        "n_detected": g["n_detected"],
        "note": "; ".join(notes),
    }


def resolve_grid(track: dict, mode: str, audio_path, sample_rate: float,
                 build=None) -> dict | None:
    """Pick the grid for a track. Returns None when nothing usable exists.

    `build` lets tests inject a fake AI grid builder.
    """
    if mode not in GRID_MODES:
        raise ValueError(f"Unknown grid mode '{mode}'. "
                         f"Choose from: {', '.join(GRID_MODES)}")

    engine = _engine_grid(track.get("beat_data_blob")) if mode != "ai" else None
    if engine is not None:
        return engine
    if mode == "engine":
        return None

    total = None
    if track.get("beat_data_blob"):
        total = decode_beat_data(track["beat_data_blob"])["total_samples"]
    elif track.get("track_data_blob"):
        total = decode_track_data(track["track_data_blob"])["total_samples"]

    try:
        ai = _ai_grid(audio_path, sample_rate, track.get("bpm"), total, build)
    except Exception as e:
        if mode == "ai":
            raise
        return {"source": "none", "beats": [], "downbeats": [],
                "samples_per_beat": None, "note": f"AI grid unavailable: {e}"}
    if ai is not None and ai["total_samples"] is None:
        ai["total_samples"] = ai["beats"][-1] + ai["samples_per_beat"] if ai["beats"] else None
    return ai


def engine_beat_data_from_grid(grid: dict, existing_blob=None) -> dict:
    """Build an Engine beatData dict from an AI grid.

    Two markers, as Engine itself writes for constant-tempo tracks. Beat
    number 0 sits on bar 1, so downbeats land on multiples of 4 exactly
    the way the codec's decoder expects. The first marker is the first
    grid beat at/after sample 0 (a negative beat number when bar 1 isn't
    the very first beat), so the grid covers the whole track.
    """
    if grid.get("source") != "ai":
        raise ValueError("Only an AI grid can be written back")
    spb = grid["samples_per_beat"]
    first_db = grid["first_downbeat"]
    total = grid["total_samples"]
    kmin = grid["first_beat_index"]
    n_beats = int((total - first_db) // spb)
    first = {"sample_offset": first_db + kmin * spb, "beat_number": kmin,
             "number_of_beats": n_beats - kmin, "unknown_value_1": 0}
    last = {"sample_offset": first_db + n_beats * spb, "beat_number": n_beats,
            "number_of_beats": 0, "unknown_value_1": 0}
    extra = b""
    if existing_blob:
        extra = decode_beat_data(existing_blob)["extra_data"]
    return {
        "sample_rate": grid["sample_rate"],
        "total_samples": float(total),
        "is_beatgrid_set": True,
        "default_markers": [dict(first), dict(last)],
        "adjusted_markers": [dict(first), dict(last)],
        "extra_data": extra,
    }
