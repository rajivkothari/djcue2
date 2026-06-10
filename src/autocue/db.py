"""Engine DJ SQLite database access layer.

Supports schema v2 (2.18.0 – 2.21.x) where blobs live in the Track table,
and schema v3 (3.0.0+) where blobs live in the PerformanceData table.
"""

import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path


SUPPORTED_SCHEMA_MAJORS = {2, 3}

TESTED_SCHEMA_VERSIONS = {
    "2.18.0",
    "2.20.1", "2.20.2", "2.20.3",
    "2.21.0", "2.21.1", "2.21.2",
    "3.0.0", "3.0.1", "3.0.2",
}


def open_library(db_path: str, readonly: bool = True) -> sqlite3.Connection:
    path = Path(db_path).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"Database not found: {path}")
    uri = f"file:{path}{'?mode=ro' if readonly else ''}"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def get_schema_version(conn: sqlite3.Connection) -> tuple[int, int, int]:
    """Returns (major, minor, patch) tuple."""
    cursor = conn.execute(
        "SELECT schemaVersionMajor, schemaVersionMinor, schemaVersionPatch "
        "FROM Information LIMIT 1"
    )
    row = cursor.fetchone()
    if row is None:
        raise RuntimeError("No schema version found in Information table")
    return (row[0], row[1], row[2])


def get_schema_version_string(conn: sqlite3.Connection) -> str:
    major, minor, patch = get_schema_version(conn)
    return f"{major}.{minor}.{patch}"


def check_schema(conn: sqlite3.Connection) -> str:
    """Check schema compatibility. Returns version string or raises."""
    major, minor, patch = get_schema_version(conn)
    version_str = f"{major}.{minor}.{patch}"

    if major not in SUPPORTED_SCHEMA_MAJORS:
        if major < min(SUPPORTED_SCHEMA_MAJORS):
            raise RuntimeError(
                f"Schema {version_str} is v1 format (separate p.db). "
                f"Only v2+ schemas are supported."
            )
        raise RuntimeError(
            f"Schema {version_str} is not supported. "
            f"Supported major versions: {sorted(SUPPORTED_SCHEMA_MAJORS)}"
        )

    if version_str not in TESTED_SCHEMA_VERSIONS:
        raise RuntimeError(
            f"Schema {version_str} has not been tested. "
            f"Tested versions: {', '.join(sorted(TESTED_SCHEMA_VERSIONS))}. "
            f"Add it to TESTED_SCHEMA_VERSIONS after verifying round-trip."
        )
    return version_str


def list_tracks(conn: sqlite3.Connection, search: str | None = None,
                limit: int | None = None) -> list[dict]:
    major, _, _ = get_schema_version(conn)

    if major >= 3:
        query = """
            SELECT
                t.id,
                t.title,
                t.artist,
                t.bpmAnalyzed as bpm,
                pd.trackData,
                pd.quickCues,
                pd.beatData
            FROM Track t
            LEFT JOIN PerformanceData pd ON pd.trackId = t.id
        """
    else:
        query = """
            SELECT
                t.id,
                t.title,
                t.artist,
                t.bpmAnalyzed as bpm,
                t.trackData,
                t.quickCues,
                t.beatData
            FROM Track t
        """

    params: list = []

    if search is not None:
        try:
            track_id = int(search)
            query += " WHERE t.id = ?"
            params.append(track_id)
        except ValueError:
            query += " WHERE t.title LIKE ? OR t.artist LIKE ?"
            params.extend([f"%{search}%", f"%{search}%"])

    if limit is not None:
        query += f" LIMIT {limit}"

    rows = conn.execute(query, params).fetchall()
    result = []
    for row in rows:
        result.append({
            "id": row["id"],
            "title": row["title"] or "(untitled)",
            "artist": row["artist"] or "(unknown)",
            "bpm": row["bpm"],
            "track_data_blob": row["trackData"],
            "quick_cues_blob": row["quickCues"],
            "beat_data_blob": row["beatData"],
        })
    return result


def is_engine_dj_running() -> bool:
    """Check if Engine DJ is running. Works on Windows and macOS."""
    try:
        if sys.platform == "win32":
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq Engine DJ.exe", "/NH"],
                capture_output=True, text=True, timeout=5,
            )
            return "Engine DJ.exe" in result.stdout
        elif sys.platform == "darwin":
            result = subprocess.run(
                ["pgrep", "-f", "Engine DJ"],
                capture_output=True, text=True, timeout=5,
            )
            return result.returncode == 0
        else:
            result = subprocess.run(
                ["pgrep", "-f", "[Ee]ngine.*[Dd][Jj]"],
                capture_output=True, text=True, timeout=5,
            )
            return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def backup_library(db_path: str) -> Path:
    """Create a timestamped backup of m.db. Returns backup path."""
    src = Path(db_path)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = src.parent / f"m_backup_{timestamp}.db"
    shutil.copy2(src, backup)
    return backup


def write_quick_cues(conn: sqlite3.Connection, track_id: int,
                     blob: bytes) -> None:
    """Write a quickCues blob for a track."""
    major, _, _ = get_schema_version(conn)
    if major >= 3:
        conn.execute(
            "UPDATE PerformanceData SET quickCues = ? WHERE trackId = ?",
            (blob, track_id),
        )
    else:
        conn.execute(
            "UPDATE Track SET quickCues = ? WHERE id = ?",
            (blob, track_id),
        )
    conn.commit()
