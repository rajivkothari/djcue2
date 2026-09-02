import argparse
import sys

from autocue.db import (
    open_library, list_tracks, check_schema,
    is_engine_dj_running, backup_library, write_quick_cues,
    resolve_audio_path,
    list_playlists, list_crates, get_playlist_tracks, get_crate_tracks,
)
from autocue.codec import (
    decode_quick_cues, encode_quick_cues,
    decode_beat_data, decode_track_data,
    is_cue_active, snap_to_downbeat, get_downbeat_positions,
    get_beat_positions, get_samples_per_beat, get_main_cue,
    CUE_POSITION_EMPTY,
)
from autocue.anchor import pick_anchor, resolve_bar_position, ANCHOR_MODES


ENGINE_COLORS = {
    "yellow":  (255, 0xEA, 0xC5, 0x32),
    "orange":  (255, 0xEA, 0x8F, 0x32),
    "purple":  (255, 0xB8, 0x55, 0xBF),
    "red":     (255, 0xBA, 0x2A, 0x41),
    "green":   (255, 0x86, 0xC6, 0x4B),
    "teal":    (255, 0x20, 0xC6, 0x7C),
    "cyan":    (255, 0x00, 0xA8, 0xB1),
    "blue":    (255, 0x15, 0x8E, 0xE2),
}

DEFAULT_CUE_COLORS = {
    1: "yellow", 2: "orange", 3: "purple", 4: "red",
    5: "green", 6: "teal", 7: "cyan", 8: "blue",
}


def _get_sample_rate(track: dict) -> float:
    if track["beat_data_blob"]:
        return decode_beat_data(track["beat_data_blob"])["sample_rate"]
    if track["track_data_blob"]:
        return decode_track_data(track["track_data_blob"])["sample_rate"]
    return 44100.0


def _parse_time(time_str: str) -> float:
    """Parse time string like '1:32.5' or '92.5' into seconds."""
    if ':' in time_str:
        parts = time_str.split(':')
        if len(parts) == 2:
            return float(parts[0]) * 60 + float(parts[1])
        elif len(parts) == 3:
            return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
    return float(time_str)


