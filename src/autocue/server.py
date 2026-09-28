"""Flask web app for visual cue review workflow."""

import json
import mimetypes
import threading
import uuid
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, request, send_file, send_from_directory

from autocue.codec import (
    decode_quick_cues, encode_quick_cues, decode_beat_data,
    is_cue_active, snap_to_downbeat, get_downbeat_positions,
    get_beat_positions, get_samples_per_beat, get_main_cue,
    CUE_POSITION_EMPTY,
)
from autocue.anchor import pick_anchor, resolve_bar_position, ANCHOR_MODES
from autocue.grid import resolve_grid, engine_beat_data_from_grid, GRID_MODES
from autocue.codec import encode_beat_data
from autocue.db import write_beat_data
from autocue.templates import PHRASE_PRESETS, DEFAULT_PHRASE_CUES, with_intro
from autocue.grid import custom_grid
from autocue.undo import (record as undo_record, latest as undo_latest,
                          discard as undo_discard, b64 as undo_b64, unb64 as undo_unb64)
from autocue.constants import (
    ENGINE_COLORS, ENGINE_COLORS_HEX, DEFAULT_CUE_COLORS, get_sample_rate,
    format_time,
)
from autocue.db import (
    open_library, check_schema, list_playlists, list_crates,
    get_playlist_tracks, get_crate_tracks, resolve_audio_path,
    is_engine_dj_running, backup_library, write_quick_cues,
)
from autocue.templates import load_template, list_templates

app = Flask(__name__, static_folder="static")

_db_path: str | None = None


def _ai_detector(audio_path):
    """Zero-arg callable that runs Beat This! only if the anchor needs it."""
    def _detect():
        if audio_path is None:
            raise FileNotFoundError("audio file not found")
        from autocue.beats import detect_first_downbeat
        return detect_first_downbeat(str(audio_path))
    return _detect


def set_db_path(path: str):
    global _db_path
    _db_path = path


def _conn(readonly=True):
    return open_library(_db_path, readonly=readonly)


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/schema")
def api_schema():
    conn = _conn()
    try:
        version = check_schema(conn)
        return jsonify({"version": version})
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 400
    finally:
        conn.close()


