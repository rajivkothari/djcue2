"""Write hot cues (and the beat grid) to a rekordbox XML collection.

rekordbox has no writable database, but it imports a "rekordbox xml"
collection: point Preferences ▸ Advanced ▸ Database ▸ rekordbox xml at the
file, then drag tracks or playlists from the *rekordbox xml* node in the
browser into your collection. Cues, colours and the beat grid come along.

We keep one such file and upsert a <TRACK> per audio file:

    <DJ_PLAYLISTS Version="1.0.0">
      <PRODUCT Name="rekordbox" Version="6.0.0" Company="Pioneer DJ"/>
      <COLLECTION Entries="1">
        <TRACK TrackID="1" Name="…" Artist="…" AverageBpm="128.00"
               TotalTime="240" Location="file://localhost/C:/Music/x.mp3">
          <TEMPO Inizio="1.005" Bpm="128.00" Metro="4/4" Battito="1"/>
          <POSITION_MARK Name="Intro" Type="0" Start="1.005" Num="0"
                         Red="234" Green="197" Blue="50"/>
        </TRACK>
      </COLLECTION>
      <PLAYLISTS><NODE Type="0" Name="ROOT" Count="1">
        <NODE Name="autocue" Type="1" KeyType="0" Entries="1"><TRACK Key="1"/></NODE>
      </NODE></PLAYLISTS>
    </DJ_PLAYLISTS>

POSITION_MARK Num is the 0-based hot cue slot (-1 would be a memory cue);
Start is seconds. TEMPO Inizio is the first beat of the grid in seconds,
Battito its beat within the bar (1 = downbeat).
"""

import os
import shutil
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

PLAYLIST_NAME = "autocue"


def default_path(library_root) -> Path:
    return Path(library_root) / "autocue_rekordbox.xml"


def location_for(file_path: str) -> str:
    p = str(file_path).replace("\\", "/")
    if not p.startswith("/"):
        p = "/" + p                      # C:/Music/x.mp3 -> /C:/Music/x.mp3
    return "file://localhost" + urllib.parse.quote(p, safe="/:")


def path_from_location(location: str) -> str:
    p = urllib.parse.unquote(location.replace("file://localhost", "", 1))
    if len(p) > 2 and p[0] == "/" and p[2] == ":":   # /C:/… -> C:/…
        p = p[1:]
    return p


def _norm(path: str) -> str:
    # Locations always carry a leading slash; a relative input path doesn't.
    return path.replace("\\", "/").strip("/").lower()


def _empty_doc() -> ET.ElementTree:
    root = ET.Element("DJ_PLAYLISTS", Version="1.0.0")
    ET.SubElement(root, "PRODUCT", Name="rekordbox", Version="6.0.0", Company="Pioneer DJ")
    ET.SubElement(root, "COLLECTION", Entries="0")
    pl = ET.SubElement(root, "PLAYLISTS")
    top = ET.SubElement(pl, "NODE", Type="0", Name="ROOT", Count="1")
    ET.SubElement(top, "NODE", Name=PLAYLIST_NAME, Type="1", KeyType="0", Entries="0")
    return ET.ElementTree(root)


def _load(xml_path: Path) -> ET.ElementTree:
    if xml_path.exists() and xml_path.stat().st_size > 0:
        return ET.parse(xml_path)
    return _empty_doc()


def _find_track(collection, file_path: str):
    target = _norm(file_path)
    for t in collection.findall("TRACK"):
        if _norm(path_from_location(t.get("Location", ""))) == target:
            return t
    return None


def _playlist_node(root):
    pl = root.find("PLAYLISTS")
    if pl is None:
        pl = ET.SubElement(root, "PLAYLISTS")
    top = pl.find("NODE")
    if top is None:
        top = ET.SubElement(pl, "NODE", Type="0", Name="ROOT", Count="1")
    for n in top.findall("NODE"):
        if n.get("Name") == PLAYLIST_NAME:
            return n
    return ET.SubElement(top, "NODE", Name=PLAYLIST_NAME, Type="1", KeyType="0", Entries="0")


