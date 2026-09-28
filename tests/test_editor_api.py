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
    # a second track with NO Engine beat grid
    conn.execute("INSERT INTO Track VALUES (2, 'Ungridded', 'Tester', 'Music/song.mp3', 126.0)")
    conn.execute("INSERT INTO PerformanceData VALUES (2, NULL, ?, NULL)", (_empty_cues_blob(),))
    conn.execute("INSERT INTO Playlist VALUES (1, 'P')")
    conn.execute("INSERT INTO PlaylistEntity VALUES (1, 1)")
    conn.execute("INSERT INTO PlaylistEntity VALUES (1, 2)")
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
    server._rekordbox_xml = str(tmp_path / "rb.xml")
    server._jobs.clear()
    return {"mdb": mdb, "audio": audio, "vdj_db": vdj_db, "rb_xml": tmp_path / "rb.xml",
            "client": server.app.test_client()}


def _fake_ai_grid(bpm=126.0, first_db=2.5):
    """Stand-in for autocue.beats.build_grid (no torch needed)."""
    import math

    def build(path, bpm_hint=None):
        spb = 60 / bpm
        duration = DURATION_S
        kmin = math.ceil(-first_db / spb); kmax = math.floor((duration - first_db) / spb)
        ks = range(kmin, kmax + 1)
        return {"tempo_bpm": bpm, "seconds_per_beat": spb, "first_downbeat": first_db,
                "first_beat_index": kmin, "beats": [first_db + k * spb for k in ks],
                "downbeats": [first_db + k * spb for k in ks if k % 4 == 0],
                "rms_residual_ms": 3.0, "variable_tempo": False, "n_detected": 400,
                "downbeat_confidence": 1.0}
    return build


@pytest.fixture
def ai_grid(monkeypatch):
    pytest.importorskip("numpy")
    import autocue.beats
    monkeypatch.setattr(autocue.beats, "build_grid", _fake_ai_grid())
    monkeypatch.setattr(autocue.beats, "detect_first_downbeat", lambda p: 2.5)


def _wait_job(client, job_id, timeout=15.0):
    import time
    end = time.time() + timeout
    while time.time() < end:
        j = client.get(f"/api/batch/{job_id}").get_json()
        if j["done"]:
            return j
        time.sleep(0.05)
    raise AssertionError("batch job did not finish")


def test_library_and_track_detail(library):
    c = library["client"]
    lib = c.get("/api/library").get_json()
    assert [t["title"] for t in lib] == ["Test Song", "Ungridded"]
    lib = [lib[0]]
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


def test_templates_include_phrase_presets(library):
    t = library["client"].get("/api/templates").get_json()
    ids = [x["id"] for x in t]
    assert ids[:3] == ["phrase-8", "phrase-16", "phrase-32"]
    assert t[1]["name"] == "Every 16 bars" and t[1]["default_cues"] == 6
    assert "edm" in ids and "djedit" in ids


def test_generate_phrase_match_from_cue_1(library):
    r = library["client"].post("/api/generate", json={
        "track_id": 1, "template": "phrase-16-8", "anchor_seconds": 5.0}).get_json()
    assert r["template_name"] == "Every 16 bars"
    times = [p["time_seconds"] for p in r["proposed"]]
    assert len(times) == 8
    spb = 60 / BPM
    for i, t in enumerate(times):
        assert t == pytest.approx(5.0 + i * 16 * 4 * spb, abs=1e-6)
    assert r["proposed"][0]["label"] == "Intro" and r["proposed"][1]["label"] == "Bar 17"
    assert r["grid"]["source"] == "engine"


def test_generate_bad_template_is_400(library):
    r = library["client"].post("/api/generate", json={
        "track_id": 1, "template": "phrase-16-9", "anchor_seconds": 5.0})
    assert r.status_code == 400 and "1–8" in r.get_json()["error"]


