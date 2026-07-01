"""Engine DJ quickCues and beatData blob codec.

Blob format derived from libdjinterop (xsco/libdjinterop on GitHub),
specifically the v2 blob implementations:
  - quick_cues_blob.cpp
  - beat_data_blob.cpp
  - track_data_blob.cpp

All blobs (except loops) use Qt qCompress framing:
  bytes 0-3: uint32 big-endian uncompressed size
  bytes 4+:  zlib compressed payload (level 6)
"""

import struct
import zlib

CUE_POSITION_EMPTY = -1.0


def _qt_decompress(blob: bytes) -> bytes:
    if len(blob) < 4:
        raise ValueError(f"Blob too short for Qt header: {len(blob)} bytes")
    expected_size = struct.unpack('>I', blob[:4])[0]
    data = zlib.decompress(blob[4:])
    if len(data) != expected_size:
        raise ValueError(
            f"Decompressed size mismatch: expected {expected_size}, got {len(data)}"
        )
    return data


def _qt_compress(data: bytes) -> bytes:
    compressed = zlib.compress(data, 6)
    return struct.pack('>I', len(data)) + compressed


# ---------------------------------------------------------------------------
# quickCues
# ---------------------------------------------------------------------------

def decode_quick_cues(blob: bytes) -> dict:
    """Decode a quickCues blob.

    Returns dict with keys:
        cues: list of cue dicts (index, label, position_samples, color_a/r/g/b)
        adjusted_main_cue: float
        is_main_cue_adjusted: bool
        default_main_cue: float
        extra_data: bytes (preserved verbatim for round-trip)
    """
    raw = _qt_decompress(blob)
    offset = 0

    num_cues = struct.unpack_from('>q', raw, offset)[0]
    offset += 8

    cues = []
    for i in range(num_cues):
        label_len = raw[offset]
        offset += 1

        label = raw[offset:offset + label_len].decode('utf-8') if label_len > 0 else ""
        offset += label_len

        sample_offset = struct.unpack_from('>d', raw, offset)[0]
        offset += 8

        color_a = raw[offset]
        color_r = raw[offset + 1]
        color_g = raw[offset + 2]
        color_b = raw[offset + 3]
        offset += 4

        cues.append({
            "index": i,
            "label": label,
            "position_samples": sample_offset,
            "color_a": color_a,
            "color_r": color_r,
            "color_g": color_g,
            "color_b": color_b,
        })

    adjusted_main_cue = struct.unpack_from('>d', raw, offset)[0]
    offset += 8

    is_main_cue_adjusted = bool(raw[offset])
    offset += 1

    default_main_cue = struct.unpack_from('>d', raw, offset)[0]
    offset += 8

    extra_data = raw[offset:]

    return {
        "cues": cues,
        "adjusted_main_cue": adjusted_main_cue,
        "is_main_cue_adjusted": is_main_cue_adjusted,
        "default_main_cue": default_main_cue,
        "extra_data": extra_data,
    }


def _serialize_quick_cues(data: dict) -> bytes:
    parts = []
    cues = data["cues"]
    parts.append(struct.pack('>q', len(cues)))

    for cue in cues:
        label_bytes = cue["label"].encode('utf-8') if cue["label"] else b""
        parts.append(struct.pack('B', len(label_bytes)))
        if label_bytes:
            parts.append(label_bytes)
        parts.append(struct.pack('>d', cue["position_samples"]))
        parts.append(bytes([cue["color_a"], cue["color_r"],
                            cue["color_g"], cue["color_b"]]))

    parts.append(struct.pack('>d', data["adjusted_main_cue"]))
    parts.append(struct.pack('B', 1 if data["is_main_cue_adjusted"] else 0))
    parts.append(struct.pack('>d', data["default_main_cue"]))
    parts.append(data["extra_data"])

    return b"".join(parts)


def encode_quick_cues(data: dict, original_blob: bytes | None = None) -> bytes:
    """Encode cues back into a quickCues blob.

    If original_blob is provided and the serialized data matches the
    original uncompressed content, returns original_blob unchanged
    (guarantees byte-identical round-trip regardless of zlib version).
    """
    raw = _serialize_quick_cues(data)
    if original_blob is not None:
        orig_raw = _qt_decompress(original_blob)
        if raw == orig_raw:
            return original_blob
    return _qt_compress(raw)


# ---------------------------------------------------------------------------
# beatData
# ---------------------------------------------------------------------------

def decode_beat_data(blob: bytes) -> dict:
    """Decode a beatData blob.

    Returns dict with keys:
        sample_rate: float
        total_samples: float
        is_beatgrid_set: bool
        default_markers: list of marker dicts
        adjusted_markers: list of marker dicts
        extra_data: bytes
    Each marker dict: sample_offset, beat_number, number_of_beats, unknown_value_1
    """
    raw = _qt_decompress(blob)
    offset = 0

    sample_rate = struct.unpack_from('>d', raw, offset)[0]
    offset += 8

    total_samples = struct.unpack_from('>d', raw, offset)[0]
    offset += 8

    is_beatgrid_set = bool(raw[offset])
    offset += 1

    def _read_markers(data, off):
        count = struct.unpack_from('>q', data, off)[0]
        off += 8
        markers = []
        for _ in range(count):
            markers.append({
                "sample_offset": struct.unpack_from('<d', data, off)[0],
                "beat_number": struct.unpack_from('<q', data, off + 8)[0],
                "number_of_beats": struct.unpack_from('<i', data, off + 16)[0],
                "unknown_value_1": struct.unpack_from('<i', data, off + 20)[0],
            })
            off += 24
        return markers, off

    default_markers, offset = _read_markers(raw, offset)
    adjusted_markers, offset = _read_markers(raw, offset)

    return {
        "sample_rate": sample_rate,
        "total_samples": total_samples,
        "is_beatgrid_set": is_beatgrid_set,
        "default_markers": default_markers,
        "adjusted_markers": adjusted_markers,
        "extra_data": raw[offset:],
    }


