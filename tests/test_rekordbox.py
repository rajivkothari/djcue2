"""Tests for the rekordbox XML exporter and the undo journal."""

import xml.etree.ElementTree as ET

from autocue.exporters import rekordbox as rb
from autocue import undo


def _cues():
    return [{"num": 1, "seconds": 1.005, "name": "Intro", "color": (0xEA, 0xC5, 0x32)},
            {"num": 3, "seconds": 61.005, "name": "Drop", "color": (0xB8, 0x55, 0xBF)}]


def test_location_roundtrip_windows_and_posix():
    loc = rb.location_for("C:\\Music\\My Song #1.mp3")
    assert loc == "file://localhost/C:/Music/My%20Song%20%231.mp3"
    assert rb.path_from_location(loc) == "C:/Music/My Song #1.mp3"
    assert rb.path_from_location(rb.location_for("/Users/x/a b.mp3")) == "/Users/x/a b.mp3"


def test_creates_file_and_track(tmp_path):
    xml = tmp_path / "rb.xml"
    res = rb.write_cues(xml, "C:\\Music\\a.mp3", _cues(), title="A", artist="Art",
                        bpm=128.0, duration_seconds=240.4,
                        grid={"first_beat_seconds": 1.005, "bpm": 128.0}, make_backup=False)
    assert res["created_track"] and res["written"] == 2 and res["backup"] is None
    root = ET.parse(xml).getroot()
    assert root.tag == "DJ_PLAYLISTS"
    coll = root.find("COLLECTION")
    assert coll.get("Entries") == "1"
    t = coll.find("TRACK")
    assert t.get("Name") == "A" and t.get("AverageBpm") == "128.00" and t.get("TotalTime") == "240"
    assert t.get("Location") == "file://localhost/C:/Music/a.mp3"
    marks = t.findall("POSITION_MARK")
    assert [(m.get("Num"), m.get("Start"), m.get("Name")) for m in marks] == [
        ("0", "1.005", "Intro"), ("2", "61.005", "Drop")]
    assert marks[0].get("Red") == "234" and marks[0].get("Type") == "0"
    tempo = t.find("TEMPO")
    assert tempo.get("Inizio") == "1.005" and tempo.get("Bpm") == "128.00" and tempo.get("Battito") == "1"
    node = root.find("PLAYLISTS/NODE/NODE")
    assert node.get("Name") == "autocue" and node.get("Entries") == "1"
    assert node.find("TRACK").get("Key") == t.get("TrackID")


def test_upsert_replaces_hot_cues_keeps_memory_cues_and_other_tracks(tmp_path):
    xml = tmp_path / "rb.xml"
    rb.write_cues(xml, "C:\\Music\\a.mp3", _cues(), make_backup=False)
    rb.write_cues(xml, "C:\\Music\\b.mp3", _cues()[:1], make_backup=False)
    # add a memory cue by hand to a.mp3
    tree = ET.parse(xml)
    t = tree.getroot().find("COLLECTION").findall("TRACK")[0]
    ET.SubElement(t, "POSITION_MARK", Name="mem", Type="0", Start="30.000", Num="-1")
    tree.write(xml)

    res = rb.write_cues(xml, "c:/music/A.MP3", [{"num": 2, "seconds": 9.0, "name": "V", "color": (1, 2, 3)}])
    assert res["created_track"] is False and res["backup"] and tmp_path.joinpath(res["backup"]).exists()
    root = ET.parse(xml).getroot()
    tracks = root.find("COLLECTION").findall("TRACK")
    assert len(tracks) == 2
    a = tracks[0]
    nums = sorted(m.get("Num") for m in a.findall("POSITION_MARK"))
    assert nums == ["-1", "1"]                                  # memory cue kept, hot cues replaced
    assert rb.read_cues(xml, "C:\\Music\\b.mp3") == _cues()[:1]
    assert len(root.find("PLAYLISTS/NODE/NODE").findall("TRACK")) == 2


def test_read_and_remove(tmp_path):
    xml = tmp_path / "rb.xml"
    assert rb.read_cues(xml, "x.mp3") is None
    rb.write_cues(xml, "x.mp3", _cues(), make_backup=False)
    assert rb.read_cues(xml, "x.mp3") == _cues()
    assert rb.remove_track(xml, "x.mp3", make_backup=False) is True
    assert rb.read_cues(xml, "x.mp3") is None
    assert ET.parse(xml).getroot().find("COLLECTION").get("Entries") == "0"
    assert rb.remove_track(xml, "x.mp3", make_backup=False) is False


def test_undo_journal_roundtrip(tmp_path):
    assert undo.latest(tmp_path, 7) is None
    p1 = undo.record(tmp_path, {"track_id": 7, "engine": {"quick_cues": undo.b64(b"\x01\x02")}})
    p2 = undo.record(tmp_path, {"track_id": 7, "engine": {"quick_cues": None}})
    undo.record(tmp_path, {"track_id": 8, "engine": {"quick_cues": undo.b64(b"zz")}})
    path, snap = undo.latest(tmp_path, 7)
    assert path == p2 and snap["engine"]["quick_cues"] is None and snap["time"]
    undo.discard(path)
    path, snap = undo.latest(tmp_path, 7)
    assert path == p1 and undo.unb64(snap["engine"]["quick_cues"]) == b"\x01\x02"
    assert undo.unb64(None) is None
