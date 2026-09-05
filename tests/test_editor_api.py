"""End-to-end test of the cue editor API against a synthetic Engine DJ
library (schema 3.0.2 layout) with a real tag write into a fake MP3."""

import sqlite3

import pytest

flask = pytest.importorskip("flask")
mutagen = pytest.importorskip("mutagen")

from autocue import server                                   # noqa: E402
from autocue.codec import (                                  # noqa: E402
    encode_beat_data, encode_quick_cues, CUE_POSITION_EMPTY,
)
from autocue.exporters import serato, vdj                    # noqa: E402

SR = 44100.0
BPM = 128.0
SPB = SR * 60 / BPM
DURATION_S = 240.0


def _beat_blob():
    total = SR * DURATION_S
    n_beats = int(total // SPB)
    markers = [
        {"sample_offset": 0.0, "beat_number": 0,
         "number_of_beats": n_beats, "unknown_value_1": 0},
        {"sample_offset": n_beats * SPB, "beat_number": n_beats,
         "number_of_beats": 0, "unknown_value_1": 0},
    ]
    return encode_beat_data({
        "sample_rate": SR, "total_samples": total, "is_beatgrid_set": True,
        "default_markers": markers, "adjusted_markers": [], "extra_data": b"",
    })


def _empty_cues_blob():
    cues = [{"index": i, "label": "", "position_samples": CUE_POSITION_EMPTY,
             "color_a": 0, "color_r": 0, "color_g": 0, "color_b": 0}
            for i in range(8)]
    return encode_quick_cues({
        "cues": cues, "adjusted_main_cue": 0.0, "is_main_cue_adjusted": False,
        "default_main_cue": 0.0, "extra_data": b"",
    })


@pytest.fixture
def library(tmp_path, monkeypatch):
    root = tmp_path / "Engine Library"
    (root / "Database2").mkdir(parents=True)
    (root / "Music").mkdir()
    audio = root / "Music" / "song.mp3"
    audio.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 2000)

    mdb = root / "Database2" / "m.db"
    conn = sqlite3.connect(mdb)
    conn.executescript("""
        CREATE TABLE Information (schemaVersionMajor INT, schemaVersionMinor INT,
                                  schemaVersionPatch INT);
        INSERT INTO Information VALUES (3, 0, 2);
        CREATE TABLE Track (id INTEGER PRIMARY KEY, title TEXT, artist TEXT,
                            path TEXT, bpmAnalyzed REAL);
        CREATE TABLE PerformanceData (trackId INT, trackData BLOB,
                                      quickCues BLOB, beatData BLOB);
        CREATE TABLE Playlist (id INTEGER PRIMARY KEY, title TEXT);
        CREATE TABLE PlaylistEntity (listId INT, trackId INT);
        CREATE TABLE Crate (id INTEGER PRIMARY KEY, title TEXT);
        CREATE TABLE CrateTrackList (crateId INT, trackId INT);
    """)
    conn.execute("INSERT INTO Track VALUES (1, 'Test Song', 'Tester', 'Music/song.mp3', ?)",
                 (BPM,))
    conn.execute("INSERT INTO PerformanceData VALUES (1, NULL, ?, ?)",
                 (_empty_cues_blob(), _beat_blob()))
    conn.commit()
    conn.close()

    vdj_db = tmp_path / "database.xml"
    vdj_db.write_text('<?xml version="1.0" encoding="UTF-8"?>\n'
                      '<VirtualDJ_Database Version="2024">\n</VirtualDJ_Database>\n',
                      encoding="utf-8")

    monkeypatch.setattr(server, "is_engine_dj_running", lambda: False)
    monkeypatch.setattr(vdj, "is_virtualdj_running", lambda: False)
    server.set_db_path(str(mdb))
    server._vdj_db_path = str(vdj_db)
    return {"mdb": mdb, "audio": audio, "vdj_db": vdj_db,
            "client": server.app.test_client()}


def test_library_and_track_detail(library):
    c = library["client"]
    lib = c.get("/api/library").get_json()
    assert [t["title"] for t in lib] == ["Test Song"]
    assert lib[0]["has_cues"] is False
    assert lib[0]["duration"] == pytest.approx(DURATION_S)

    t = c.get("/api/track/1").get_json()
    assert t["seconds_per_beat"] == pytest.approx(60 / BPM)
    assert len(t["beats"]) > 500
    assert t["main_cue_seconds"] is None            # unset main cue -> no anchor
    assert t["grid_first_downbeat_seconds"] == 0.0
    assert t["serato_supported"] is True
    assert t["vdj_available"] is True
    assert t["audio_available"] is True
    assert t["cues"] == []