def _serialize_beat_data(data: dict) -> bytes:
    parts = []
    parts.append(struct.pack('>d', data["sample_rate"]))
    parts.append(struct.pack('>d', data["total_samples"]))
    parts.append(struct.pack('B', 1 if data["is_beatgrid_set"] else 0))

    for markers in (data["default_markers"], data["adjusted_markers"]):
        parts.append(struct.pack('>q', len(markers)))
        for m in markers:
            parts.append(struct.pack('<d', m["sample_offset"]))
            parts.append(struct.pack('<q', m["beat_number"]))
            parts.append(struct.pack('<i', m["number_of_beats"]))
            parts.append(struct.pack('<i', m["unknown_value_1"]))

    parts.append(data["extra_data"])
    return b"".join(parts)


def encode_beat_data(data: dict, original_blob: bytes | None = None) -> bytes:
    """Encode beat data back into a beatData blob."""
    raw = _serialize_beat_data(data)
    if original_blob is not None:
        orig_raw = _qt_decompress(original_blob)
        if raw == orig_raw:
            return original_blob
    return _qt_compress(raw)


# ---------------------------------------------------------------------------
# trackData (minimal decode — just sample rate)
# ---------------------------------------------------------------------------

def decode_track_data(blob: bytes) -> dict:
    """Decode enough of trackData to extract sample rate."""
    raw = _qt_decompress(blob)
    return {
        "sample_rate": struct.unpack_from('>d', raw, 0)[0],
        "total_samples": struct.unpack_from('>q', raw, 8)[0],
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def is_cue_active(cue: dict) -> bool:
    return cue["position_samples"] != CUE_POSITION_EMPTY


def get_downbeat_positions(beat_data: dict) -> list[float]:
    """Extract downbeat sample positions from decoded beat data.

    Uses the adjusted grid if available, otherwise the default grid.
    Downbeats are every 4th beat (assuming 4/4 time).
    """
    markers = beat_data["adjusted_markers"] or beat_data["default_markers"]
    if not markers:
        return []

    sample_rate = beat_data["sample_rate"]
    if not markers or not beat_data["is_beatgrid_set"]:
        return []

    downbeats = []
    for i, marker in enumerate(markers):
        if marker["number_of_beats"] <= 0 and i < len(markers) - 1:
            continue

        start_sample = marker["sample_offset"]
        start_beat = marker["beat_number"]

        if i + 1 < len(markers):
            next_marker = markers[i + 1]
            end_sample = next_marker["sample_offset"]
            end_beat = next_marker["beat_number"]
        else:
            break

        if end_beat <= start_beat:
            continue

        samples_per_beat = (end_sample - start_sample) / (end_beat - start_beat)

        for beat_num in range(int(start_beat), int(end_beat)):
            if beat_num % 4 == 0:
                pos = start_sample + (beat_num - start_beat) * samples_per_beat
                downbeats.append(pos)

    return downbeats


def snap_to_downbeat(position_samples: float, downbeats: list[float]) -> float:
    """Snap a sample position to the nearest downbeat."""
    if not downbeats:
        return position_samples
    return min(downbeats, key=lambda d: abs(d - position_samples))


def get_beat_positions(beat_data: dict) -> list[float]:
    """Extract every beat's sample position from the grid (not just downbeats)."""
    markers = beat_data["adjusted_markers"] or beat_data["default_markers"]
    if not markers or not beat_data["is_beatgrid_set"]:
        return []

    beats = []
    for i in range(len(markers) - 1):
        start_sample = markers[i]["sample_offset"]
        start_beat = markers[i]["beat_number"]
        end_sample = markers[i + 1]["sample_offset"]
        end_beat = markers[i + 1]["beat_number"]
        if end_beat <= start_beat:
            continue
        spb = (end_sample - start_sample) / (end_beat - start_beat)
        for beat_num in range(int(start_beat), int(end_beat)):
            beats.append(start_sample + (beat_num - start_beat) * spb)

    if markers:
        beats.append(markers[-1]["sample_offset"])
    return beats


def get_samples_per_beat(beat_data: dict) -> float | None:
    """Average samples-per-beat across the whole grid."""
    markers = beat_data["adjusted_markers"] or beat_data["default_markers"]
    if len(markers) < 2:
        return None
    first, last = markers[0], markers[-1]
    beat_span = last["beat_number"] - first["beat_number"]
    if beat_span <= 0:
        return None
    return (last["sample_offset"] - first["sample_offset"]) / beat_span


def get_main_cue(quick_cues_data: dict) -> float | None:
    """The main/load cue position, which typically sits on the true bar 1.

    Returns None if no usable main cue is set.
    """
    cue = (quick_cues_data["adjusted_main_cue"]
           if quick_cues_data["is_main_cue_adjusted"]
           else quick_cues_data["default_main_cue"])
    if cue is None or cue <= 0:
        return None
    return float(cue)