@app.route("/api/playlists")
def api_playlists():
    conn = _conn()
    try:
        data = list_playlists(conn)
        return jsonify(data)
    except Exception as e:
        print(f"ERROR /api/playlists: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


@app.route("/api/crates")
def api_crates():
    conn = _conn()
    try:
        data = list_crates(conn)
        return jsonify(data)
    except Exception as e:
        print(f"ERROR /api/crates: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


@app.route("/api/templates")
def api_templates():
    try:
        result = [{"id": f"phrase-{b}", "name": f"Every {b} bars",
                   "phrase_bars": b, "default_cues": DEFAULT_PHRASE_CUES}
                  for b in PHRASE_PRESETS]
        for name in list_templates():
            t = load_template(name)
            result.append({"id": name, "name": t.get("name", name)})
        return jsonify(result)
    except Exception as e:
        print(f"ERROR /api/templates: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/tracks")
def api_tracks():
    source_type = request.args.get("type")
    source_name = request.args.get("name")
    if not source_type or not source_name:
        return jsonify({"error": "type and name required"}), 400

    conn = _conn()
    try:
        if source_type == "playlist":
            tracks = get_playlist_tracks(conn, source_name)
        elif source_type == "crate":
            tracks = get_crate_tracks(conn, source_name)
        else:
            return jsonify({"error": "type must be playlist or crate"}), 400
    finally:
        conn.close()

    result = []
    for t in tracks:
        sample_rate = get_sample_rate(t)
        existing_cues = []
        has_cues = False
        if t["quick_cues_blob"]:
            cue_data = decode_quick_cues(t["quick_cues_blob"])
            for c in cue_data["cues"]:
                if is_cue_active(c):
                    has_cues = True
                    existing_cues.append({
                        "slot": c["index"] + 1,
                        "label": c["label"],
                        "time_seconds": c["position_samples"] / sample_rate,
                        "color": f"#{c['color_r']:02x}{c['color_g']:02x}{c['color_b']:02x}",
                    })

        result.append({
            "id": t["id"],
            "title": t["title"],
            "artist": t["artist"],
            "bpm": t["bpm"],
            "has_cues": has_cues,
            "existing_cues": existing_cues,
            "sample_rate": sample_rate,
        })
    return jsonify(result)


@app.route("/api/audio/<int:track_id>")
def api_audio(track_id):
    conn = _conn()
    try:
        from autocue.db import list_tracks
        tracks = list_tracks(conn, search=str(track_id))
    finally:
        conn.close()

    if not tracks:
        return jsonify({"error": "Track not found"}), 404

    track = tracks[0]
    if not track["path"]:
        return jsonify({"error": "No file path"}), 404

    try:
        audio_path = resolve_audio_path(_db_path, track["path"])
    except FileNotFoundError:
        return jsonify({"error": "Audio file not found"}), 404

    mime = mimetypes.guess_type(str(audio_path))[0] or "audio/mpeg"
    return send_file(str(audio_path), mimetype=mime)


@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    data = request.json
    track_id = data.get("track_id")
    template_name = data.get("template", "edm")
    overwrite = data.get("overwrite", False)
    beat_offset = int(data.get("beat_offset", 0))
    anchor_mode = data.get("anchor", "auto")
    if anchor_mode not in ANCHOR_MODES:
        return jsonify({"error": f"Unknown anchor mode '{anchor_mode}'"}), 400

    conn = _conn()
    try:
        from autocue.db import list_tracks
        tracks = list_tracks(conn, search=str(track_id))
    finally:
        conn.close()

    if not tracks:
        return jsonify({"error": "Track not found"}), 404

    track = tracks[0]
    template = with_intro(load_template(template_name), int(data.get("intro_bars", 0) or 0))
    analysis_params = template.get("analysis", {})
    template_cues = template["cues"]

    needs_analysis = any(
        not cue_def["detect"].startswith("bar_")
        for cue_def in template_cues.values()
    )

    sample_rate = get_sample_rate(track)
    audio_path = _audio_path_or_none(track)

    grid_mode = data.get("grid", "auto")
    if grid_mode not in GRID_MODES:
        return jsonify({"error": f"Unknown grid mode '{grid_mode}'"}), 400
    try:
        grid = resolve_grid(track, grid_mode, audio_path, sample_rate)
    except Exception as e:
        return jsonify({"error": f"AI grid failed: {e}"}), 400
    if grid is None or not grid["beats"]:
        return jsonify({"error": (grid or {}).get("note")
                        or "Track has no beat grid in Engine DJ"}), 400
    downbeats, beats = grid["downbeats"], grid["beats"]
    samples_per_beat = grid["samples_per_beat"]

    existing_cue_data = None
    if track["quick_cues_blob"]:
        existing_cue_data = decode_quick_cues(track["quick_cues_blob"])

    main_cue = get_main_cue(existing_cue_data) if existing_cue_data else None
    picked = pick_anchor(
        anchor_mode, main_cue=main_cue, downbeats=downbeats, beats=beats,
        samples_per_beat=samples_per_beat, sample_rate=sample_rate,
        detect_first_downbeat=_ai_detector(audio_path),
        grid_anchor=grid.get("first_downbeat"))
    anchor = picked["anchor"]

    result = None
    sr_scale = 1.0
    if needs_analysis:
        try:
            from autocue.analysis import analyze_structure
        except ImportError as e:
            return jsonify({"error": str(e)}), 500

        if audio_path is None:
            return jsonify({"error": "Audio file not found"}), 404

        result = analyze_structure(str(audio_path), **analysis_params)
        analysis_sr = result["sample_rate"]
        sr_scale = sample_rate / analysis_sr if analysis_sr != sample_rate else 1.0

    proposed = []
    for slot_key, cue_def in sorted(template_cues.items(), key=lambda x: int(x[0])):
        slot = int(slot_key)
        cue_index = slot - 1
        detect_key = cue_def["detect"]
        label = cue_def.get("label", "")
        color_name = cue_def.get("color", DEFAULT_CUE_COLORS.get(slot, "yellow"))
        is_optional = cue_def.get("optional", False)

        bar_pos = resolve_bar_position(
            detect_key, anchor, samples_per_beat, beats, beat_offset=beat_offset)
        if bar_pos is not None:
            position_samples, confidence = bar_pos
            if position_samples is None:
                proposed.append({
                    "slot": slot, "label": label,
                    "color_name": color_name,
                    "color_hex": ENGINE_COLORS_HEX[color_name],
                    "detected": False, "optional": is_optional,
                    "confidence": 0.0,
                })
                continue
        else:
            if result is None:
                continue
            raw_pos = result["positions"].get(detect_key)
            confidence = result["confidences"].get(detect_key, 0.0)

            if raw_pos is None:
                proposed.append({
                    "slot": slot, "label": label,
                    "color_name": color_name,
                    "color_hex": ENGINE_COLORS_HEX[color_name],
                    "detected": False, "optional": is_optional,
                    "confidence": confidence,
                })
                continue

            position_samples = raw_pos * sr_scale
            if downbeats:
                position_samples = snap_to_downbeat(position_samples, downbeats)
        time_seconds = position_samples / sample_rate

        has_existing = False
        if existing_cue_data and not overwrite:
            existing = existing_cue_data["cues"][cue_index]
            if is_cue_active(existing):
                has_existing = True

        proposed.append({
            "slot": slot,
            "label": label,
            "color_name": color_name,
            "color_hex": ENGINE_COLORS_HEX[color_name],
            "detected": True,
            "time_seconds": time_seconds,
            "time_display": format_time(time_seconds),
            "position_samples": position_samples,
            "confidence": confidence,
            "optional": is_optional,
            "has_existing": has_existing,
        })

    return jsonify({
        "track_id": track["id"],
        "template": template_name,
        "sample_rate": sample_rate,
        "proposed": proposed,
        "grid": {"source": grid["source"], "tempo_bpm": grid["tempo_bpm"],
                 "note": grid.get("note", "")},
        "anchor": {
            "source": picked["source"],
            "note": picked["note"],
            "candidates": {
                name.replace("_", " "): (format_time(v / sample_rate)
                                         if v is not None else None)
                for name, v in picked["candidates"].items()
            },
        },
    })


@app.route("/api/finalize", methods=["POST"])
def api_finalize():
    data = request.json
    track_id = data.get("track_id")
    cues = data.get("cues", [])
    overwrite = data.get("overwrite", False)

    if is_engine_dj_running():
        return jsonify({"error": "Engine DJ is running. Close it first."}), 400

    conn = _conn()
    try:
        from autocue.db import list_tracks
        tracks = list_tracks(conn, search=str(track_id))
    finally:
        conn.close()

    if not tracks:
        return jsonify({"error": "Track not found"}), 404

    track = tracks[0]
    if not track["quick_cues_blob"]:
        return jsonify({"error": "Track has no quickCues blob"}), 400

    cue_data = decode_quick_cues(track["quick_cues_blob"])
    sample_rate = get_sample_rate(track)
    written = 0

    for cue in cues:
        slot = cue["slot"]
        cue_index = slot - 1
        existing = cue_data["cues"][cue_index]
        if is_cue_active(existing) and not overwrite:
            continue

        color_name = cue.get("color_name", DEFAULT_CUE_COLORS.get(slot, "yellow"))
        color_a, color_r, color_g, color_b = ENGINE_COLORS[color_name.lower()]

        cue_data["cues"][cue_index] = {
            "index": cue_index,
            "label": cue.get("label", ""),
            "position_samples": cue["position_samples"],
            "color_a": color_a,
            "color_r": color_r,
            "color_g": color_g,
            "color_b": color_b,
        }
        written += 1

    if written == 0:
        return jsonify({"message": "No cues to write", "written": 0})

    backup_path = backup_library(_db_path)
    new_blob = encode_quick_cues(cue_data)
    wdb = _conn(readonly=False)
    try:
        write_quick_cues(wdb, track["id"], new_blob)
    finally:
        wdb.close()

    return jsonify({
        "message": f"Wrote {written} cues",
        "written": written,
        "backup": str(backup_path),
    })


# ---------------------------------------------------------------------------
# Cue editor: library browser, bar-1 placement, generate, multi-target save
# ---------------------------------------------------------------------------

_vdj_db_path: str | None = None
_rekordbox_xml: str | None = None
_RGB_TO_NAME = {(r, g, b): name for name, (a, r, g, b) in ENGINE_COLORS.items()}


def _get_track(track_id):
    conn = _conn()
    try:
        from autocue.db import list_tracks
        tracks = list_tracks(conn, search=str(track_id))
    finally:
        conn.close()
    return tracks[0] if tracks else None


def _audio_path_or_none(track):
    if not track["path"]:
        return None
    try:
        return resolve_audio_path(_db_path, track["path"])
    except FileNotFoundError:
        return None


def _backups_dir() -> Path:
    d = Path(_db_path).parent / "autocue_backups"
    d.mkdir(exist_ok=True)
    return d


@app.route("/editor")
def editor():
    return send_from_directory(app.static_folder, "editor.html")


@app.route("/api/colors")
def api_colors():
    return jsonify(ENGINE_COLORS_HEX)


@app.route("/api/library")
def api_library():
    q = request.args.get("q", "").strip() or None
    limit = int(request.args.get("limit", 300))
    conn = _conn()
    try:
        from autocue.db import list_tracks
        tracks = list_tracks(conn, search=q, limit=limit)
    finally:
        conn.close()

    out = []
    for t in tracks:
        has_cues = False
        if t["quick_cues_blob"]:
            try:
                cd = decode_quick_cues(t["quick_cues_blob"])
                has_cues = any(is_cue_active(c) for c in cd["cues"])
            except Exception:
                pass
        duration = None
        if t["beat_data_blob"]:
            try:
                bd = decode_beat_data(t["beat_data_blob"])
                if bd["sample_rate"] > 0:
                    duration = bd["total_samples"] / bd["sample_rate"]
            except Exception:
                pass
        out.append({"id": t["id"], "title": t["title"], "artist": t["artist"],
                    "bpm": t["bpm"], "has_cues": has_cues, "duration": duration})
    return jsonify(out)


@app.route("/api/track/<int:track_id>")
def api_track(track_id):
    track = _get_track(track_id)
    if track is None:
        return jsonify({"error": "Track not found"}), 404

    sample_rate = get_sample_rate(track)
    beats, downbeats, spb, duration = [], [], None, None
    if track["beat_data_blob"]:
        bd = decode_beat_data(track["beat_data_blob"])
        beats = get_beat_positions(bd)
        downbeats = get_downbeat_positions(bd)
        spb = get_samples_per_beat(bd)
        if bd["sample_rate"] > 0:
            duration = bd["total_samples"] / bd["sample_rate"]

    cues, main_cue = [], None
    if track["quick_cues_blob"]:
        cd = decode_quick_cues(track["quick_cues_blob"])
        main_cue = get_main_cue(cd)
        for c in cd["cues"]:
            if not is_cue_active(c):
                continue
            rgb = (c["color_r"], c["color_g"], c["color_b"])
            cues.append({
                "slot": c["index"] + 1,
                "label": c["label"],
                "color_name": _RGB_TO_NAME.get(rgb),
                "color_hex": f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}",
                "time_seconds": c["position_samples"] / sample_rate,
            })

    audio_path = _audio_path_or_none(track)
    ext = audio_path.suffix.lower() if audio_path else None
    from autocue.exporters import serato, vdj
    return jsonify({
        "id": track["id"], "title": track["title"], "artist": track["artist"],
        "bpm": track["bpm"], "sample_rate": sample_rate, "duration": duration,
        "beats": [b / sample_rate for b in beats],
        "downbeats": [d / sample_rate for d in downbeats],
        "seconds_per_beat": (spb / sample_rate) if spb else None,
        "main_cue_seconds": (main_cue / sample_rate) if main_cue is not None else None,
        "grid_first_downbeat_seconds": (downbeats[0] / sample_rate) if downbeats else None,
        "cues": cues,
        "has_quick_cues": track["quick_cues_blob"] is not None,
        "audio_available": audio_path is not None,
        "audio_path": str(audio_path) if audio_path else None,
        "serato_supported": bool(ext) and ext in (serato.MP3_LIKE | serato.MP4_LIKE | serato.FLAC_LIKE),
        "vdj_available": (_vdj_db_path or vdj.find_database()) is not None,
        "rekordbox_path": str(_rb_path()),
        "can_undo": undo_latest(_backups_dir(), track["id"]) is not None,
    })


@app.route("/api/track/<int:track_id>/ai_downbeat")
def api_ai_downbeat(track_id):
    track = _get_track(track_id)
    if track is None:
        return jsonify({"error": "Track not found"}), 404
    audio_path = _audio_path_or_none(track)
    if audio_path is None:
        return jsonify({"error": "Audio file not found"}), 404
    try:
        from autocue.beats import detect_first_downbeat
        secs = detect_first_downbeat(str(audio_path))
    except ImportError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"AI detection failed: {e}"}), 500
    if secs is None:
        return jsonify({"error": "No downbeat detected"}), 404
    return jsonify({"seconds": secs})