def test_generate_places_bars_from_anchor(library):
    c = library["client"]
    anchor = 5.0
    r = c.post("/api/generate", json={"track_id": 1, "template": "edm",
                                      "anchor_seconds": anchor}).get_json()
    assert r["unsupported"] == []
    times = {p["slot"]: p["time_seconds"] for p in r["proposed"]}
    spb = 60 / BPM
    # edm template: bars 1, 17, 33, 49, 65, 81 -> (bar-1)*4 beats after anchor
    for slot, bar in zip(range(1, 7), (1, 17, 33, 49, 65, 81)):
        expected = anchor + (bar - 1) * 4 * spb
        # anchor at 5.0s is not on the synthetic grid (which starts at 0),
        # so no snap happens and the pure math is honoured
        assert times[slot] == pytest.approx(expected, abs=1e-6)


def test_generate_skips_cues_past_track_end(library):
    # Track is 240 s. Anchoring bar 1 at 200 s leaves room for bars 1 and 17
    # (200 s, 230 s) but bars 33+ (260 s, ...) fall off the end.
    r = library["client"].post("/api/generate", json={
        "track_id": 1, "template": "edm", "anchor_seconds": 200.0}).get_json()
    assert [p["slot"] for p in r["proposed"]] == [1, 2]
    assert r["beyond_end"] == [3, 4, 5, 6]


def test_generate_requires_anchor(library):
    r = library["client"].post("/api/generate", json={"track_id": 1, "template": "edm"})
    assert r.status_code == 400


def test_save_to_all_targets(library):
    c = library["client"]
    cues = [
        {"slot": 1, "label": "Intro", "color_name": "yellow", "time_seconds": 5.0},
        {"slot": 2, "label": "Build", "color_name": "orange", "time_seconds": 35.0},
        {"slot": 3, "label": "Drop 1", "color_name": "purple", "time_seconds": 65.0},
    ]
    r = c.post("/api/save", json={"track_id": 1, "cues": cues,
                                  "targets": {"engine": True, "serato": True, "vdj": True}}).get_json()
    res = r["results"]
    assert res["engine"]["ok"], res["engine"]
    assert res["serato"]["ok"], res["serato"]
    assert res["vdj"]["ok"], res["vdj"]
    assert "backup" in res["engine"] and "backup" in res["vdj"]

    # Engine DJ: read back through the API
    t = c.get("/api/track/1").get_json()
    got = {x["slot"]: x for x in t["cues"]}
    assert got[1]["label"] == "Intro" and got[1]["color_name"] == "yellow"
    assert got[2]["time_seconds"] == pytest.approx(35.0)
    assert set(got) == {1, 2, 3}
    assert c.get("/api/library").get_json()[0]["has_cues"] is True

    # Serato: tag really landed in the file, in ms, 0-based index
    scues = serato.read_cues(library["audio"])
    assert [(s.index, s.position_ms, s.name) for s in scues] == [
        (0, 5000, "Intro"), (1, 35000, "Build"), (2, 65000, "Drop 1")]
    assert scues[0].color == (0xEA, 0xC5, 0x32)

    # VirtualDJ: Song entry created with 1-based Num and ARGB colour
    vcues = vdj.read_cues(library["vdj_db"], str(library["audio"]))
    assert [(v["num"], v["seconds"], v["name"]) for v in vcues] == [
        (1, 5.0, "Intro"), (2, 35.0, "Build"), (3, 65.0, "Drop 1")]

    # Saving again with fewer cues clears the missing Engine slots and
    # replaces the Serato cues; the previous tag is backed up.
    r2 = c.post("/api/save", json={"track_id": 1, "cues": cues[:1],
                                   "targets": {"engine": True, "serato": True}}).get_json()
    assert r2["results"]["serato"].get("backup", "").endswith(".bin")
    assert [x["slot"] for x in c.get("/api/track/1").get_json()["cues"]] == [1]
    assert len(serato.read_cues(library["audio"])) == 1


def test_save_rejects_bad_slot_and_color(library):
    c = library["client"]
    bad = {"track_id": 1, "targets": {"engine": True},
           "cues": [{"slot": 9, "label": "", "color_name": "yellow", "time_seconds": 1}]}
    assert c.post("/api/save", json=bad).status_code == 400
    bad["cues"][0].update(slot=1, color_name="magenta")
    assert c.post("/api/save", json=bad).status_code == 400


def test_save_refuses_while_engine_running(library, monkeypatch):
    monkeypatch.setattr(server, "is_engine_dj_running", lambda: True)
    r = library["client"].post("/api/save", json={
        "track_id": 1, "targets": {"engine": True},
        "cues": [{"slot": 1, "label": "", "color_name": "yellow", "time_seconds": 1}]}).get_json()
    assert r["results"]["engine"]["ok"] is False
    assert "running" in r["results"]["engine"]["message"]


def test_editor_page_served(library):
    r = library["client"].get("/editor")
    assert r.status_code == 200
    assert b"Cue Editor" in r.data
