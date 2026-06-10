import argparse
import sys

from autocue.db import open_library, list_tracks, check_schema
from autocue.codec import (
    decode_quick_cues, encode_quick_cues,
    decode_beat_data, decode_track_data,
    is_cue_active,
)


def _get_sample_rate(track: dict) -> float:
    """Extract sample rate from beatData or trackData blob."""
    if track["beat_data_blob"]:
        return decode_beat_data(track["beat_data_blob"])["sample_rate"]
    if track["track_data_blob"]:
        return decode_track_data(track["track_data_blob"])["sample_rate"]
    return 44100.0


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
            minutes = int(pos_sec // 60)
            seconds = pos_sec % 60
            color_hex = (f"#{cue['color_r']:02x}{cue['color_g']:02x}"
                         f"{cue['color_b']:02x}")
            label = cue["label"] or ""
            print(f"  Cue {cue['index'] + 1:>2}: {minutes}:{seconds:05.2f}  "
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

    args = parser.parse_args()
    args.db = str(__import__("pathlib").Path(args.db).expanduser())
    args.func(args)


if __name__ == "__main__":
    main()