def _format_time(seconds: float) -> str:
    minutes = int(seconds // 60)
    secs = seconds % 60
    return f"{minutes}:{secs:05.2f}"


def _ai_detector(audio_path):
    """Zero-arg callable that runs Beat This! on demand.

    Deferred so the model only loads (and the checkpoint only downloads)
    for tracks whose anchor mode actually needs it.
    """
    def _detect():
        if audio_path is None:
            raise FileNotFoundError("audio file not found")
        from autocue.beats import detect_first_downbeat
        return detect_first_downbeat(str(audio_path))
    return _detect


def _describe_anchor(picked: dict, sample_rate: float) -> str:
    parts = []
    for name in ("main_cue", "ai", "grid"):
        v = picked["candidates"].get(name)
        if v is not None:
            parts.append(f"{name.replace('_', ' ')}={_format_time(v / sample_rate)}")
    s = f"anchor: {picked['source']} [{', '.join(parts)}]"
    if picked["note"]:
        s += f" ({picked['note']})"
    return s


def cmd_inspect(args):
    db = open_library(args.db, readonly=True)
    try:
        version = check_schema(db)
    except RuntimeError as e:
        print(f"ERROR: {e}")
        sys.exit(1)
    print(f"Schema version: {version}")

    tracks = list_tracks(db, search=args.track)
    if not tracks:
        print(f"No tracks matching '{args.track}'")
        sys.exit(1)

    for track in tracks:
        sample_rate = _get_sample_rate(track)

        print(f"\n{'='*60}")
        print(f"Track: {track['title']}")
        print(f"Artist: {track['artist']}")
        print(f"BPM: {track['bpm']}")
        print(f"ID: {track['id']}")
        print(f"Sample rate: {sample_rate}")

        if track["quick_cues_blob"] is None:
            print("  (no quickCues data)")
            continue

        data = decode_quick_cues(track["quick_cues_blob"])
        active_cues = [c for c in data["cues"] if is_cue_active(c)]

        if not active_cues:
            print("  (no cues set)")
            continue

        for cue in active_cues:
            pos_sec = cue["position_samples"] / sample_rate
            color_hex = (f"#{cue['color_r']:02x}{cue['color_g']:02x}"
                         f"{cue['color_b']:02x}")
            label = cue["label"] or ""
            print(f"  Cue {cue['index'] + 1:>2}: {_format_time(pos_sec)}  "
                  f"color={color_hex}  label=\"{label}\"")

    db.close()


def cmd_roundtrip(args):
    db = open_library(args.db, readonly=True)
    try:
        version = check_schema(db)
    except RuntimeError as e:
        print(f"ERROR: {e}")
        sys.exit(1)
    print(f"Schema version: {version}")

    tracks = list_tracks(db, limit=args.count)
    passed = 0
    failed = 0
    skipped = 0

    for track in tracks:
        blob = track["quick_cues_blob"]
        if blob is None:
            skipped += 1
            continue

        try:
            data = decode_quick_cues(blob)
            re_encoded = encode_quick_cues(data, original_blob=blob)
        except Exception as e:
            failed += 1
            print(f"FAIL: {track['title']} (id={track['id']}): {e}")
            continue

        if re_encoded == blob:
            passed += 1
        else:
            failed += 1
            print(f"FAIL: {track['title']} (id={track['id']})")
            print(f"  original:   {len(blob)} bytes")
            print(f"  re-encoded: {len(re_encoded)} bytes")

    print(f"\nRound-trip results: {passed} passed, {failed} failed, "
          f"{skipped} skipped (no cue data)")
    db.close()
    if failed:
        sys.exit(1)


def cmd_set(args):
    if is_engine_dj_running():
        print("ERROR: Engine DJ is running. Close it before writing cues.")
        sys.exit(1)

    cue_index = args.cue - 1
    if cue_index < 0 or cue_index > 7:
        print("ERROR: Cue number must be 1–8.")
        sys.exit(1)

    color_name = args.color.lower()
    if color_name not in ENGINE_COLORS:
        print(f"ERROR: Unknown color '{args.color}'. "
              f"Available: {', '.join(ENGINE_COLORS.keys())}")
        sys.exit(1)
    color_a, color_r, color_g, color_b = ENGINE_COLORS[color_name]

    time_seconds = _parse_time(args.at)
    label = args.label or ""

    db = open_library(args.db, readonly=True)
    try:
        version = check_schema(db)
    except RuntimeError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    tracks = list_tracks(db, search=args.track)
    db.close()

    if not tracks:
        print(f"No tracks matching '{args.track}'")
        sys.exit(1)
    if len(tracks) > 1:
        print(f"Multiple tracks match '{args.track}'. Use track ID instead:")
        for t in tracks:
            print(f"  ID {t['id']}: {t['title']} — {t['artist']}")
        sys.exit(1)

    track = tracks[0]
    sample_rate = _get_sample_rate(track)
    position_samples = time_seconds * sample_rate

    if track["beat_data_blob"]:
        beat_data = decode_beat_data(track["beat_data_blob"])
        downbeats = get_downbeat_positions(beat_data)
        if downbeats:
            snapped = snap_to_downbeat(position_samples, downbeats)
            snap_diff = abs(snapped - position_samples) / sample_rate
            if snap_diff > 0.001:
                print(f"Snapping to nearest downbeat: "
                      f"{_format_time(time_seconds)} -> "
                      f"{_format_time(snapped / sample_rate)} "
                      f"(moved {snap_diff:.3f}s)")
            position_samples = snapped

    if track["quick_cues_blob"] is None:
        print(f"ERROR: Track has no quickCues blob. "
              f"Analyze it in Engine DJ first.")
        sys.exit(1)

    data = decode_quick_cues(track["quick_cues_blob"])

    existing = data["cues"][cue_index]
    if is_cue_active(existing) and not args.overwrite:
        pos_sec = existing["position_samples"] / sample_rate
        print(f"ERROR: Cue {args.cue} already set at {_format_time(pos_sec)} "
              f"label=\"{existing['label']}\". Use --overwrite to replace.")
        sys.exit(1)

    data["cues"][cue_index] = {
        "index": cue_index,
        "label": label,
        "position_samples": position_samples,
        "color_a": color_a,
        "color_r": color_r,
        "color_g": color_g,
        "color_b": color_b,
    }

    new_blob = encode_quick_cues(data)

    print(f"\nTrack: {track['title']}")
    print(f"Artist: {track['artist']}")
    print(f"Setting cue {args.cue}: {_format_time(position_samples / sample_rate)}  "
          f"color={color_name}  label=\"{label}\"")

    if args.dry_run:
        print("\n(dry run — no changes written)")
        return

    backup_path = backup_library(args.db)
    print(f"Backup: {backup_path}")

    db = open_library(args.db, readonly=False)
    write_quick_cues(db, track["id"], new_blob)
    db.close()

    print("Cue written. Open Engine DJ and verify.")


def cmd_analyze(args):
    try:
        from autocue.analysis import analyze_structure
    except ImportError as e:
        print(f"ERROR: {e}")
        sys.exit(1)
    from autocue.templates import load_template

    db = open_library(args.db, readonly=True)
    try:
        version = check_schema(db)
    except RuntimeError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    tracks = list_tracks(db, search=args.track)
    db.close()

    if not tracks:
        print(f"No tracks matching '{args.track}'")
        sys.exit(1)
    if len(tracks) > 1:
        print(f"Multiple tracks match '{args.track}'. Use track ID instead:")
        for t in tracks:
            print(f"  ID {t['id']}: {t['title']} — {t['artist']}")
        sys.exit(1)

    track = tracks[0]
    print(f"Track: {track['title']}")
    print(f"Artist: {track['artist']}")
    print(f"ID: {track['id']}")

    try:
        template = load_template(args.template, user_dir=args.template_dir)
    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    print(f"Template: {template.get('name', args.template)}")

    if not track["path"]:
        print("ERROR: Track has no file path in database.")
        sys.exit(1)

    try:
        audio_path = resolve_audio_path(args.db, track["path"])
    except FileNotFoundError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    print(f"Audio: {audio_path}")
    print("Analyzing...")

    analysis_params = template.get("analysis", {})
    result = analyze_structure(str(audio_path), **analysis_params)

    analysis_sr = result["sample_rate"]
    sample_rate = _get_sample_rate(track)

    sr_scale = sample_rate / analysis_sr if analysis_sr != sample_rate else 1.0

    downbeats = []
    beats = []
    samples_per_beat = None
    if track["beat_data_blob"]:
        beat_data = decode_beat_data(track["beat_data_blob"])
        downbeats = get_downbeat_positions(beat_data)
        beats = get_beat_positions(beat_data)
        samples_per_beat = get_samples_per_beat(beat_data)

    if track["quick_cues_blob"] is None:
        print("ERROR: Track has no quickCues blob. "
              "Analyze it in Engine DJ first.")
        sys.exit(1)

    cue_data = decode_quick_cues(track["quick_cues_blob"])
    template_cues = template["cues"]

    beat_offset = getattr(args, "beat_offset", 0)
    picked = pick_anchor(
        getattr(args, "anchor", "auto"), main_cue=get_main_cue(cue_data),
        downbeats=downbeats, beats=beats, samples_per_beat=samples_per_beat,
        sample_rate=sample_rate, detect_first_downbeat=_ai_detector(audio_path))
    anchor = picked["anchor"]
    print(f"Bar-1 {_describe_anchor(picked, sample_rate)}, "
          f"beat offset: {beat_offset}")

    proposed = []
    skipped_existing = []

    print(f"\n{'Cue':<6} {'Time':<10} {'Label':<14} {'Color':<8} {'Confidence':<12} {'Note'}")
    print("-" * 70)

    for slot_key, cue_def in sorted(template_cues.items(), key=lambda x: int(x[0])):
        slot = int(slot_key)
        cue_index = slot - 1
        detect_key = cue_def["detect"]
        label = cue_def.get("label", "")
        color_name = cue_def.get("color", DEFAULT_CUE_COLORS.get(slot, "yellow"))
        is_optional = cue_def.get("optional", False)

        bar_result = resolve_bar_position(
            detect_key, anchor, samples_per_beat, beats, beat_offset=beat_offset)
        if bar_result is not None:
            position_samples, confidence = bar_result
            if position_samples is None:
                note = "NO BEAT GRID"
                print(f"  {slot:<4} {'—':<10} {label:<14} {color_name:<8} "
                      f"{'—':<12} {note}")
                continue
        else:
            raw_pos = result["positions"].get(detect_key)
            confidence = result["confidences"].get(detect_key, 0.0)

            if raw_pos is None:
                note = "(optional — skipped)" if is_optional else "NOT DETECTED"
                print(f"  {slot:<4} {'—':<10} {label:<14} {color_name:<8} "
                      f"{'—':<12} {note}")
                continue

            position_samples = raw_pos * sr_scale

            if downbeats:
                position_samples = snap_to_downbeat(position_samples, downbeats)

        existing = cue_data["cues"][cue_index]
        note = ""
        if is_cue_active(existing):
            if not args.overwrite:
                old_time = _format_time(
                    existing["position_samples"] / sample_rate
                )
                note = f"EXISTS at {old_time} (use --overwrite)"
                skipped_existing.append(slot)
                print(f"  {slot:<4} "
                      f"{_format_time(position_samples / sample_rate):<10} "
                      f"{label:<14} {color_name:<8} "
                      f"{confidence:<12.0%} {note}")
                continue
            else:
                note = "(overwriting)"

        if confidence < 0.4:
            note += " LOW CONFIDENCE"

        time_str = _format_time(position_samples / sample_rate)
        print(f"  {slot:<4} {time_str:<10} {label:<14} {color_name:<8} "
              f"{confidence:<12.0%} {note}")

        color_a, color_r, color_g, color_b = ENGINE_COLORS[color_name.lower()]
        proposed.append({
            "slot": slot,
            "index": cue_index,
            "label": label,
            "position_samples": position_samples,
            "color_a": color_a,
            "color_r": color_r,
            "color_g": color_g,
            "color_b": color_b,
            "confidence": confidence,
        })

    if not proposed:
        print("\nNo cues to write.")
        return

    if args.dry_run:
        print(f"\n(dry run — {len(proposed)} cues proposed, not written)")
        return

    if is_engine_dj_running():
        print("\nERROR: Engine DJ is running. Close it before writing cues.")
        sys.exit(1)

    backup_path = backup_library(args.db)
    print(f"\nBackup: {backup_path}")

    for p in proposed:
        cue_data["cues"][p["index"]] = {
            "index": p["index"],
            "label": p["label"],
            "position_samples": p["position_samples"],
            "color_a": p["color_a"],
            "color_r": p["color_r"],
            "color_g": p["color_g"],
            "color_b": p["color_b"],
        }

    new_blob = encode_quick_cues(cue_data)

    db = open_library(args.db, readonly=False)
    write_quick_cues(db, track["id"], new_blob)
    db.close()

    print(f"Wrote {len(proposed)} cues. Open Engine DJ and verify.")
    if skipped_existing:
        print(f"Skipped slots with existing cues: "
              f"{', '.join(str(s) for s in skipped_existing)}")


def cmd_list_playlists(args):
    db = open_library(args.db, readonly=True)
    try:
        check_schema(db)
    except RuntimeError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    playlists = list_playlists(db)
    db.close()

    if not playlists:
        print("No playlists found.")
        return

    print(f"{'ID':<6} {'Tracks':<8} {'Title'}")
    print("-" * 50)
    for p in playlists:
        print(f"{p['id']:<6} {p['track_count']:<8} {p['title']}")


def cmd_list_crates(args):
    db = open_library(args.db, readonly=True)
    try:
        check_schema(db)
    except RuntimeError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    crates = list_crates(db)
    db.close()

    if not crates:
        print("No crates found.")
        return

    print(f"{'ID':<6} {'Tracks':<8} {'Title'}")
    print("-" * 50)
    for c in crates:
        print(f"{c['id']:<6} {c['track_count']:<8} {c['title']}")


def cmd_serve(args):
    try:
        from autocue.server import run_server
    except ImportError:
        print("ERROR: GUI requires Flask. Install with: pip install autocue[gui]")
        sys.exit(1)
    run_server(args.db, host=args.host, port=args.port)


def cmd_batch(args):
    try:
        from autocue.analysis import analyze_structure
    except ImportError as e:
        print(f"ERROR: {e}")
        sys.exit(1)
    from autocue.templates import load_template

    db = open_library(args.db, readonly=True)
    try:
        check_schema(db)
    except RuntimeError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    if args.playlist:
        tracks = get_playlist_tracks(db, args.playlist)
        source = f"playlist '{args.playlist}'"
    elif args.crate:
        tracks = get_crate_tracks(db, args.crate)
        source = f"crate '{args.crate}'"
    else:
        print("ERROR: specify --playlist or --crate")
        sys.exit(1)
    db.close()

    if not tracks:
        print(f"No tracks found in {source}.")
        sys.exit(1)

    try:
        template = load_template(args.template, user_dir=args.template_dir)
    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    print(f"Source: {source} ({len(tracks)} tracks)")
    print(f"Template: {template.get('name', args.template)}")

    if not args.dry_run and is_engine_dj_running():
        print("ERROR: Engine DJ is running. Close it before writing cues.")
        sys.exit(1)

    backup_path = None
    if not args.dry_run:
        backup_path = backup_library(args.db)
        print(f"Backup: {backup_path}")

    analysis_params = template.get("analysis", {})
    template_cues = template["cues"]

    needs_analysis = any(
        not cue_def["detect"].startswith("bar_")
        for cue_def in template_cues.values()
    )

    stats = {"processed": 0, "cues_written": 0, "skipped": 0,
             "errors": 0, "low_confidence": [], "anchors": {}}

    for i, track in enumerate(tracks):
        prefix = f"[{i+1}/{len(tracks)}]"

        existing_cues_blob = track["quick_cues_blob"]
        if existing_cues_blob:
            cue_data = decode_quick_cues(existing_cues_blob)
            active = [c for c in cue_data["cues"] if is_cue_active(c)]
            if active and not args.overwrite:
                print(f"{prefix} SKIP {track['title']} — "
                      f"{len(active)} existing cues (use --overwrite)")
                stats["skipped"] += 1
                continue

        if not track["path"]:
            print(f"{prefix} ERROR {track['title']} — no file path")
            stats["errors"] += 1
            continue

        if track["beat_data_blob"]:
            bd = decode_beat_data(track["beat_data_blob"])
            dur_secs = bd["total_samples"] / bd["sample_rate"] if bd["sample_rate"] > 0 else 0
            if dur_secs > args.max_duration:
                print(f"{prefix} SKIP {track['title']} — "
                      f"{_format_time(dur_secs)} exceeds {args.max_duration // 60:.0f}min limit")
                stats["skipped"] += 1
                continue
            if dur_secs < 30:
                print(f"{prefix} SKIP {track['title']} — "
                      f"too short ({dur_secs:.0f}s)")
                stats["skipped"] += 1
                continue

        existing_cues_blob = track["quick_cues_blob"]
        if not existing_cues_blob:
            print(f"{prefix} SKIP {track['title']} — no quickCues blob")
            stats["skipped"] += 1
            continue

        downbeats = []
        beats = []
        samples_per_beat = None
        if track["beat_data_blob"]:
            beat_data = decode_beat_data(track["beat_data_blob"])
            downbeats = get_downbeat_positions(beat_data)
            beats = get_beat_positions(beat_data)
            samples_per_beat = get_samples_per_beat(beat_data)

        if not beats or samples_per_beat is None:
            print(f"{prefix} SKIP {track['title']} — no beat grid")
            stats["skipped"] += 1
            continue

        cue_data = decode_quick_cues(existing_cues_blob)
        sample_rate = _get_sample_rate(track)

        try:
            audio_path = resolve_audio_path(args.db, track["path"])
        except FileNotFoundError:
            audio_path = None

        picked = pick_anchor(
            args.anchor, main_cue=get_main_cue(cue_data),
            downbeats=downbeats, beats=beats,
            samples_per_beat=samples_per_beat, sample_rate=sample_rate,
            detect_first_downbeat=_ai_detector(audio_path))
        anchor = picked["anchor"]
        if anchor is None:
            print(f"{prefix} SKIP {track['title']} — no usable bar-1 anchor "
                  f"({picked['note'] or 'no main cue, AI result, or grid'})")
            stats["skipped"] += 1
            continue

        result = None

        if needs_analysis:
            if audio_path is None:
                print(f"{prefix} ERROR {track['title']} — audio file not found")
                stats["errors"] += 1
                continue

            print(f"{prefix} Analyzing {track['title']} "
                  f"({_describe_anchor(picked, sample_rate)})...",
                  end="", flush=True)

            try:
                result = analyze_structure(str(audio_path), **analysis_params)
            except Exception as e:
                print(f" ERROR: {e}")
                stats["errors"] += 1
                continue

            analysis_sr = result["sample_rate"]
            sr_scale = sample_rate / analysis_sr if analysis_sr != sample_rate else 1.0
        else:
            print(f"{prefix} {track['title']} "
                  f"({_describe_anchor(picked, sample_rate)})...",
                  end="", flush=True)

        proposed = []
        track_low_conf = False

        for slot_key, cue_def in sorted(template_cues.items(),
                                        key=lambda x: int(x[0])):
            slot = int(slot_key)
            cue_index = slot - 1
            detect_key = cue_def["detect"]
            label = cue_def.get("label", "")
            color_name = cue_def.get("color",
                                     DEFAULT_CUE_COLORS.get(slot, "yellow"))
            is_optional = cue_def.get("optional", False)

            bar_result = resolve_bar_position(
                detect_key, anchor, samples_per_beat, beats,
                beat_offset=args.beat_offset)
            if bar_result is not None:
                position_samples, confidence = bar_result
                if position_samples is None:
                    print(f" [cue {slot}: {detect_key} unresolved]", end="")
                    continue
            else:
                if result is None:
                    continue
                raw_pos = result["positions"].get(detect_key)
                confidence = result["confidences"].get(detect_key, 0.0)

                if raw_pos is None:
                    continue

                position_samples = raw_pos * sr_scale
                if downbeats:
                    position_samples = snap_to_downbeat(position_samples,
                                                        downbeats)

            existing = cue_data["cues"][cue_index]
            if is_cue_active(existing) and not args.overwrite:
                continue

            if confidence < 0.4:
                track_low_conf = True

            color_a, color_r, color_g, color_b = ENGINE_COLORS[
                color_name.lower()
            ]
            proposed.append({
                "index": cue_index,
                "label": label,
                "position_samples": position_samples,
                "color_a": color_a,
                "color_r": color_r,
                "color_g": color_g,
                "color_b": color_b,
            })

        if not proposed:
            print(" no cues to set")
            continue

        if track_low_conf:
            stats["low_confidence"].append(track["title"])

        if args.dry_run:
            print(f" {len(proposed)} cues proposed")
        else:
            for p in proposed:
                cue_data["cues"][p["index"]] = p

            new_blob = encode_quick_cues(cue_data)
            wdb = open_library(args.db, readonly=False)
            write_quick_cues(wdb, track["id"], new_blob)
            wdb.close()
            print(f" {len(proposed)} cues written")
            stats["cues_written"] += len(proposed)

        stats["processed"] += 1
        stats["anchors"][picked["source"]] = (
            stats["anchors"].get(picked["source"], 0) + 1)

    # Summary
    print(f"\n{'='*50}")
    print(f"Batch complete: {stats['processed']} processed, "
          f"{stats['skipped']} skipped, {stats['errors']} errors")
    if not args.dry_run:
        print(f"Cues written: {stats['cues_written']}")
        if backup_path:
            print(f"Backup: {backup_path}")
    if stats["anchors"]:
        print("Bar-1 anchors: " + ", ".join(
            f"{k} {v}" for k, v in stats["anchors"].items()))
    if stats["low_confidence"]:
        print(f"\nLow-confidence tracks (review manually):")
        for t in stats["low_confidence"]:
            print(f"  - {t}")


def main():
    parser = argparse.ArgumentParser(
        prog="autocue",
        description="Auto-cue hot cue placement for Engine DJ libraries",
    )
    parser.add_argument(
        "--db",
        default="~/Music/Engine Library/Database2/m.db",
        help="Path to Engine DJ m.db",
    )

    _DB_HELP = "Path to Engine DJ m.db"
    _DB_DEFAULT = "~/Music/Engine Library/Database2/m.db"

    sub = parser.add_subparsers(dest="command", required=True)

    p_inspect = sub.add_parser("inspect", help="Inspect cues on a track")
    p_inspect.add_argument("--db", default=argparse.SUPPRESS, help=_DB_HELP)
    p_inspect.add_argument("track", help="Track title, artist, or ID to search")
    p_inspect.set_defaults(func=cmd_inspect)

    p_rt = sub.add_parser("roundtrip", help="Round-trip decode/re-encode test")
    p_rt.add_argument("--db", default=argparse.SUPPRESS, help=_DB_HELP)
    p_rt.add_argument(
        "--count", type=int, default=50,
        help="Number of tracks to test (default: 50)",
    )
    p_rt.set_defaults(func=cmd_roundtrip)

    p_set = sub.add_parser("set", help="Set a hot cue on a track")
    p_set.add_argument("--db", default=argparse.SUPPRESS, help=_DB_HELP)
    p_set.add_argument("track", help="Track title or ID (must match exactly one)")
    p_set.add_argument("--cue", type=int, required=True, help="Cue number (1–8)")
    p_set.add_argument("--at", required=True, help="Position as m:ss.s or seconds")
    p_set.add_argument("--label", default="", help="Cue label text")
    p_set.add_argument(
        "--color", default=None,
        help=f"Cue color ({', '.join(ENGINE_COLORS.keys())})",
    )
    p_set.add_argument("--overwrite", action="store_true",
                       help="Overwrite existing cue at this slot")
    p_set.add_argument("--dry-run", action="store_true",
                       help="Show what would be written without writing")
    p_set.set_defaults(func=cmd_set)

    p_analyze = sub.add_parser("analyze",
                               help="Auto-detect and place cues on a track")
    p_analyze.add_argument("--db", default=argparse.SUPPRESS, help=_DB_HELP)
    p_analyze.add_argument("track",
                           help="Track title or ID (must match exactly one)")
    p_analyze.add_argument(
        "--template", default="edm",
        help="Cue template name or path to .yaml file (default: edm)",
    )
    p_analyze.add_argument("--template-dir", default=None,
                           help="Directory for user template overrides")
    p_analyze.add_argument("--dry-run", action="store_true",
                           help="Show proposed cues without writing")
    p_analyze.add_argument("--overwrite", action="store_true",
                           help="Overwrite existing cues in matched slots")
    p_analyze.add_argument("--beat-offset", type=int, default=0,
                           help="Shift all bar cues by N beats (can be negative)")
    p_analyze.add_argument("--anchor", choices=ANCHOR_MODES, default="auto",
                           help="How to find bar 1: auto (main cue, then AI "
                                "downbeat, then grid), main-cue, ai, or grid")
    p_analyze.set_defaults(func=cmd_analyze)

    p_lp = sub.add_parser("list-playlists", help="List Engine DJ playlists")
    p_lp.add_argument("--db", default=argparse.SUPPRESS, help=_DB_HELP)
    p_lp.set_defaults(func=cmd_list_playlists)

    p_lc = sub.add_parser("list-crates", help="List Engine DJ crates")
    p_lc.add_argument("--db", default=argparse.SUPPRESS, help=_DB_HELP)
    p_lc.set_defaults(func=cmd_list_crates)

    p_serve = sub.add_parser("serve", help="Launch web GUI for visual cue review")
    p_serve.add_argument("--db", default=argparse.SUPPRESS, help=_DB_HELP)
    p_serve.add_argument("--host", default="127.0.0.1", help="Host (default: 127.0.0.1)")
    p_serve.add_argument("--port", type=int, default=5555, help="Port (default: 5555)")
    p_serve.set_defaults(func=cmd_serve)

    p_batch = sub.add_parser("batch",
                             help="Batch-process a playlist or crate")
    p_batch.add_argument("--db", default=argparse.SUPPRESS, help=_DB_HELP)
    group = p_batch.add_mutually_exclusive_group(required=True)
    group.add_argument("--playlist", help="Playlist name to process")
    group.add_argument("--crate", help="Crate name to process")
    p_batch.add_argument(
        "--template", default="edm",
        help="Cue template name or path to .yaml file (default: edm)",
    )
    p_batch.add_argument("--template-dir", default=None,
                         help="Directory for user template overrides")
    p_batch.add_argument("--dry-run", action="store_true",
                         help="Show proposed cues without writing")
    p_batch.add_argument("--overwrite", action="store_true",
                         help="Overwrite existing cues on tracks")
    p_batch.add_argument("--max-duration", type=int, default=900,
                         help="Skip tracks longer than N seconds (default: 900 = 15min)")
    p_batch.add_argument("--beat-offset", type=int, default=0,
                         help="Shift all bar cues by N beats to correct a "
                              "systematic grid phase error (can be negative)")
    p_batch.add_argument("--anchor", choices=ANCHOR_MODES, default="auto",
                         help="How to find bar 1: auto (main cue, then AI "
                              "downbeat, then grid), main-cue, ai, or grid. "
                              "'ai' needs: pip install autocue[beats]")
    p_batch.set_defaults(func=cmd_batch)

    args = parser.parse_args()
    if not hasattr(args, 'db'):
        args.db = _DB_DEFAULT
    args.db = str(__import__("pathlib").Path(args.db).expanduser())

    if hasattr(args, 'color') and args.color is None and hasattr(args, 'cue'):
        default_color = DEFAULT_CUE_COLORS.get(args.cue, "yellow")
        args.color = default_color

    args.func(args)


if __name__ == "__main__":
    main()
