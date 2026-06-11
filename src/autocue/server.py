"""Flask web app for visual cue review workflow."""

import json
import mimetypes
from pathlib import Path

from flask import Flask, jsonify, request, send_file, send_from_directory

from autocue.codec import (
    decode_quick_cues, encode_quick_cues, decode_beat_data,
    is_cue_active, snap_to_downbeat, get_downbeat_positions,
    CUE_POSITION_EMPTY,
)
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


def _resolve_bar_position(detect_key: str, downbeats: list[float]):
    if not detect_key.startswith("bar_"):
        return None
    bar_num = int(detect_key.split("_")[1])
    if bar_num < len(downbeats):
        return float(downbeats[bar_num]), 1.0
    return None, 0.0


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

    try:
        from autocue.analysis import analyze_structure
    except ImportError as e:
        return jsonify({"error": str(e)}), 500

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

    if not track["path"]:
        return jsonify({"error": "No file path for track"}), 400

    try:
        audio_path = resolve_audio_path(_db_path, track["path"])
    except FileNotFoundError:
        return jsonify({"error": "Audio file not found"}), 404

    result = analyze_structure(str(audio_path), **analysis_params)

    analysis_sr = result["sample_rate"]
    sample_rate = get_sample_rate(track)
    sr_scale = sample_rate / analysis_sr if analysis_sr != sample_rate else 1.0

    downbeats = []
    if track["beat_data_blob"]:
        beat_data = decode_beat_data(track["beat_data_blob"])
        downbeats = get_downbeat_positions(beat_data)

    template_cues = template["cues"]
    existing_cue_data = None
    if track["quick_cues_blob"]:
        existing_cue_data = decode_quick_cues(track["quick_cues_blob"])

    proposed = []
    for slot_key, cue_def in sorted(template_cues.items(), key=lambda x: int(x[0])):
        slot = int(slot_key)
        cue_index = slot - 1
        detect_key = cue_def["detect"]
        label = cue_def.get("label", "")
        color_name = cue_def.get("color", DEFAULT_CUE_COLORS.get(slot, "yellow"))
        is_optional = cue_def.get("optional", False)

        bar_pos = _resolve_bar_position(detect_key, downbeats)
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


def run_server(db_path: str, host: str = "127.0.0.1", port: int = 5555):
    set_db_path(db_path)
    conn = _conn()
    try:
        version = check_schema(conn)
        print(f"Schema: {version}")
    finally:
        conn.close()
    print(f"Starting at http://{host}:{port}")
    app.run(host=host, port=port, debug=False)