def test_ai_grid_endpoint_and_generate_on_ungridded_track(library, ai_grid):
    c = library["client"]
    assert c.get("/api/track/2/grid?source=engine").status_code == 404

    g = c.get("/api/track/2/grid?source=ai").get_json()
    assert g["source"] == "ai" and g["tempo_bpm"] == pytest.approx(126.0)
    assert g["first_downbeat_seconds"] == pytest.approx(2.5)
    assert g["beats"][0] >= 0 and g["variable_tempo"] is False

    # auto mode falls back to the AI grid when Engine has none
    r = c.post("/api/generate", json={"track_id": 2, "template": "phrase-8-3",
                                      "anchor_seconds": 2.5}).get_json()
    assert r["grid"]["source"] == "ai"
    spb = 60 / 126.0
    assert [p["time_seconds"] for p in r["proposed"]] == pytest.approx(
        [2.5, 2.5 + 32 * spb, 2.5 + 64 * spb], abs=1e-6)

    # analyze (review page) also works on it now instead of erroring
    a = c.post("/api/analyze", json={"track_id": 2, "template": "phrase-16"}).get_json()
    assert a["grid"]["source"] == "ai" and a["anchor"]["source"] == "ai"
    assert a["proposed"][0]["time_seconds"] == pytest.approx(2.5)


def test_ai_grid_overrides_engine_when_asked(library, ai_grid):
    r = library["client"].post("/api/generate", json={
        "track_id": 1, "template": "phrase-16-2", "anchor_seconds": 2.5, "grid": "ai"}).get_json()
    assert r["grid"]["source"] == "ai" and r["grid"]["tempo_bpm"] == pytest.approx(126.0)
    assert r["proposed"][1]["time_seconds"] == pytest.approx(2.5 + 64 * 60 / 126.0, abs=1e-6)


def test_save_ai_grid_into_engine(library, ai_grid):
    c = library["client"]
    before = c.get("/api/track/1").get_json()
    assert before["seconds_per_beat"] == pytest.approx(60 / BPM)

    r = c.post("/api/save", json={"track_id": 1, "cues": [],
                                  "targets": {"engine_grid": True}}).get_json()
    res = r["results"]["engine_grid"]
    assert res["ok"], res
    assert "126.00 BPM" in res["message"] and res["backup"]

    after = c.get("/api/track/1").get_json()
    assert after["seconds_per_beat"] == pytest.approx(60 / 126.0)
    assert after["grid_first_downbeat_seconds"] is not None
    # bar 1 (2.5 s) is a downbeat in the new grid
    assert min(after["downbeats"], key=lambda d: abs(d - 2.5)) == pytest.approx(2.5, abs=1e-6)
    assert after["beats"][0] < 60 / 126.0                      # grid covers the intro


def test_cli_batch_phrase_works_without_librosa(library, monkeypatch, capsys):
    import sys
    from autocue import cli
    monkeypatch.setitem(sys.modules, "librosa", None)      # import would fail
    monkeypatch.setitem(sys.modules, "beat_this", None)    # no model: AI anchor unavailable
    monkeypatch.setattr(cli, "is_engine_dj_running", lambda: False)
    monkeypatch.setattr(sys, "argv", [
        "autocue", "batch", "--db", str(library["mdb"]), "--playlist", "P",
        "--phrase", "16", "--phrase-cues", "4", "--dry-run", "--grid", "engine"])
    cli.main()
    out = capsys.readouterr().out
    assert "Template: Every 16 bars" in out
    assert "grid: engine 128.0 BPM" in out and "4 cues proposed" in out
    assert "SKIP Ungridded" in out                          # engine-only grid
    assert "Tracks to review" in out and "grid only" in out  # no main cue on track 1


def test_cli_batch_intro_and_rekordbox_export(library, monkeypatch, capsys, tmp_path):
    import sys
    from autocue import cli
    from autocue.exporters import rekordbox
    monkeypatch.setattr(cli, "is_engine_dj_running", lambda: False)
    xml = tmp_path / "out.xml"
    monkeypatch.setattr(sys, "argv", [
        "autocue", "batch", "--db", str(library["mdb"]), "--playlist", "P",
        "--phrase", "16", "--phrase-cues", "3", "--intro-bars", "8", "--grid", "engine",
        "--rekordbox-xml", str(xml)])
    cli.main()
    out = capsys.readouterr().out
    assert "Intro: 8 bars" in out and "+ rekordbox" in out
    got = rekordbox.read_cues(xml, str(library["audio"]))
    spb = 60 / BPM
    assert [(x["num"], x["seconds"]) for x in got] == [
        (1, 0.0), (2, pytest.approx(32 * spb, abs=1e-3)), (3, pytest.approx(96 * spb, abs=1e-3))]