def _grid_summary(grid: dict, sample_rate: float) -> dict:
    """JSON-friendly view of a grid, times in seconds."""
    out = {
        "source": grid["source"],
        "tempo_bpm": grid["tempo_bpm"],
        "seconds_per_beat": grid["samples_per_beat"] / sample_rate,
        "beats": [b / sample_rate for b in grid["beats"]],
        "downbeats": [d / sample_rate for d in grid["downbeats"]],
        "note": grid.get("note", ""),
    }
    if grid.get("first_downbeat") is not None:
        out["first_downbeat_seconds"] = grid["first_downbeat"] / sample_rate
    for k in ("variable_tempo", "rms_residual_ms", "n_detected"):
        if k in grid:
            out[k] = grid[k]
    return out


@app.route("/api/track/<int:track_id>/grid")
def api_track_grid(track_id):
    """Beat grid for a track: ?source=engine|ai (default engine)."""
    track = _get_track(track_id)
    if track is None:
        return jsonify({"error": "Track not found"}), 404
    source = request.args.get("source", "engine")
    if source not in GRID_MODES:
        return jsonify({"error": f"Unknown grid source '{source}'"}), 400
    sample_rate = get_sample_rate(track)
    try:
        grid = resolve_grid(track, source, _audio_path_or_none(track), sample_rate)
    except ImportError as e:
        return jsonify({"error": str(e)}), 400
    except FileNotFoundError:
        return jsonify({"error": "Audio file not found"}), 404
    except Exception as e:
        return jsonify({"error": f"AI grid failed: {e}"}), 500
    if grid is None or not grid["beats"]:
        return jsonify({"error": (grid or {}).get("note") or "No beat grid"}), 404
    return jsonify(_grid_summary(grid, sample_rate))


