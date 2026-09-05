"""Tests for the Serato Markers2 codec and ID3 round-trip."""

import base64
import struct

import pytest

from autocue.exporters import serato
from autocue.exporters.serato import (
    SeratoCue, build_payload, parse_payload, encode_cue, decode_cue,
    replace_cues, encode_tag, decode_tag, cues_from_entries,
)


def _cues():
    return [
        SeratoCue(0, 5000, (0xEA, 0xC5, 0x32), "Intro"),
        SeratoCue(1, 20000, (0xEA, 0x8F, 0x32), "Verse"),
        SeratoCue(2, 50000, (0xB8, 0x55, 0xBF), "Chorus 1"),
    ]


class TestCueEntry:
    def test_layout_matches_spec(self):
        data = encode_cue(SeratoCue(3, 123456, (1, 2, 3), "Hi"))
        # pad, index, pos(ms BE), pad, rgb, pad pad, name, null
        assert data[0] == 0
        assert data[1] == 3
        assert struct.unpack(">I", data[2:6])[0] == 123456
        assert data[6] == 0
        assert data[7:10] == bytes((1, 2, 3))
        assert data[10:12] == b"\x00\x00"
        assert data[12:] == b"Hi\x00"

    def test_roundtrip_unicode_name(self):
        c = SeratoCue(7, 1, (255, 0, 128), "Hook — दिल")
        assert decode_cue(encode_cue(c)) == c


class TestPayload:
    def test_roundtrip_preserves_unknown_entries(self):
        loop = ("LOOP", b"\x00\x00" + b"\x01" * 20)
        entries = [("COLOR", b"\x00\x99\xff\x99"),
                   ("CUE", encode_cue(_cues()[0])),
                   loop,
                   ("BPMLOCK", b"\x01")]
        payload = build_payload(entries)
        assert payload[:2] == b"\x01\x01" and payload[-1] == 0
        assert parse_payload(payload) == entries

    def test_bad_header_rejected(self):
        with pytest.raises(ValueError):
            parse_payload(b"\x00\x00")

    def test_replace_cues_keeps_color_first_and_loops(self):
        entries = [("COLOR", b"\x00\x11\x22\x33"), ("CUE", encode_cue(_cues()[2])),
                   ("LOOP", b"x" * 10), ("BPMLOCK", b"\x00")]
        new = replace_cues(entries, _cues())
        names = [n for n, _ in new]
        assert names == ["COLOR", "CUE", "CUE", "CUE", "LOOP", "BPMLOCK"]
        assert [c.index for c in cues_from_entries(new)] == [0, 1, 2]


class TestTag:
    def test_header_line_wrap_padding(self):
        tag = encode_tag(build_payload([("CUE", encode_cue(c)) for c in _cues()]))
        assert tag[:2] == b"\x01\x01"
        assert len(tag) >= serato.MIN_TAG_LENGTH
        body = tag[2:tag.index(b"\x00", 2)]
        lines = body.split(b"\n")
        assert all(len(l) <= 72 for l in lines)
        assert b"=" not in body                 # Serato swaps '=' for 'A'

    def test_roundtrip(self):
        payload = build_payload([("CUE", encode_cue(c)) for c in _cues()])
        assert decode_tag(encode_tag(payload)) == payload

    def test_decodes_serato_off_by_one_base64(self):
        # 16-byte payload -> 22 unpadded base64 chars (len % 4 == 2).
        # Serato sometimes emits one char short of that, i.e. len % 4 == 1,
        # which strict base64 rejects. The lenient decoder pads with 'A=='
        # and recovers everything but the trailing terminator byte.
        entries = [("BPMLOCK", b"\x01")]
        payload = build_payload(entries)
        assert len(payload) == 16
        b64 = base64.b64encode(payload).rstrip(b"=")
        assert len(b64) % 4 == 2
        short = b64[:-1]
        assert len(short) % 4 == 1
        with pytest.raises(Exception):
            base64.b64decode(short)                   # strict decode fails
        recovered = decode_tag(b"\x01\x01" + short + b"\x00\x00")
        assert parse_payload(recovered) == entries

    def test_decodes_unpadded_and_wrapped_base64(self):
        payload = build_payload([("CUE", encode_cue(c)) for c in _cues()])
        b64 = base64.b64encode(payload).rstrip(b"=")
        wrapped = b"\n".join(b64[i:i + 72] for i in range(0, len(b64), 72))
        assert decode_tag(b"\x01\x01" + wrapped + b"\x00") == payload

    def test_atom_wrapper_roundtrip_with_and_without_padding(self):
        tag = encode_tag(build_payload([("BPMLOCK", b"\x01")]))
        for pad in (True, False):
            wrapped = serato._wrap_for_atom(tag, pad=pad)
            assert all(len(l) <= 72 for l in wrapped.split(b"\n"))
            assert serato._unwrap_from_atom(wrapped) == tag


class TestID3File:
    def test_write_read_restore_on_mp3(self, tmp_path):
        pytest.importorskip("mutagen")
        f = tmp_path / "song.mp3"
        f.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 400)   # bare MPEG-ish frame

        assert serato.read_cues(f) == []
        prev = serato.write_cues(f, _cues())
        assert prev is None
        assert serato.read_cues(f) == _cues()

        # second write replaces cues and returns the previous tag bytes
        prev2 = serato.write_cues(f, _cues()[:1])
        assert prev2 is not None and prev2[:2] == b"\x01\x01"
        assert serato.read_cues(f) == _cues()[:1]

        serato.restore_tag_bytes(f, prev2)
        assert serato.read_cues(f) == _cues()

        serato.restore_tag_bytes(f, None)
        assert serato.read_cues(f) == []

    def test_legacy_markers_frame_is_removed(self, tmp_path):
        mutagen = pytest.importorskip("mutagen")
        from mutagen.id3 import ID3, GEOB
        f = tmp_path / "song.mp3"
        f.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 400)
        tags = ID3()
        tags["GEOB:Serato Markers_"] = GEOB(encoding=0, mime="application/octet-stream",
                                            desc="Serato Markers_", data=b"\x02\x05junk")
        tags.save(str(f))
        serato.write_cues(f, _cues())
        assert "GEOB:Serato Markers_" not in ID3(str(f))
        assert "GEOB:Serato Markers2" in ID3(str(f))

    def test_unsupported_extension(self, tmp_path):
        with pytest.raises(ValueError, match="not supported"):
            serato.read_cues(tmp_path / "x.ogg")
