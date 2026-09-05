"""Flask web app for visual cue review workflow."""

import json
import mimetypes
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
        names = list_templates()
        result = []
        for name in names:
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
    template = load_template(template_name)
    analysis_params = template.get("analysis", {})
    template_cues = template["cues"]

    needs_analysis = any(
        not cue_def["detect"].startswith("bar_")
        for cue_def in template_cues.values()
    )

    sample_rate = get_sample_rate(track)

    downbeats = []
    beats = []
    samples_per_beat = None
    if track["beat_data_blob"]:
        beat_data = decode_beat_data(track["beat_data_blob"])
        downbeats = get_downbeat_positions(beat_data)
        beats = get_beat_positions(beat_data)
        samples_per_beat = get_samples_per_beat(beat_data)

    existing_cue_data = None
    if track["quick_cues_blob"]:
        existing_cue_data = decode_quick_cues(track["quick_cues_blob"])

    audio_path = None
    if track["path"]:
        try:
            audio_path = resolve_audio_path(_db_path, track["path"])
        except FileNotFoundError:
            audio_path = None

    main_cue = get_main_cue(existing_cue_data) if existing_cue_data else None
    picked = pick_anchor(
        anchor_mode, main_cue=main_cue, downbeats=downbeats, beats=beats,
        samples_per_beat=samples_per_beat, sample_rate=sample_rate,
        detect_first_downbeat=_ai_detector(audio_path))
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


@app.route("/api/generate", methods=["POST"])
def api_generate():
    data = request.json
    track = _get_track(data.get("track_id"))
    if track is None:
        return jsonify({"error": "Track not found"}), 404
    anchor_seconds = data.get("anchor_seconds")
    if anchor_seconds is None:
        return jsonify({"error": "Set bar 1 first"}), 400
    template = load_template(data.get("template", "edm"))

    sample_rate = get_sample_rate(track)
    beats, spb, total = [], None, None
    if track["beat_data_blob"]:
        bd = decode_beat_data(track["beat_data_blob"])
        beats = get_beat_positions(bd)
        spb = get_samples_per_beat(bd)
        total = bd["total_samples"]
    if spb is None:
        return jsonify({"error": "Track has no beat grid in Engine DJ"}), 400

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
                    "beyond_end": beyond_end})


@app.route("/favicon.ico")
def favicon():
    return "", 204


@app.route("/api/save", methods=["POST"])
def api_save():
    data = request.json
    track = _get_track(data.get("track_id"))
    if track is None:
        return jsonify({"error": "Track not found"}), 404
    cues = data.get("cues", [])
    targets = data.get("targets", {})
    clear_missing = bool(data.get("clear_missing", True))
    results = {}

    for c in cues:
        if not 1 <= int(c["slot"]) <= 8:
            return jsonify({"error": f"Bad slot {c['slot']}"}), 400
        if c.get("color_name", "yellow").lower() not in ENGINE_COLORS:
            return jsonify({"error": f"Unknown color {c.get('color_name')}"}), 400

    sample_rate = get_sample_rate(track)
    audio_path = _audio_path_or_none(track)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # --- Engine DJ -----------------------------------------------------
    if targets.get("engine"):
        if is_engine_dj_running():
            results["engine"] = {"ok": False, "message": "Engine DJ is running. Close it first."}
        elif not track["quick_cues_blob"]:
            results["engine"] = {"ok": False, "message": "Track has no quickCues blob (analyze it in Engine DJ first)"}
        else:
            cue_data = decode_quick_cues(track["quick_cues_blob"])
            by_slot = {int(c["slot"]): c for c in cues}
            for idx in range(len(cue_data["cues"])):
                slot = idx + 1
                if slot in by_slot:
                    c = by_slot[slot]
                    a, r, g, b = ENGINE_COLORS[c.get("color_name", "yellow").lower()]
                    cue_data["cues"][idx] = {
                        "index": idx, "label": c.get("label", ""),
                        "position_samples": float(c["time_seconds"]) * sample_rate,
                        "color_a": a, "color_r": r, "color_g": g, "color_b": b,
                    }
                elif clear_missing:
                    cue_data["cues"][idx] = {
                        "index": idx, "label": "",
                        "position_samples": CUE_POSITION_EMPTY,
                        "color_a": 0, "color_r": 0, "color_g": 0, "color_b": 0,
                    }
            backup = backup_library(_db_path)
            wdb = _conn(readonly=False)
            try:
                write_quick_cues(wdb, track["id"], encode_quick_cues(cue_data))
            finally:
                wdb.close()
            results["engine"] = {"ok": True, "message": f"Wrote {len(cues)} cues",
                                 "backup": str(backup)}

    # --- Serato tags in the audio file (also read by djay Pro) ----------
    if targets.get("serato"):
        if audio_path is None:
            results["serato"] = {"ok": False, "message": "Audio file not found"}
        else:
            try:
                from autocue.exporters import serato
                scues = []
                for c in cues:
                    a, r, g, b = ENGINE_COLORS[c.get("color_name", "yellow").lower()]
                    scues.append(serato.SeratoCue(
                        index=int(c["slot"]) - 1,
                        position_ms=int(round(float(c["time_seconds"]) * 1000)),
                        color=(r, g, b), name=c.get("label", "")))
                previous = serato.write_cues(audio_path, scues)
                msg = f"Wrote {len(scues)} cues to {audio_path.name}"
                res = {"ok": True, "message": msg}
                if previous is not None:
                    bpath = _backups_dir() / f"serato_{track['id']}_{stamp}.bin"
                    bpath.write_bytes(previous)
                    res["backup"] = str(bpath)
                results["serato"] = res
            except Exception as e:
                results["serato"] = {"ok": False, "message": str(e)}

    # --- VirtualDJ database.xml ------------------------------------------
    if targets.get("vdj"):
        from autocue.exporters import vdj
        db = _vdj_db_path or vdj.find_database()
        if db is None:
            results["vdj"] = {"ok": False, "message": "VirtualDJ database.xml not found"}
        elif audio_path is None:
            results["vdj"] = {"ok": False, "message": "Audio file not found"}
        elif vdj.is_virtualdj_running():
            results["vdj"] = {"ok": False, "message": "VirtualDJ is running. Close it first."}
        else:
            try:
                vcues = []
                for c in cues:
                    a, r, g, b = ENGINE_COLORS[c.get("color_name", "yellow").lower()]
                    vcues.append({"num": int(c["slot"]), "seconds": float(c["time_seconds"]),
                                  "name": c.get("label", ""), "color": (r, g, b)})
                res = vdj.write_cues(db, str(audio_path), vcues)
                results["vdj"] = {"ok": True, "backup": res["backup"],
                                  "message": f"Wrote {res['written']} cues"
                                             + (" (new entry)" if res["created_song"] else "")}
            except Exception as e:
                results["vdj"] = {"ok": False, "message": str(e)}

    return jsonify({"results": results})


def run_server(db_path: str, host: str = "127.0.0.1", port: int = 5555,
               vdj_db: str | None = None):
    global _vdj_db_path
    _vdj_db_path = vdj_db
    set_db_path(db_path)
    conn = _conn()
    try:
        version = check_schema(conn)
        print(f"Schema: {version}")
    finally:
        conn.close()
    print(f"Review workflow: http://{host}:{port}")
    print(f"Cue editor:      http://{host}:{port}/editor")
    app.run(host=host, port=port, debug=False)