@app.route("/api/generate", methods=["POST"])
def api_generate():
    data = request.json
    track = _get_track(data.get("track_id"))
    if track is None:
        return jsonify({"error": "Track not found"}), 404
    anchor_seconds = data.get("anchor_seconds")
    if anchor_seconds is None:
        return jsonify({"error": "Set bar 1 first"}), 400
    try:
        template = with_intro(load_template(data.get("template", "edm")),
                              int(data.get("intro_bars", 0) or 0))
    except (FileNotFoundError, ValueError) as e:
        return jsonify({"error": str(e)}), 400

    grid_mode = data.get("grid", "auto")
    sample_rate = get_sample_rate(track)
    audio_path = _audio_path_or_none(track)
    if grid_mode == "custom":
        try:
            grid = _build_grid_from_spec(track, sample_rate, {
                "source": "custom", "seconds_per_beat": data["seconds_per_beat"],
                "bar1_seconds": anchor_seconds,
                "duration_seconds": data.get("duration_seconds", 0)}, audio_path)
        except Exception as e:
            return jsonify({"error": f"Custom grid failed: {e}"}), 400
    else:
        if grid_mode not in GRID_MODES:
            return jsonify({"error": f"Unknown grid mode '{grid_mode}'"}), 400
        try:
            grid = resolve_grid(track, grid_mode, audio_path, sample_rate)
        except Exception as e:
            return jsonify({"error": f"AI grid failed: {e}"}), 400
    if grid is None or not grid["beats"]:
        return jsonify({"error": (grid or {}).get("note")
                        or "Track has no beat grid in Engine DJ"}), 400
    beats, spb, total = grid["beats"], grid["samples_per_beat"], grid.get("total_samples")

    anchor = float(anchor_seconds) * sample_rate
    proposed, unsupported, beyond_end = [], [], []
    for slot_key, cue_def in sorted(template["cues"].items(), key=lambda x: int(x[0])):
        slot = int(slot_key)
        detect_key = cue_def["detect"]
        res = resolve_bar_position(detect_key, anchor, spb, beats)
        if res is None:
            unsupported.append(f"cue {slot} ({detect_key})")
            continue
        pos, _ = res
        if pos is None:
            continue
        if total and pos >= total:
            beyond_end.append(slot)
            continue
        color_name = cue_def.get("color", DEFAULT_CUE_COLORS.get(slot, "yellow"))
        t = pos / sample_rate
        proposed.append({"slot": slot, "label": cue_def.get("label", ""),
                         "color_name": color_name,
                         "color_hex": ENGINE_COLORS_HEX[color_name],
                         "time_seconds": t, "time_display": format_time(t)})
    return jsonify({"proposed": proposed, "unsupported": unsupported,
                    "beyond_end": beyond_end,
                    "grid": {"source": grid["source"],
                             "tempo_bpm": grid["tempo_bpm"],
                             "note": grid.get("note", "")},
                    "template_name": template.get("name")})