def _save(tree: ET.ElementTree, xml_path: Path, make_backup: bool):
    root = tree.getroot()
    coll = root.find("COLLECTION")
    coll.set("Entries", str(len(coll.findall("TRACK"))))
    node = _playlist_node(root)
    node.set("Entries", str(len(node.findall("TRACK"))))
    backup = None
    if make_backup and xml_path.exists():
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = xml_path.with_name(f"{xml_path.stem}_backup_{stamp}.xml")
        shutil.copy2(xml_path, backup)
    ET.indent(tree, space="  ")
    tree.write(xml_path, encoding="UTF-8", xml_declaration=True)
    return backup


def write_cues(xml_path, file_path: str, cues: list[dict], *, title: str = "",
               artist: str = "", bpm: float | None = None,
               duration_seconds: float | None = None,
               grid: dict | None = None, make_backup: bool = True) -> dict:
    """Upsert one track's hot cues (replacing all of its hot cues).

    cues: [{"num": 1-based slot, "seconds", "name", "color": (r, g, b)}]
    grid: optional {"first_beat_seconds", "bpm"} written as the TEMPO.
    """
    xml_path = Path(xml_path)
    tree = _load(xml_path)
    root = tree.getroot()
    coll = root.find("COLLECTION")

    track = _find_track(coll, file_path)
    created = track is None
    if created:
        ids = [int(t.get("TrackID", 0)) for t in coll.findall("TRACK")]
        track = ET.SubElement(coll, "TRACK", TrackID=str(max(ids, default=0) + 1),
                              Location=location_for(file_path))
    if title:
        track.set("Name", title)
    if artist:
        track.set("Artist", artist)
    if bpm:
        track.set("AverageBpm", f"{bpm:.2f}")
    if duration_seconds:
        track.set("TotalTime", str(int(round(duration_seconds))))

    for pm in list(track.findall("POSITION_MARK")):
        if pm.get("Num", "-1") != "-1":            # keep memory cues
            track.remove(pm)
    for c in sorted(cues, key=lambda c: int(c["num"])):
        r, g, b = c["color"]
        ET.SubElement(track, "POSITION_MARK", Name=c.get("name", ""), Type="0",
                      Start=f"{float(c['seconds']):.3f}", Num=str(int(c["num"]) - 1),
                      Red=str(r), Green=str(g), Blue=str(b))
    if grid and grid.get("bpm"):
        for t in list(track.findall("TEMPO")):
            track.remove(t)
        ET.SubElement(track, "TEMPO", Inizio=f"{float(grid['first_beat_seconds']):.3f}",
                      Bpm=f"{float(grid['bpm']):.2f}", Metro="4/4", Battito="1")

    node = _playlist_node(root)
    key = track.get("TrackID")
    if not any(t.get("Key") == key for t in node.findall("TRACK")):
        ET.SubElement(node, "TRACK", Key=key)

    backup = _save(tree, xml_path, make_backup)
    return {"written": len(cues), "created_track": created,
            "backup": str(backup) if backup else None, "path": str(xml_path)}


def read_cues(xml_path, file_path: str) -> list[dict] | None:
    """Hot cues for a file, or None if the file isn't in the collection."""
    xml_path = Path(xml_path)
    if not xml_path.exists():
        return None
    track = _find_track(ET.parse(xml_path).getroot().find("COLLECTION"), file_path)
    if track is None:
        return None
    out = []
    for pm in track.findall("POSITION_MARK"):
        num = int(pm.get("Num", "-1"))
        if num < 0:
            continue
        out.append({"num": num + 1, "seconds": float(pm.get("Start", 0)),
                    "name": pm.get("Name", ""),
                    "color": (int(pm.get("Red", 0)), int(pm.get("Green", 0)),
                              int(pm.get("Blue", 0)))})
    return sorted(out, key=lambda c: c["num"])


def remove_track(xml_path, file_path: str, make_backup: bool = True) -> bool:
    xml_path = Path(xml_path)
    if not xml_path.exists():
        return False
    tree = ET.parse(xml_path)
    root = tree.getroot()
    coll = root.find("COLLECTION")
    track = _find_track(coll, file_path)
    if track is None:
        return False
    key = track.get("TrackID")
    coll.remove(track)
    node = _playlist_node(root)
    for t in list(node.findall("TRACK")):
        if t.get("Key") == key:
            node.remove(t)
    _save(tree, xml_path, make_backup)
    return True
