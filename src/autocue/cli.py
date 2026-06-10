import argparse
import sys

from autocue.db import (
    open_library, list_tracks, check_schema,
    is_engine_dj_running, backup_library, write_quick_cues,
)
from autocue.codec import (
    decode_quick_cues, encode_quick_cues,
    decode_beat_data, decode_track_data,
    is_cue_active, snap_to_downbeat, get_downbeat_positions,
    CUE_POSITION_EMPTY,
)


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

    sub = parser.add_subparsers(dest="command", required=True)

    p_inspect = sub.add_parser("inspect", help="Inspect cues on a track")
    p_inspect.add_argument("track", help="Track title, artist, or ID to search")
    p_inspect.set_defaults(func=cmd_inspect)

    p_rt = sub.add_parser("roundtrip", help="Round-trip decode/re-encode test")
    p_rt.add_argument(
        "--count", type=int, default=50,
        help="Number of tracks to test (default: 50)",
    )
    p_rt.set_defaults(func=cmd_roundtrip)

    p_set = sub.add_parser("set", help="Set a hot cue on a track")
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

    args = parser.parse_args()
    args.db = str(__import__("pathlib").Path(args.db).expanduser())

    if hasattr(args, 'color') and args.color is None and hasattr(args, 'cue'):
        default_color = DEFAULT_CUE_COLORS.get(args.cue, "yellow")
        args.color = default_color

    args.func(args)


if __name__ == "__main__":
    main()
