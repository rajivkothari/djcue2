"""Write hot cues into VirtualDJ's database.xml.

VirtualDJ keeps all track metadata in an XML database:

    <VirtualDJ_Database Version="2024">
      <Song FilePath="C:\\Music\\track.mp3" FileSize="...">
        <Tags .../> <Infos .../> <Scan .../>
        <Poi Pos="12.345" Type="cue" Num="1" Name="Intro" Color="4294901760"/>
      </Song>
    </VirtualDJ_Database>

Pos is seconds, Num is the 1-based hot cue slot, Color is ARGB packed
into an unsigned 32-bit decimal. VirtualDJ's wiki notes external writes
are unsupported, so this module: refuses to write while VirtualDJ is
running, backs the file up first, and only touches <Poi Type="cue">
elements for the slots being written.
"""

import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path


def default_database_paths() -> list[Path]:
    """Where VirtualDJ keeps database.xml on this platform."""
    home = Path.home()
    candidates = [home / "Documents" / "VirtualDJ" / "database.xml"]
    if sys.platform == "win32":
        one_drive = os.environ.get("OneDrive")
        if one_drive:
            candidates.append(Path(one_drive) / "Documents" / "VirtualDJ" / "database.xml")
        local = os.environ.get("LOCALAPPDATA")
        if local:
            candidates.append(Path(local) / "VirtualDJ" / "database.xml")
    return candidates


def find_database() -> Path | None:
    for p in default_database_paths():
        if p.exists():
            return p
    return None


def is_virtualdj_running() -> bool:
    try:
        if sys.platform == "win32":
            out = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq virtualdj.exe", "/NH"],
                capture_output=True, text=True, timeout=5).stdout
            return "virtualdj" in out.lower()
        out = subprocess.run(["pgrep", "-fi", "virtualdj"],
                             capture_output=True, text=True, timeout=5)
        return out.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def argb_int(r: int, g: int, b: int, a: int = 255) -> int:
    return (a << 24) | (r << 16) | (g << 8) | b


def _norm(path: str) -> str:
    """Compare paths the way VirtualDJ's Windows/Mac databases need:
    case-insensitive, slash-agnostic, regardless of the host OS."""
    p = path.replace("\\", "/")
    while "//" in p:
        p = p.replace("//", "/")
    return p.rstrip("/").lower()


def _find_song(root: ET.Element, file_path: str) -> ET.Element | None:
    target = _norm(file_path)
    for song in root.findall("Song"):
        if _norm(song.get("FilePath", "")) == target:
            return song
    return None


def backup_database(db_path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = db_path.with_name(f"database_backup_{stamp}.xml")
    shutil.copy2(db_path, backup)
    return backup


def write_cues(db_path, file_path: str, cues: list[dict],
               make_backup: bool = True) -> dict:
    """Write hot cues for one track.

    cues: [{"num": 1-based slot, "seconds": float, "name": str,
            "color": (r, g, b)}]
    Existing <Poi Type="cue"> elements for those slot numbers are replaced;
    all other Poi (loops, beatgrid, other cue slots) are left alone. A
    <Song> element is created if the track isn't in the database yet.

    Returns {"written": n, "backup": path|None, "created_song": bool}.
    """
    db_path = Path(db_path)
    tree = ET.parse(db_path)
    root = tree.getroot()

    song = _find_song(root, file_path)
    created = False
    if song is None:
        song = ET.SubElement(root, "Song", FilePath=file_path)
        created = True

    nums = {int(c["num"]) for c in cues}
    for poi in list(song.findall("Poi")):
        if poi.get("Type") == "cue" and poi.get("Num", "").isdigit() \
                and int(poi.get("Num")) in nums:
            song.remove(poi)

    for c in sorted(cues, key=lambda c: int(c["num"])):
        r, g, b = c["color"]
        attrs = {
            "Pos": f"{float(c['seconds']):.6f}".rstrip("0").rstrip("."),
            "Type": "cue",
            "Num": str(int(c["num"])),
            "Color": str(argb_int(r, g, b)),
        }
        if c.get("name"):
            attrs["Name"] = c["name"]
        ET.SubElement(song, "Poi", attrs)

    backup = backup_database(db_path) if make_backup else None
    ET.indent(tree, space=" ")
    tree.write(db_path, encoding="UTF-8", xml_declaration=True)
    return {"written": len(cues), "backup": str(backup) if backup else None,
            "created_song": created}


def read_cues(db_path, file_path: str) -> list[dict]:
    root = ET.parse(Path(db_path)).getroot()
    song = _find_song(root, file_path)
    if song is None:
        return []
    out = []
    for poi in song.findall("Poi"):
        if poi.get("Type") != "cue":
            continue
        argb = int(poi.get("Color", "0") or 0)
        out.append({
            "num": int(poi.get("Num", "0") or 0),
            "seconds": float(poi.get("Pos", "0") or 0),
            "name": poi.get("Name", ""),
            "color": ((argb >> 16) & 255, (argb >> 8) & 255, argb & 255),
        })
    return sorted(out, key=lambda c: c["num"])
