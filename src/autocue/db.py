import sqlite3
from pathlib import Path


def open_library(db_path: str, readonly: bool = True) -> sqlite3.Connection:
    path = Path(db_path).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"Database not found: {path}")
    uri = f"file:{path}{'?mode=ro' if readonly else ''}"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def get_schema_version(conn: sqlite3.Connection) -> str:
    cursor = conn.execute(
        "SELECT schemaVersionMajor, schemaVersionMinor, schemaVersionPatch "
        "FROM Information LIMIT 1"
    )
    row = cursor.fetchone()
    if row is None:
        raise RuntimeError("No schema version found in Information table")
    return f"{row[0]}.{row[1]}.{row[2]}"


def list_tracks(conn: sqlite3.Connection, search: str | None = None,
                limit: int | None = None) -> list[dict]:
    query = """
        SELECT
            t.id,
            t.title,
            t.artist,
            t.bpmAnalyzed as bpm,
            t.sampleRate,
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
            "sample_rate": row["sampleRate"],
            "quick_cues_blob": row["quickCues"],
            "beat_data_blob": row["beatData"],
        })
    return result
