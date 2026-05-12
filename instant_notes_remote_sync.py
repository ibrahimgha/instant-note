"""SSH receiver for Instant Notes remote SQLite replicas.

The laptop app sends one JSON sync item on stdin. This script upserts or deletes
the note in a SQLite database on the remote machine.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


APP_DIR = Path.home() / "instant-notes"
DB_PATH = APP_DIR / "instant-notes-replica.db"


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def session() -> sqlite3.Connection:
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def initialize(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS notes (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            content TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            title_status TEXT NOT NULL DEFAULT '',
            title_updated_at TEXT,
            last_closed_at TEXT,
            received_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sync_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            queue_id INTEGER,
            note_id TEXT NOT NULL,
            action TEXT NOT NULL,
            source_created_at TEXT,
            received_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS notes_created_at_idx ON notes(created_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS sync_events_note_idx ON sync_events(note_id, id)")


def upsert_note(conn: sqlite3.Connection, item: dict[str, object]) -> None:
    note = item.get("note")
    if not isinstance(note, dict):
        raise ValueError("upsert_note requires a note object")

    received_at = now_iso()
    conn.execute(
        """
        INSERT INTO notes (
            id,
            created_at,
            updated_at,
            content,
            title,
            title_status,
            title_updated_at,
            last_closed_at,
            received_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            created_at = excluded.created_at,
            updated_at = excluded.updated_at,
            content = excluded.content,
            title = excluded.title,
            title_status = excluded.title_status,
            title_updated_at = excluded.title_updated_at,
            last_closed_at = excluded.last_closed_at,
            received_at = excluded.received_at
        """,
        (
            str(note.get("id", "")),
            str(note.get("created_at", "")),
            str(note.get("updated_at", "")),
            str(note.get("content", "")),
            str(note.get("title", "")),
            str(note.get("title_status", "")),
            note.get("title_updated_at"),
            note.get("last_closed_at"),
            received_at,
        ),
    )


def delete_note(conn: sqlite3.Connection, item: dict[str, object]) -> None:
    note_id = str(item.get("note_id", "") or item.get("id", ""))
    if not note_id:
        raise ValueError("delete_note requires note_id")
    conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))


def record_event(conn: sqlite3.Connection, item: dict[str, object]) -> None:
    conn.execute(
        """
        INSERT INTO sync_events (queue_id, note_id, action, source_created_at, received_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            item.get("queue_id"),
            str(item.get("note_id", "")),
            str(item.get("action", "")),
            item.get("created_at"),
            now_iso(),
        ),
    )


def handle_item(conn: sqlite3.Connection, item: dict[str, object]) -> None:
    action = str(item.get("action", ""))
    if action == "upsert_note":
        upsert_note(conn, item)
    elif action == "delete_note":
        delete_note(conn, item)
    else:
        raise ValueError(f"Unsupported action: {action!r}")
    record_event(conn, item)


def main() -> int:
    raw = sys.stdin.read()
    item = json.loads(raw)
    if not isinstance(item, dict):
        raise ValueError("Expected a JSON object")

    with session() as conn:
        initialize(conn)
        handle_item(conn, item)

    print(json.dumps({"ok": True, "db": str(DB_PATH)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
