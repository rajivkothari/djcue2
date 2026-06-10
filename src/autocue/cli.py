import argparse
import sys

from autocue.db import open_library, list_tracks, get_schema_version
from autocue.codec import decode_quick_cues, encode_quick_cues


TESTED_SCHEMA_VERSIONS = {"1.18.0", "1.18.1", "1.19.0", "1.20.0", "1.21.0"}


def cmd_inspect(args):
    db = open_library(args.db, readonly=True)
    version = get_schema_version(db)
    if version not in TESTED_SCHEMA_VERSIONS:
        print(f"ERROR: untested schema version '{version}'. "
              f"Tested versions: {', '.join(sorted(TESTED_SCHEMA_VERSIONS))}")
        sys.exit(1)

    tracks = list_tracks(db, search=args.track)
    if not tracks:
        print(f"No tracks matching '{args.track}'")
        sys.exit(1)

    for track in tracks:
        print(f"\n{'='*60}")
        print(f"Track: {track['title']}")
        print(f"Artist: {track['artist']}")
        print(f"BPM: {track['bpm']}")
        print(f"ID: {track['id']}")

        if track["quick_cues_blob"] is None:
            print("  (no quickCues data)")
            continue

        cues = decode_quick_cues(track["quick_cues_blob"])
        if not cues:
            print("  (no cues set)")
            continue

        for cue in cues:
            pos_sec = cue["position_samples"] / track["sample_rate"] if track["sample_rate"] else cue["position_samples"]
            minutes = int(pos_sec // 60)
            seconds = pos_sec % 60
            color_str = f"#{cue['color_r']:02x}{cue['color_g']:02x}{cue['color_b']:02x}{cue['color_a']:02x}"
            label = cue["label"] or ""
            print(f"  Cue {cue['index']:>2}: {minutes}:{seconds:05.2f}  "
                  f"color={color_str}  label=\"{label}\"")

    db.close()


def cmd_roundtrip(args):
    db = open_library(args.db, readonly=True)
    version = get_schema_version(db)
    if version not in TESTED_SCHEMA_VERSIONS:
        print(f"ERROR: untested schema version '{version}'.")
        sys.exit(1)

    tracks = list_tracks(db, limit=args.count)
    passed = 0
    failed = 0
    skipped = 0

    for track in tracks:
        blob = track["quick_cues_blob"]
        if blob is None:
            skipped += 1
            continue

        cues = decode_quick_cues(blob)
        re_encoded = encode_quick_cues(cues, original_blob=blob)
        if re_encoded == blob:
            passed += 1
        else:
            failed += 1
            print(f"FAIL: {track['title']} (id={track['id']})")
            print(f"  original:   {len(blob)} bytes")
            print(f"  re-encoded: {len(re_encoded)} bytes")

    print(f"\nRound-trip results: {passed} passed, {failed} failed, {skipped} skipped (no cue data)")
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
        help="Path to Engine DJ m.db (default: ~/Music/Engine Library/Database2/m.db)",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    p_inspect = sub.add_parser("inspect", help="Inspect cues on a track")
    p_inspect.add_argument("track", help="Track title or ID to inspect")
    p_inspect.set_defaults(func=cmd_inspect)

    p_rt = sub.add_parser("roundtrip", help="Round-trip test: decode and re-encode quickCues blobs")
    p_rt.add_argument("--count", type=int, default=50, help="Number of tracks to test (default: 50)")
    p_rt.set_defaults(func=cmd_roundtrip)

    args = parser.parse_args()
    args.db = args.db.replace("~", str(__import__("pathlib").Path.home()))
    args.func(args)


if __name__ == "__main__":
    main()
