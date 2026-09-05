"""Read/write Serato cue points in audio file tags ("Serato Markers2").

Format reference: github.com/Holzhaus/serato-tags (docs/serato_markers2.md),
cross-checked against Mixxx's src/track/serato/markers2.cpp.

The tag is the de-facto cross-app cue format: Serato DJ writes and reads
it, djay Pro imports cue points from it, VirtualDJ can import it. Only
the tag block of the file is rewritten (mutagen never touches audio
frames), and the previous tag bytes are returned so callers can back
them up.

Container placement:
  MP3          ID3 GEOB frame, desc "Serato Markers2", data = tag bytes
  AIFF / WAV   same GEOB frame, inside the container's ID3 chunk
  MP4 / M4A    atom ----:com.serato.dj:markersv2, base64 of
               GEOB_PREFIX + tag bytes, newline every 72 chars
  FLAC         Vorbis comment SERATO_MARKERS_V2, same as MP4 but base64
               without '=' padding (best effort — not verified against
               a real Serato install)
"""

import base64
import struct
from dataclasses import dataclass
from pathlib import Path

TAG_HEADER = b"\x01\x01"
PAYLOAD_HEADER = b"\x01\x01"
MIN_TAG_LENGTH = 470
B64_LINE = 72

GEOB_DESC = "Serato Markers2"
GEOB_MIME = "application/octet-stream"
# Mirrors an ID3 GEOB body without its encoding byte:
#   mime NUL, (empty filename) NUL, description NUL, data
GEOB_PREFIX = GEOB_MIME.encode() + b"\x00\x00" + GEOB_DESC.encode() + b"\x00"
MP4_ATOM = "----:com.serato.dj:markersv2"
MP4_LEGACY_ATOM = "----:com.serato.dj:markers"
FLAC_KEY = "SERATO_MARKERS_V2"
FLAC_LEGACY_KEY = "SERATO_MARKERS"

# Serato DJ prefers the legacy fixed-slot "Serato Markers_" frame when
# both are present. We remove it so the Markers2 we write is authoritative;
# Serato regenerates it on its next save.
LEGACY_GEOB_DESC = "Serato Markers_"

_CUE_FMT = ">cBIc3s2s"          # pad, index, position_ms, pad, rgb, pad
_CUE_FIXED = struct.calcsize(_CUE_FMT)

MP3_LIKE = {".mp3", ".aif", ".aiff", ".wav"}
MP4_LIKE = {".m4a", ".mp4", ".aac"}
FLAC_LIKE = {".flac"}
SUPPORTED = MP3_LIKE | MP4_LIKE | FLAC_LIKE


@dataclass
class SeratoCue:
    index: int                  # 0-based hot cue slot (0..7)
    position_ms: int
    color: tuple[int, int, int]
    name: str = ""


# ---------------------------------------------------------------------------
# Payload (the binary inside the base64)
# ---------------------------------------------------------------------------

def parse_payload(payload: bytes) -> list[tuple[str, bytes]]:
    """Split a decoded Markers2 payload into (entry_name, entry_data) pairs.

    Unknown entry types (LOOP, FLIP, …) are kept verbatim so a rewrite
    never drops data we don't understand.
    """
    if payload[:2] != PAYLOAD_HEADER:
        raise ValueError("Bad Serato Markers2 payload header")
    entries = []
    pos = 2
    while pos < len(payload):
        null = payload.find(b"\x00", pos)
        if null == -1 or null == pos:        # trailing terminator
            break
        name = payload[pos:null].decode("ascii")
        pos = null + 1
        if pos + 4 > len(payload):
            break
        (length,) = struct.unpack_from(">I", payload, pos)
        pos += 4
        entries.append((name, payload[pos:pos + length]))
        pos += length
    return entries


def build_payload(entries: list[tuple[str, bytes]]) -> bytes:
    out = [PAYLOAD_HEADER]
    for name, data in entries:
        out.append(name.encode("ascii") + b"\x00")
        out.append(struct.pack(">I", len(data)))
        out.append(data)
    out.append(b"\x00")
    return b"".join(out)


