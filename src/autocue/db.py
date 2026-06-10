"""Engine DJ SQLite database access layer.

Supports schema v2 (2.18.0 – 2.21.x) where blobs live in the Track table.
Schema v3 (3.0.0+) moves blobs to a PerformanceData table and is not yet supported.
"""

import sqlite3
from pathlib import Path


SUPPORTED_SCHEMA_MAJOR = 2

TESTED_SCHEMA_VERSIONS = {
    "2.18.0",
    "2.20.1", "2.20.2", "2.20.3",
    "2.21.0", "2.21.1", "2.21.2",
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

    if major < SUPPORTED_SCHEMA_MAJOR:
        raise RuntimeError(
            f"Schema {version_str} is v1 format (separate p.db). "
            f"Only v2 schemas (2.18.0+) are supported."
        )
    if major > SUPPORTED_SCHEMA_MAJOR:
        raise RuntimeError(
            f"Schema {version_str} is v3+ format (blobs in PerformanceData table). "
            f"Only v2 schemas (2.18.0 – 2.21.x) are currently supported."
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
