"""Undo journal for GUI saves.

Every save records what each target held *before* the write, as one JSON
file per save under <backups>/journal/. Undo restores the most recent
entry for a track and deletes it. Binary blobs are base64 in the JSON.

Snapshot shape (any key may be absent if that target wasn't written):
    {
      "track_id": 12, "time": "2026-09-28T10:00:00", "title": "…",
      "engine": {"quick_cues": b64 | null},
      "engine_grid": {"beat_data": b64 | null},
      "serato": {"path": "...mp3", "tag": b64 | null},
      "vdj": {"db": "...database.xml", "path": "...mp3", "cues": [...]},
      "rekordbox": {"xml": "...xml", "path": "...mp3", "cues": [...] | null,
                    "existed": bool}
    }
"""

import base64
import json
from datetime import datetime
from pathlib import Path


def journal_dir(backups_dir) -> Path:
    d = Path(backups_dir) / "journal"
    d.mkdir(parents=True, exist_ok=True)
    return d


def b64(data: bytes | None) -> str | None:
    return base64.b64encode(data).decode("ascii") if data is not None else None


def unb64(s: str | None) -> bytes | None:
    return base64.b64decode(s) if s is not None else None


def record(backups_dir, snapshot: dict) -> Path:
    snapshot = dict(snapshot)
    snapshot.setdefault("time", datetime.now().isoformat(timespec="seconds"))
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = journal_dir(backups_dir) / f"{snapshot['track_id']}_{stamp}.json"
    path.write_text(json.dumps(snapshot, indent=1), encoding="utf-8")
    return path


def entries(backups_dir, track_id: int) -> list[Path]:
    """Newest first."""
    d = journal_dir(backups_dir)
    return sorted(d.glob(f"{int(track_id)}_*.json"), reverse=True)


def latest(backups_dir, track_id: int):
    """(path, snapshot) for the most recent save, or None."""
    for p in entries(backups_dir, track_id):
        try:
            return p, json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return None


def discard(path: Path) -> None:
    try:
        Path(path).unlink()
    except OSError:
        pass
