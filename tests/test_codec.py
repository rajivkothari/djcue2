"""Tests for the Engine DJ blob codec.

Uses synthetic blobs to verify parsing logic. Once real fixture blobs
are extracted from an actual Engine DJ library, add them to fixtures/
and add tests against those.
"""

import struct
import zlib
import pytest

from autocue.codec import (
    _qt_compress,
    _qt_decompress,
    decode_quick_cues,
    encode_quick_cues,
    decode_beat_data,
    encode_beat_data,
    is_cue_active,
    CUE_POSITION_EMPTY,
    snap_to_downbeat,
    get_downbeat_positions,
)


def _make_quick_cues_blob(cues, adjusted_main_cue=0.0,
                          is_main_cue_adjusted=False,
                          default_main_cue=0.0, extra_data=b""):
    """Build a synthetic quickCues blob for testing."""
    parts = [struct.pack('>q', len(cues))]
    for label, position, argb in cues:
        label_bytes = label.encode('utf-8')
        parts.append(struct.pack('B', len(label_bytes)))
        if label_bytes:
            parts.append(label_bytes)
        parts.append(struct.pack('>d', position))
        parts.append(bytes(argb))
    parts.append(struct.pack('>d', adjusted_main_cue))
    parts.append(struct.pack('B', 1 if is_main_cue_adjusted else 0))
    parts.append(struct.pack('>d', default_main_cue))
    parts.append(extra_data)

    raw = b"".join(parts)
    return _qt_compress(raw)


def _make_beat_data_blob(sample_rate, total_samples, is_set,
                         default_markers, adjusted_markers,
                         extra_data=b""):
    parts = []
    parts.append(struct.pack('>d', sample_rate))
    parts.append(struct.pack('>d', total_samples))
    parts.append(struct.pack('B', 1 if is_set else 0))

    for markers in (default_markers, adjusted_markers):
        parts.append(struct.pack('>q', len(markers)))
        for m in markers:
            parts.append(struct.pack('<d', m[0]))   # sample_offset
            parts.append(struct.pack('<q', m[1]))   # beat_number
            parts.append(struct.pack('<i', m[2]))   # number_of_beats
            parts.append(struct.pack('<i', m[3]))   # unknown_value_1

    parts.append(extra_data)
    raw = b"".join(parts)
    return _qt_compress(raw)


class TestQtCompression:
    def test_roundtrip(self):
        data = b"hello world this is a test of qt compression"
        blob = _qt_compress(data)
        assert blob[:4] == struct.pack('>I', len(data))
        assert _qt_decompress(blob) == data

    def test_empty(self):
        data = b""
        blob = _qt_compress(data)
        assert _qt_decompress(blob) == data

    def test_short_blob_raises(self):
        with pytest.raises(ValueError, match="too short"):
            _qt_decompress(b"\x00\x01")