@app.route("/favicon.ico")
def favicon():
    return "", 204


def _rb_path() -> Path:
    from autocue.exporters import rekordbox
    if _rekordbox_xml:
        return Path(_rekordbox_xml)
    return rekordbox.default_path(Path(_db_path).parent.parent)


def _build_grid_from_spec(track, sample_rate, spec, audio_path):
    """spec: "ai" | {"source": "custom", "seconds_per_beat", "bar1_seconds"}."""
    if spec == "ai" or spec is None:
        grid = resolve_grid(track, "ai", audio_path, sample_rate)
        if grid is None or not grid["beats"]:
            raise RuntimeError("AI could not build a grid for this track")
        return grid
    if isinstance(spec, dict) and spec.get("source") == "custom":
        total = None
        if track.get("beat_data_blob"):
            total = decode_beat_data(track["beat_data_blob"])["total_samples"]
        if total is None and audio_path is not None:
            try:
                import soundfile as sf
                info = sf.info(str(audio_path))
                total = info.frames * sample_rate / info.samplerate
            except Exception:
                total = None
        if total is None:
            total = float(spec.get("duration_seconds", 0)) * sample_rate
        if not total:
            raise RuntimeError("Track length unknown; cannot build a custom grid")
        return custom_grid(float(spec["seconds_per_beat"]), float(spec["bar1_seconds"]),
                           sample_rate, total, note="custom tempo")
    raise RuntimeError("Unknown grid specification")