def test_generate_with_intro_bars(library):
    r = library["client"].post("/api/generate", json={
        "track_id": 1, "template": "phrase-16-3", "anchor_seconds": 5.0, "intro_bars": 8}).get_json()
    spb = 60 / BPM
    assert r["template_name"] == "Every 16 bars after a 8-bar intro"
    assert [p["time_seconds"] for p in r["proposed"]] == pytest.approx(
        [5.0, 5.0 + 32 * spb, 5.0 + 96 * spb], abs=1e-6)


def test_generate_custom_tempo_grid(library):
    # Tap tempo said 100 BPM although Engine's grid is 128: cues follow the custom grid.
    r = library["client"].post("/api/generate", json={
        "track_id": 1, "template": "phrase-8-3", "anchor_seconds": 5.0,
        "grid": "custom", "seconds_per_beat": 0.6}).get_json()
    assert r["grid"]["source"] == "custom" and r["grid"]["tempo_bpm"] == pytest.approx(100.0)
    assert [p["time_seconds"] for p in r["proposed"]] == pytest.approx(
        [5.0, 5.0 + 32 * 0.6, 5.0 + 64 * 0.6], abs=1e-6)


def test_save_custom_grid_into_engine(library):
    c = library["client"]
    r = c.post("/api/save", json={"track_id": 1, "cues": [], "targets": {"engine_grid": True},
                                  "grid_spec": {"source": "custom", "seconds_per_beat": 0.5,
                                                "bar1_seconds": 3.0}}).get_json()
    assert r["results"]["engine_grid"]["ok"], r
    t = c.get("/api/track/1").get_json()
    assert t["seconds_per_beat"] == pytest.approx(0.5)
    assert min(t["downbeats"], key=lambda d: abs(d - 3.0)) == pytest.approx(3.0, abs=1e-6)


def test_rekordbox_target_and_full_undo(library):
    from autocue.exporters import rekordbox, serato, vdj
    c = library["client"]
    cues = [{"slot": 1, "label": "Intro", "color_name": "yellow", "time_seconds": 5.0},
            {"slot": 2, "label": "Build", "color_name": "orange", "time_seconds": 35.0}]
    assert c.get("/api/track/1").get_json()["can_undo"] is False

    r = c.post("/api/save", json={"track_id": 1, "cues": cues, "targets": {
        "engine": True, "serato": True, "vdj": True, "rekordbox": True}}).get_json()
    res = r["results"]
    assert all(res[k]["ok"] for k in ("engine", "serato", "vdj", "rekordbox")), res
    assert "beat grid" in res["rekordbox"]["message"]

    rb = rekordbox.read_cues(library["rb_xml"], str(library["audio"]))
    assert [(x["num"], x["seconds"], x["name"]) for x in rb] == [(1, 5.0, "Intro"), (2, 35.0, "Build")]
    import xml.etree.ElementTree as ET
    tempo = ET.parse(library["rb_xml"]).getroot().find("COLLECTION/TRACK/TEMPO")
    # Engine's grid has no bar-1 notion, so the TEMPO starts on cue 1
    assert tempo.get("Bpm") == "128.00" and tempo.get("Inizio") == "5.000"
    assert c.get("/api/track/1").get_json()["can_undo"] is True

    u = c.post("/api/undo/1").get_json()
    assert all(v["ok"] for v in u["results"].values()), u
    assert set(u["results"]) == {"engine", "serato", "vdj", "rekordbox"}
    assert u["can_undo"] is False
    assert c.get("/api/track/1").get_json()["cues"] == []
    assert serato.read_cues(library["audio"]) == []
    assert vdj.read_cues(library["vdj_db"], str(library["audio"])) == []
    assert rekordbox.read_cues(library["rb_xml"], str(library["audio"])) is None
    assert c.post("/api/undo/1").status_code == 404