class TestQuickCues:
    def test_decode_single_active_cue(self):
        blob = _make_quick_cues_blob([
            ("Mix In", 44100.0, [255, 234, 197, 50]),
            ("", CUE_POSITION_EMPTY, [0, 0, 0, 0]),
        ])
        data = decode_quick_cues(blob)
        assert len(data["cues"]) == 2

        cue0 = data["cues"][0]
        assert cue0["index"] == 0
        assert cue0["label"] == "Mix In"
        assert cue0["position_samples"] == 44100.0
        assert cue0["color_a"] == 255
        assert cue0["color_r"] == 234
        assert cue0["color_g"] == 197
        assert cue0["color_b"] == 50
        assert is_cue_active(cue0)

        cue1 = data["cues"][1]
        assert cue1["position_samples"] == CUE_POSITION_EMPTY
        assert not is_cue_active(cue1)

    def test_decode_eight_slots(self):
        cues = []
        for i in range(8):
            if i < 3:
                cues.append((f"Cue {i+1}", 44100.0 * (i + 1), [255, 100+i, 50, 200]))
            else:
                cues.append(("", CUE_POSITION_EMPTY, [0, 0, 0, 0]))

        blob = _make_quick_cues_blob(cues, adjusted_main_cue=44100.0,
                                     default_main_cue=44100.0)
        data = decode_quick_cues(blob)
        assert len(data["cues"]) == 8
        active = [c for c in data["cues"] if is_cue_active(c)]
        assert len(active) == 3
        assert data["adjusted_main_cue"] == 44100.0
        assert data["default_main_cue"] == 44100.0

    def test_roundtrip_byte_identical(self):
        cues = [
            ("Mix In", 44100.0, [255, 234, 197, 50]),
            ("Drop", 88200.0, [255, 186, 42, 65]),
            ("", CUE_POSITION_EMPTY, [0, 0, 0, 0]),
            ("", CUE_POSITION_EMPTY, [0, 0, 0, 0]),
            ("Outro", 441000.0, [255, 134, 198, 75]),
            ("", CUE_POSITION_EMPTY, [0, 0, 0, 0]),
            ("", CUE_POSITION_EMPTY, [0, 0, 0, 0]),
            ("", CUE_POSITION_EMPTY, [0, 0, 0, 0]),
        ]
        blob = _make_quick_cues_blob(cues, adjusted_main_cue=44100.0,
                                     is_main_cue_adjusted=True,
                                     default_main_cue=22050.0,
                                     extra_data=b"\x01\x02\x03")
        data = decode_quick_cues(blob)
        re_encoded = encode_quick_cues(data, original_blob=blob)
        assert re_encoded == blob

    def test_roundtrip_without_original(self):
        blob = _make_quick_cues_blob([
            ("Test", 44100.0, [255, 100, 100, 100]),
        ])
        data = decode_quick_cues(blob)
        re_encoded = encode_quick_cues(data)
        data2 = decode_quick_cues(re_encoded)
        assert data2["cues"][0]["label"] == "Test"
        assert data2["cues"][0]["position_samples"] == 44100.0

    def test_unicode_label(self):
        blob = _make_quick_cues_blob([
            ("Müsik ♪", 44100.0, [255, 200, 100, 50]),
        ])
        data = decode_quick_cues(blob)
        assert data["cues"][0]["label"] == "Müsik ♪"
        re_encoded = encode_quick_cues(data, original_blob=blob)
        assert re_encoded == blob

    def test_extra_data_preserved(self):
        extra = b"\xde\xad\xbe\xef" * 10
        blob = _make_quick_cues_blob(
            [("", CUE_POSITION_EMPTY, [0, 0, 0, 0])],
            extra_data=extra,
        )
        data = decode_quick_cues(blob)
        assert data["extra_data"] == extra
        re_encoded = encode_quick_cues(data, original_blob=blob)
        assert re_encoded == blob


class TestBeatData:
    def test_decode_basic(self):
        blob = _make_beat_data_blob(
            sample_rate=44100.0,
            total_samples=44100.0 * 300,
            is_set=True,
            default_markers=[
                (0.0, 0, 400, 0),
                (44100.0 * 300, 400, 0, 0),
            ],
            adjusted_markers=[],
        )
        data = decode_beat_data(blob)
        assert data["sample_rate"] == 44100.0
        assert data["is_beatgrid_set"] is True
        assert len(data["default_markers"]) == 2
        assert len(data["adjusted_markers"]) == 0
        assert data["default_markers"][0]["beat_number"] == 0
        assert data["default_markers"][1]["beat_number"] == 400

    def test_roundtrip_byte_identical(self):
        blob = _make_beat_data_blob(
            sample_rate=48000.0,
            total_samples=48000.0 * 240,
            is_set=True,
            default_markers=[
                (24000.0, 0, 500, 0),
                (48000.0 * 240, 500, 0, 0),
            ],
            adjusted_markers=[
                (24500.0, 0, 500, 0),
                (48000.0 * 240, 500, 0, 0),
            ],
            extra_data=b"\xff\x00",
        )
        data = decode_beat_data(blob)
        re_encoded = encode_beat_data(data, original_blob=blob)
        assert re_encoded == blob


class TestDownbeats:
    def test_snap_to_nearest(self):
        downbeats = [0.0, 44100.0, 88200.0, 132300.0]
        assert snap_to_downbeat(45000.0, downbeats) == 44100.0
        assert snap_to_downbeat(0.0, downbeats) == 0.0
        assert snap_to_downbeat(130000.0, downbeats) == 132300.0

    def test_snap_empty_list(self):
        assert snap_to_downbeat(44100.0, []) == 44100.0

    def test_get_downbeats_from_grid(self):
        beat_data = {
            "sample_rate": 44100.0,
            "total_samples": 44100.0 * 120,
            "is_beatgrid_set": True,
            "default_markers": [
                {"sample_offset": 0.0, "beat_number": 0,
                 "number_of_beats": 16, "unknown_value_1": 0},
                {"sample_offset": 44100.0 * 8, "beat_number": 16,
                 "number_of_beats": 0, "unknown_value_1": 0},
            ],
            "adjusted_markers": [],
            "extra_data": b"",
        }
        downbeats = get_downbeat_positions(beat_data)
        assert len(downbeats) == 4
        assert downbeats[0] == 0.0