def _save_track(track, cues, targets, clear_missing=True, grid_spec=None):
    """Write `cues` to the chosen targets; journal the previous state."""
    from autocue.exporters import serato, vdj, rekordbox
    from autocue.pipeline import apply_cues_to_blob

    results = {}
    sample_rate = get_sample_rate(track)
    audio_path = _audio_path_or_none(track)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    snapshot = {"track_id": track["id"], "title": track["title"]}

    # --- Engine DJ -----------------------------------------------------
    if targets.get("engine"):
        if is_engine_dj_running():
            results["engine"] = {"ok": False, "message": "Engine DJ is running. Close it first."}
        elif not track["quick_cues_blob"]:
            results["engine"] = {"ok": False, "message": "Track has no quickCues blob (analyze it in Engine DJ first)"}
        else:
            cue_data = apply_cues_to_blob(decode_quick_cues(track["quick_cues_blob"]),
                                          cues, sample_rate, clear_missing=clear_missing)
            backup = backup_library(_db_path)
            wdb = _conn(readonly=False)
            try:
                write_quick_cues(wdb, track["id"], encode_quick_cues(cue_data))
            finally:
                wdb.close()
            snapshot["engine"] = {"quick_cues": undo_b64(track["quick_cues_blob"])}
            results["engine"] = {"ok": True, "message": f"Wrote {len(cues)} cues",
                                 "backup": str(backup)}

    # --- beat grid into Engine DJ (opt-in) -------------------------------
    if targets.get("engine_grid"):
        if is_engine_dj_running():
            results["engine_grid"] = {"ok": False, "message": "Engine DJ is running. Close it first."}
        else:
            try:
                grid = _build_grid_from_spec(track, sample_rate, grid_spec, audio_path)
                bd = engine_beat_data_from_grid(grid, track["beat_data_blob"])
                backup = (results.get("engine", {}).get("backup")
                          or str(backup_library(_db_path)))
                wdb = _conn(readonly=False)
                try:
                    write_beat_data(wdb, track["id"], encode_beat_data(bd))
                finally:
                    wdb.close()
                snapshot["engine_grid"] = {"beat_data": undo_b64(track["beat_data_blob"])}
                results["engine_grid"] = {
                    "ok": True, "backup": backup,
                    "message": f"Replaced Engine DJ's beat grid: {grid['tempo_bpm']:.2f} BPM, "
                               f"bar 1 at {format_time(grid['first_downbeat'] / sample_rate)}"}
            except Exception as e:
                results["engine_grid"] = {"ok": False, "message": str(e)}

    # --- Serato tags in the audio file (also read by djay Pro) ----------
    if targets.get("serato"):
        if audio_path is None:
            results["serato"] = {"ok": False, "message": "Audio file not found"}
        else:
            try:
                scues = []
                for c in cues:
                    a, r, g, b = ENGINE_COLORS[c.get("color_name", "yellow").lower()]
                    scues.append(serato.SeratoCue(
                        index=int(c["slot"]) - 1,
                        position_ms=int(round(float(c["time_seconds"]) * 1000)),
                        color=(r, g, b), name=c.get("label", "")))
                previous = serato.write_cues(audio_path, scues)
                res = {"ok": True, "message": f"Wrote {len(scues)} cues to {audio_path.name}"}
                if previous is not None:
                    bpath = _backups_dir() / f"serato_{track['id']}_{stamp}.bin"
                    bpath.write_bytes(previous)
                    res["backup"] = str(bpath)
                snapshot["serato"] = {"path": str(audio_path), "tag": undo_b64(previous)}
                results["serato"] = res
            except Exception as e:
                results["serato"] = {"ok": False, "message": str(e)}

    # --- VirtualDJ database.xml ------------------------------------------
    if targets.get("vdj"):
        db = _vdj_db_path or vdj.find_database()
        if db is None:
            results["vdj"] = {"ok": False, "message": "VirtualDJ database.xml not found"}
        elif audio_path is None:
            results["vdj"] = {"ok": False, "message": "Audio file not found"}
        elif vdj.is_virtualdj_running():
            results["vdj"] = {"ok": False, "message": "VirtualDJ is running. Close it first."}
        else:
            try:
                previous = vdj.read_cues(db, str(audio_path))
                vcues = []
                for c in cues:
                    a, r, g, b = ENGINE_COLORS[c.get("color_name", "yellow").lower()]
                    vcues.append({"num": int(c["slot"]), "seconds": float(c["time_seconds"]),
                                  "name": c.get("label", ""), "color": (r, g, b)})
                res = vdj.write_cues(db, str(audio_path), vcues, replace_all=clear_missing)
                snapshot["vdj"] = {"db": str(db), "path": str(audio_path), "cues": previous}
                results["vdj"] = {"ok": True, "backup": res["backup"],
                                  "message": f"Wrote {res['written']} cues"
                                             + (" (new entry)" if res["created_song"] else "")}
            except Exception as e:
                results["vdj"] = {"ok": False, "message": str(e)}

    # --- rekordbox XML collection ----------------------------------------
    if targets.get("rekordbox"):
        if audio_path is None:
            results["rekordbox"] = {"ok": False, "message": "Audio file not found"}
        else:
            try:
                xml = _rb_path()
                previous = rekordbox.read_cues(xml, str(audio_path))
                rcues = []
                for c in cues:
                    a, r, g, b = ENGINE_COLORS[c.get("color_name", "yellow").lower()]
                    rcues.append({"num": int(c["slot"]), "seconds": float(c["time_seconds"]),
                                  "name": c.get("label", ""), "color": (r, g, b)})
                grid_info, duration = None, None
                try:
                    g = (_build_grid_from_spec(track, sample_rate, grid_spec, audio_path)
                         if grid_spec else resolve_grid(track, "engine", audio_path, sample_rate))
                    if g and g.get("beats"):
                        bar1 = g.get("first_downbeat")
                        if bar1 is None:
                            bar1 = min((c["time_seconds"] for c in cues if int(c["slot"]) == 1),
                                       default=g["downbeats"][0] / sample_rate if g["downbeats"] else 0.0)
                        else:
                            bar1 = bar1 / sample_rate
                        grid_info = {"first_beat_seconds": bar1, "bpm": g["tempo_bpm"]}
                        if g.get("total_samples"):
                            duration = g["total_samples"] / sample_rate
                except Exception:
                    grid_info = None
                res = rekordbox.write_cues(
                    xml, str(audio_path), rcues, title=track["title"], artist=track["artist"],
                    bpm=track.get("bpm"), duration_seconds=duration, grid=grid_info)
                snapshot["rekordbox"] = {"xml": str(xml), "path": str(audio_path),
                                         "cues": previous, "existed": previous is not None}
                results["rekordbox"] = {
                    "ok": True, "backup": res["backup"],
                    "message": f"Wrote {res['written']} cues to {xml.name}"
                               + (" (new entry)" if res["created_track"] else "")
                               + (" with beat grid" if grid_info else "")}
            except Exception as e:
                results["rekordbox"] = {"ok": False, "message": str(e)}

    if any(k in snapshot for k in ("engine", "engine_grid", "serato", "vdj", "rekordbox")):
        undo_record(_backups_dir(), snapshot)
    return results