def encode_cue(cue: SeratoCue) -> bytes:
    r, g, b = cue.color
    fixed = struct.pack(_CUE_FMT, b"\x00", cue.index, int(cue.position_ms),
                        b"\x00", bytes((r, g, b)), b"\x00\x00")
    return fixed + cue.name.encode("utf-8") + b"\x00"


def decode_cue(data: bytes) -> SeratoCue:
    _, index, pos_ms, _, rgb, _ = struct.unpack_from(_CUE_FMT, data, 0)
    name = data[_CUE_FIXED:].split(b"\x00", 1)[0].decode("utf-8", "replace")
    return SeratoCue(index=index, position_ms=pos_ms,
                     color=(rgb[0], rgb[1], rgb[2]), name=name)


def cues_from_entries(entries) -> list[SeratoCue]:
    return [decode_cue(d) for n, d in entries if n == "CUE"]


def replace_cues(entries, cues: list[SeratoCue]) -> list[tuple[str, bytes]]:
    """Return entries with all CUE entries replaced by `cues` (sorted by
    index), everything else preserved in its original order."""
    kept = [(n, d) for n, d in entries if n != "CUE"]
    new_cues = [("CUE", encode_cue(c)) for c in sorted(cues, key=lambda c: c.index)]
    # Serato writes COLOR first, then CUEs, then LOOPs, then BPMLOCK.
    head = [e for e in kept if e[0] == "COLOR"]
    tail = [e for e in kept if e[0] != "COLOR"]
    return head + new_cues + tail


# ---------------------------------------------------------------------------
# Tag bytes (header + Serato's quirky base64)
# ---------------------------------------------------------------------------

def _wrap_lines(enc: bytes) -> bytes:
    enc = bytearray(enc)
    i = B64_LINE
    while i < len(enc):
        enc.insert(i, 0x0A)
        i += B64_LINE + 1
    return bytes(enc)


def _b64_serato(payload: bytes) -> bytes:
    """Serato replaces '=' padding with 'A' and wraps lines at 72 chars."""
    return _wrap_lines(base64.b64encode(payload).replace(b"=", b"A"))


def _b64_decode_lenient(b64: bytes) -> bytes:
    b64 = b64.replace(b"\n", b"").replace(b"\r", b"")
    rem = len(b64) % 4
    if rem == 1:                     # Serato's off-by-one: pad with 'A=='
        b64 += b"A=="
    elif rem:
        b64 += b"=" * (4 - rem)
    return base64.b64decode(b64)


def encode_tag(payload: bytes) -> bytes:
    data = TAG_HEADER + _b64_serato(payload)
    if len(data) < MIN_TAG_LENGTH:
        data += b"\x00" * (MIN_TAG_LENGTH - len(data))
    return data


def decode_tag(data: bytes) -> bytes:
    if data[:2] != TAG_HEADER:
        raise ValueError("Bad Serato Markers2 tag header")
    end = data.find(b"\x00", 2)
    b64 = data[2:end if end != -1 else len(data)]
    return _b64_decode_lenient(b64)


def _wrap_for_atom(tag: bytes, pad: bool) -> bytes:
    enc = base64.b64encode(GEOB_PREFIX + tag)
    if not pad:
        enc = enc.rstrip(b"=")
    return _wrap_lines(enc)


def _unwrap_from_atom(value: bytes) -> bytes:
    raw = _b64_decode_lenient(value)
    if not raw.startswith(GEOB_PREFIX):
        raise ValueError("Unexpected Serato atom prefix")
    return raw[len(GEOB_PREFIX):]


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------

