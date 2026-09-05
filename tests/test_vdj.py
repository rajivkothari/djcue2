"""Tests for the VirtualDJ database.xml writer."""

import xml.etree.ElementTree as ET

from autocue.exporters import vdj

SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<VirtualDJ_Database Version="2024">
 <Song FilePath="C:\\Music\\a.mp3" FileSize="1000">
  <Tags Author="A" Title="Track A"/>
  <Scan Version="801" Bpm="0.46875"/>
  <Poi Pos="0.5" Type="beatgrid" Bpm="0.46875"/>
  <Poi Pos="10" Type="cue" Num="1" Name="Old 1" Color="4294901760"/>
  <Poi Pos="30" Type="cue" Num="3" Name="Keep 3" Color="4278255360"/>
  <Poi Pos="40" Size="7.5" Type="loop" Num="1"/>
 </Song>
 <Song FilePath="C:\\Music\\b.mp3" FileSize="2000">
  <Poi Pos="1" Type="cue" Num="1" Name="B1" Color="1"/>
 </Song>
</VirtualDJ_Database>
"""


def _db(tmp_path):
    p = tmp_path / "database.xml"
    p.write_text(SAMPLE, encoding="utf-8")
    return p


def test_argb_packing():
    assert vdj.argb_int(0xEA, 0xC5, 0x32) == 0xFFEAC532


def test_replaces_only_targeted_cue_slots(tmp_path):
    db = _db(tmp_path)
    res = vdj.write_cues(db, "c:/music/A.MP3", [          # different case + slashes
        {"num": 1, "seconds": 5.0, "name": "Intro", "color": (0xEA, 0xC5, 0x32)},
        {"num": 2, "seconds": 20.0, "name": "Verse", "color": (0xEA, 0x8F, 0x32)},
    ])
    assert res["written"] == 2 and res["created_song"] is False
    assert res["backup"] and tmp_path.joinpath(res["backup"]).exists()

    cues = {c["num"]: c for c in vdj.read_cues(db, "C:\\Music\\a.mp3")}
    assert cues[1]["seconds"] == 5.0 and cues[1]["name"] == "Intro"
    assert cues[2]["seconds"] == 20.0
    assert cues[3]["name"] == "Keep 3"                    # untouched slot

    song = [s for s in ET.parse(db).getroot().findall("Song")
            if s.get("FilePath") == "C:\\Music\\a.mp3"][0]
    types = [p.get("Type") for p in song.findall("Poi")]
    assert "beatgrid" in types and "loop" in types        # non-cue POIs kept
    assert song.find("Tags").get("Title") == "Track A"    # metadata kept


def test_other_song_untouched(tmp_path):
    db = _db(tmp_path)
    vdj.write_cues(db, "C:\\Music\\a.mp3",
                   [{"num": 1, "seconds": 1, "name": "", "color": (0, 0, 0)}])
    assert vdj.read_cues(db, "C:\\Music\\b.mp3")[0]["name"] == "B1"


def test_creates_song_when_missing(tmp_path):
    db = _db(tmp_path)
    res = vdj.write_cues(db, "C:\\Music\\new.mp3",
                         [{"num": 4, "seconds": 61.25, "name": "Drop", "color": (1, 2, 3)}],
                         make_backup=False)
    assert res["created_song"] is True and res["backup"] is None
    got = vdj.read_cues(db, "C:\\Music\\new.mp3")
    assert got == [{"num": 4, "seconds": 61.25, "name": "Drop", "color": (1, 2, 3)}]


def test_output_is_valid_utf8_xml_with_declaration(tmp_path):
    db = _db(tmp_path)
    vdj.write_cues(db, "C:\\Music\\a.mp3",
                   [{"num": 1, "seconds": 2, "name": "दिल", "color": (9, 9, 9)}],
                   make_backup=False)
    text = db.read_text(encoding="utf-8")
    assert text.startswith("<?xml version='1.0' encoding='UTF-8'?>") or \
        text.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    assert vdj.read_cues(db, "C:\\Music\\a.mp3")[0]["name"] == "दिल"