@app.route("/api/save", methods=["POST"])
def api_save():
    data = request.json
    track = _get_track(data.get("track_id"))
    if track is None:
        return jsonify({"error": "Track not found"}), 404
    cues = data.get("cues", [])
    for c in cues:
        if not 1 <= int(c["slot"]) <= 8:
            return jsonify({"error": f"Bad slot {c['slot']}"}), 400
        if c.get("color_name", "yellow").lower() not in ENGINE_COLORS:
            return jsonify({"error": f"Unknown color {c.get('color_name')}"}), 400
    results = _save_track(track, cues, data.get("targets", {}),
                          clear_missing=bool(data.get("clear_missing", True)),
                          grid_spec=data.get("grid_spec"))
    return jsonify({"results": results})


@app.route("/api/undo/<int:track_id>", methods=["POST"])
def api_undo(track_id):
    """Restore every target to the state recorded by the last save."""
    from autocue.exporters import serato, vdj, rekordbox
    track = _get_track(track_id)
    if track is None:
        return jsonify({"error": "Track not found"}), 404
    found = undo_latest(_backups_dir(), track_id)
    if found is None:
        return jsonify({"error": "Nothing to undo for this track"}), 404
    path, snap = found
    results = {}

    if "engine" in snap or "engine_grid" in snap:
        if is_engine_dj_running():
            results["engine"] = {"ok": False, "message": "Engine DJ is running. Close it first."}
        else:
            try:
                backup_library(_db_path)
                wdb = _conn(readonly=False)
                try:
                    if "engine" in snap:
                        blob = undo_unb64(snap["engine"]["quick_cues"])
                        if blob is not None:
                            write_quick_cues(wdb, track_id, blob)
                            results["engine"] = {"ok": True, "message": "Cues restored"}
                    if "engine_grid" in snap:
                        blob = undo_unb64(snap["engine_grid"]["beat_data"])
                        write_beat_data(wdb, track_id, blob)
                        results["engine_grid"] = {"ok": True, "message": "Beat grid restored"}
                finally:
                    wdb.close()
            except Exception as e:
                results["engine"] = {"ok": False, "message": str(e)}

    if "serato" in snap:
        try:
            serato.restore_tag_bytes(snap["serato"]["path"], undo_unb64(snap["serato"]["tag"]))
            results["serato"] = {"ok": True, "message": "File tag restored"}
        except Exception as e:
            results["serato"] = {"ok": False, "message": str(e)}

    if "vdj" in snap:
        try:
            s = snap["vdj"]
            if vdj.is_virtualdj_running():
                raise RuntimeError("VirtualDJ is running. Close it first.")
            prev = [dict(c, color=tuple(c["color"])) for c in (s["cues"] or [])]
            vdj.write_cues(s["db"], s["path"], prev, replace_all=True)
            results["vdj"] = {"ok": True, "message": f"Restored {len(prev)} cues"}
        except Exception as e:
            results["vdj"] = {"ok": False, "message": str(e)}

    if "rekordbox" in snap:
        try:
            s = snap["rekordbox"]
            if s.get("existed") and s.get("cues") is not None:
                prev = [dict(c, color=tuple(c["color"])) for c in s["cues"]]
                rekordbox.write_cues(s["xml"], s["path"], prev)
                results["rekordbox"] = {"ok": True, "message": f"Restored {len(prev)} cues"}
            else:
                rekordbox.remove_track(s["xml"], s["path"])
                results["rekordbox"] = {"ok": True, "message": "Entry removed"}
        except Exception as e:
            results["rekordbox"] = {"ok": False, "message": str(e)}

    if all(r.get("ok") for r in results.values()):
        undo_discard(path)
    remaining = undo_latest(_backups_dir(), track_id)
    return jsonify({"results": results, "restored_from": snap.get("time"),
                    "can_undo": remaining is not None})


# ---------------------------------------------------------------------------
# Batch: plan a whole playlist/crate in the background, then apply
# ---------------------------------------------------------------------------

_jobs: dict = {}
_jobs_lock = threading.Lock()


def _job_public(job: dict) -> dict:
    return {k: v for k, v in job.items() if k not in ("_tracks", "_plans", "_thread")}