def _kind(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in MP3_LIKE:
        return "id3"
    if ext in MP4_LIKE:
        return "mp4"
    if ext in FLAC_LIKE:
        return "flac"
    raise ValueError(f"Serato tags not supported for {ext or 'this'} files")


def _id3_handle(path: Path):
    """(tags, save) for MP3/AIFF/WAV, putting ID3 frames where the
    container expects them. A bare ID3.save() on a RIFF/AIFF file would
    prepend the tag and corrupt the audio."""
    ext = path.suffix.lower()
    if ext in (".aif", ".aiff"):
        from mutagen.aiff import AIFF
        f = AIFF(str(path))
        if f.tags is None:
            f.add_tags()
        return f.tags, f.save
    if ext == ".wav":
        from mutagen.wave import WAVE
        f = WAVE(str(path))
        if f.tags is None:
            f.add_tags()
        return f.tags, f.save
    from mutagen.id3 import ID3, ID3NoHeaderError
    try:
        tags = ID3(str(path))
    except ID3NoHeaderError:
        tags = ID3()
    return tags, (lambda: tags.save(str(path), v2_version=4))


def read_tag_bytes(path) -> bytes | None:
    """Raw Serato Markers2 tag bytes as stored in the file, or None."""
    path = Path(path)
    kind = _kind(path)
    if kind == "id3":
        tags, _ = _id3_handle(path)
        frame = tags.get(f"GEOB:{GEOB_DESC}")
        return bytes(frame.data) if frame is not None else None
    if kind == "mp4":
        from mutagen.mp4 import MP4
        f = MP4(str(path))
        vals = f.tags.get(MP4_ATOM) if f.tags else None
        return _unwrap_from_atom(bytes(vals[0])) if vals else None
    from mutagen.flac import FLAC
    vals = FLAC(str(path)).get(FLAC_KEY)
    return _unwrap_from_atom(vals[0].encode("ascii")) if vals else None


def read_cues(path) -> list[SeratoCue]:
    raw = read_tag_bytes(path)
    if raw is None:
        return []
    return cues_from_entries(parse_payload(decode_tag(raw)))


def _store_tag_bytes(path: Path, tag: bytes | None) -> None:
    """Write raw tag bytes (or remove the tag when None)."""
    kind = _kind(path)
    if kind == "id3":
        from mutagen.id3 import GEOB
        tags, save = _id3_handle(path)
        tags.delall(f"GEOB:{LEGACY_GEOB_DESC}")
        if tag is None:
            tags.delall(f"GEOB:{GEOB_DESC}")
        else:
            tags[f"GEOB:{GEOB_DESC}"] = GEOB(encoding=0, mime=GEOB_MIME,
                                             desc=GEOB_DESC, data=tag)
        save()
    elif kind == "mp4":
        from mutagen.mp4 import MP4, MP4FreeForm, AtomDataType
        f = MP4(str(path))
        if f.tags is None:
            f.add_tags()
        f.tags.pop(MP4_LEGACY_ATOM, None)
        if tag is None:
            f.tags.pop(MP4_ATOM, None)
        else:
            f.tags[MP4_ATOM] = [MP4FreeForm(_wrap_for_atom(tag, pad=True),
                                            dataformat=AtomDataType.UTF8)]
        f.save()
    else:
        from mutagen.flac import FLAC
        f = FLAC(str(path))
        f.pop(FLAC_LEGACY_KEY, None)
        if tag is None:
            f.pop(FLAC_KEY, None)
        else:
            f[FLAC_KEY] = _wrap_for_atom(tag, pad=False).decode("ascii")
        f.save()


def write_cues(path, cues: list[SeratoCue]) -> bytes | None:
    """Replace the file's Serato cue points, preserving other Serato
    entries (loops, track colour, beatgrid lock).

    Returns the previous raw tag bytes (None if there were none) so the
    caller can keep a backup for undo.
    """
    path = Path(path)
    previous = read_tag_bytes(path)
    entries = parse_payload(decode_tag(previous)) if previous else []
    _store_tag_bytes(path, encode_tag(build_payload(replace_cues(entries, cues))))
    return previous


def restore_tag_bytes(path, previous: bytes | None) -> None:
    """Undo helper: put back raw tag bytes saved from write_cues()."""
    _store_tag_bytes(Path(path), previous)