def test_undo_restores_previous_cues_not_just_clears(library):
    from autocue.exporters import serato
    c = library["client"]
    first = [{"slot": 1, "label": "A", "color_name": "yellow", "time_seconds": 1.0}]
    second = [{"slot": 3, "label": "B", "color_name": "red", "time_seconds": 9.0}]
    c.post("/api/save", json={"track_id": 1, "cues": first, "targets": {"engine": True, "serato": True}})
    c.post("/api/save", json={"track_id": 1, "cues": second, "targets": {"engine": True, "serato": True}})
    assert [x["slot"] for x in c.get("/api/track/1").get_json()["cues"]] == [3]
    u = c.post("/api/undo/1").get_json()
    assert u["can_undo"] is True                       # one more level left
    got = c.get("/api/track/1").get_json()["cues"]
    assert [(x["slot"], x["label"]) for x in got] == [(1, "A")]
    assert [(s.index, s.name) for s in serato.read_cues(library["audio"])] == [(0, "A")]


def test_batch_plan_and_apply(library, ai_grid):
    c = library["client"]
    r = c.post("/api/batch", json={"type": "playlist", "name": "P", "template": "phrase-16",
                                   "intro_bars": 0, "grid": "auto"}).get_json()
    assert "id" in r and r["total"] == 2
    job = _wait_job(c, r["id"])
    assert job["error"] is None and job["template_name"] == "Every 16 bars"
    rows = {x["track_id"]: x for x in job["rows"]}
    assert rows[1]["status"] == "ok" and rows[1]["grid"] == "engine"
    assert rows[1]["anchor"] == "ai" and rows[1]["needs_review"] is False   # no main cue, AI found bar 1
    assert rows[2]["status"] == "ok" and rows[2]["grid"] == "ai"
    assert rows[2]["tempo_bpm"] == pytest.approx(126.0)
    # AI said 2.5 s; locked onto the nearest Engine grid beat (5 x 0.46875 s)
    assert len(rows[1]["cues"]) == 6 and rows[1]["cues"][0]["time_display"] == "0:02.34"
    assert all(x["applied"] is None for x in job["rows"])

    a = c.post(f"/api/batch/{r['id']}/apply", json={"track_ids": [2], "targets": {"engine": True}}).get_json()
    assert a["count"] == 1 and a["applied"]["2"]["engine"]["ok"]
    assert [x["slot"] for x in c.get("/api/track/2").get_json()["cues"]] == [1, 2, 3, 4, 5, 6]
    assert c.get("/api/track/1").get_json()["cues"] == []           # not selected
    job = c.get(f"/api/batch/{r['id']}").get_json()
    assert {x["track_id"]: x["applied"] is not None for x in job["rows"]} == {1: False, 2: True}
    assert c.get("/api/track/2").get_json()["can_undo"] is True


def test_batch_skips_existing_and_reports(library):
    c = library["client"]
    c.post("/api/save", json={"track_id": 1, "targets": {"engine": True},
                              "cues": [{"slot": 1, "label": "", "color_name": "yellow", "time_seconds": 1}]})
    r = c.post("/api/batch", json={"type": "playlist", "name": "P", "template": "edm", "grid": "engine"}).get_json()
    job = _wait_job(c, r["id"])
    rows = {x["track_id"]: x for x in job["rows"]}
    assert rows[1]["status"] == "skip" and "existing" in rows[1]["reason"]
    assert rows[2]["status"] == "skip" and "grid" in rows[2]["reason"]
    assert c.post(f"/api/batch/{r['id']}/apply", json={"targets": {"engine": True}}).get_json()["count"] == 0
    assert c.post("/api/batch", json={"type": "playlist", "name": "nope"}).status_code == 404
    assert c.get("/api/batch/zzz").status_code == 404


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