def _run_batch_job(job_id: str):
    job = _jobs[job_id]
    from autocue.pipeline import plan_track, needs_analysis
    from autocue.templates import load_template, with_intro
    try:
        template = with_intro(load_template(job["template"]), job["intro_bars"])
    except Exception as e:
        job.update(done=True, error=str(e))
        return
    analyze = None
    if needs_analysis(template):
        try:
            from autocue.analysis import analyze_structure as analyze
        except ImportError as e:
            job.update(done=True, error=str(e))
            return
    job["template_name"] = template.get("name")

    for i, track in enumerate(job["_tracks"]):
        if job.get("cancel"):
            break
        sample_rate = get_sample_rate(track)
        audio_path = _audio_path_or_none(track)
        try:
            plan = plan_track(track, template, sample_rate=sample_rate, audio_path=audio_path,
                              grid_mode=job["grid"], anchor_mode=job["anchor"],
                              beat_offset=job["beat_offset"], overwrite=job["overwrite"],
                              max_duration=job["max_duration"], analyze=analyze,
                              ai_detector=_ai_detector(audio_path))
        except Exception as e:
            plan = {"status": "error", "reason": str(e), "proposed": [], "flags": [],
                    "needs_review": False, "grid": None, "anchor": None,
                    "duration": None, "existing": []}
        job["_plans"][track["id"]] = plan
        row = {
            "track_id": track["id"], "title": track["title"], "artist": track["artist"],
            "status": plan["status"], "reason": plan["reason"],
            "needs_review": plan["needs_review"], "flags": plan["flags"],
            "grid": (plan["grid"] or {}).get("source"),
            "tempo_bpm": (plan["grid"] or {}).get("tempo_bpm"),
            "anchor": (plan["anchor"] or {}).get("source"),
            "cues": [{"slot": c["slot"], "label": c["label"], "time_display": format_time(c["time_seconds"])}
                     for c in plan["proposed"]],
            "applied": None,
        }
        job["rows"].append(row)
        job["progress"] = i + 1
    job["done"] = True


@app.route("/api/batch", methods=["POST"])
def api_batch_start():
    data = request.json or {}
    source_type, name = data.get("type"), data.get("name")
    if not source_type or not name:
        return jsonify({"error": "type and name required"}), 400
    conn = _conn()
    try:
        tracks = (get_playlist_tracks(conn, name) if source_type == "playlist"
                  else get_crate_tracks(conn, name))
    finally:
        conn.close()
    if not tracks:
        return jsonify({"error": f"No tracks in {source_type} '{name}'"}), 404
    grid = data.get("grid", "auto")
    anchor = data.get("anchor", "auto")
    if grid not in GRID_MODES or anchor not in ANCHOR_MODES:
        return jsonify({"error": "Bad grid or anchor mode"}), 400

    job_id = uuid.uuid4().hex[:10]
    job = {
        "id": job_id, "source": f"{source_type} '{name}'", "total": len(tracks),
        "progress": 0, "done": False, "error": None, "rows": [],
        "template": data.get("template", "phrase-16"),
        "intro_bars": int(data.get("intro_bars", 0) or 0),
        "grid": grid, "anchor": anchor,
        "beat_offset": int(data.get("beat_offset", 0) or 0),
        "overwrite": bool(data.get("overwrite", False)),
        "max_duration": float(data.get("max_duration", 900) or 0) or None,
        "_tracks": tracks, "_plans": {},
    }
    with _jobs_lock:
        _jobs[job_id] = job
    t = threading.Thread(target=_run_batch_job, args=(job_id,), daemon=True)
    job["_thread"] = t
    t.start()
    return jsonify(_job_public(job))


@app.route("/api/batch/<job_id>")
def api_batch_status(job_id):
    job = _jobs.get(job_id)
    if job is None:
        return jsonify({"error": "Unknown job"}), 404
    return jsonify(_job_public(job))


@app.route("/api/batch/<job_id>/cancel", methods=["POST"])
def api_batch_cancel(job_id):
    job = _jobs.get(job_id)
    if job is None:
        return jsonify({"error": "Unknown job"}), 404
    job["cancel"] = True
    return jsonify({"ok": True})


@app.route("/api/batch/<job_id>/apply", methods=["POST"])
def api_batch_apply(job_id):
    """Write the planned cues for the selected tracks to the chosen targets."""
    job = _jobs.get(job_id)
    if job is None:
        return jsonify({"error": "Unknown job"}), 404
    if not job["done"]:
        return jsonify({"error": "Batch is still running"}), 409
    data = request.json or {}
    targets = data.get("targets", {"engine": True})
    ids = data.get("track_ids")
    write_grid = bool(data.get("write_grid", False))
    applied = {}
    for row in job["rows"]:
        tid = row["track_id"]
        if ids is not None and tid not in ids:
            continue
        plan = job["_plans"].get(tid)
        if not plan or plan["status"] != "ok":
            continue
        track = _get_track(tid)
        if track is None:
            continue
        tgts = dict(targets)
        if write_grid and plan["grid"] and plan["grid"]["source"] == "ai":
            tgts["engine_grid"] = True
        results = _save_track(track, plan["proposed"], tgts,
                              clear_missing=bool(data.get("clear_missing", False)),
                              grid_spec="ai")
        row["applied"] = results
        applied[tid] = results
    return jsonify({"applied": applied, "count": len(applied)})


def run_server(db_path: str, host: str = "127.0.0.1", port: int = 5555,
               vdj_db: str | None = None, rekordbox_xml: str | None = None):
    global _vdj_db_path, _rekordbox_xml
    _vdj_db_path = vdj_db
    _rekordbox_xml = rekordbox_xml
    set_db_path(db_path)
    conn = _conn()
    try:
        version = check_schema(conn)
        print(f"Schema: {version}")
    finally:
        conn.close()
    print(f"Review workflow: http://{host}:{port}")
    print(f"Cue editor:      http://{host}:{port}/editor")
    print(f"rekordbox XML:   {_rb_path()}")
    app.run(host=host, port=port, debug=False, threaded=True)
