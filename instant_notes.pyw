"""Instant Notes: a tiny Windows background note taker.

Run with pythonw.exe. F9 opens a blank note; F10 opens the note list.
"""

from __future__ import annotations

import ctypes
import json
import os
import queue
import re
import sqlite3
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import traceback
import urllib.error
import urllib.request
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import messagebox


APP_NAME = "Instant Notes"
APP_USER_MODEL_ID = "ibrah.instantnotes"
EDITOR_BACKGROUND = "#f7f7f5"
EDITOR_FOREGROUND = "#151515"
EDITOR_INSERT_BACKGROUND = "#111111"
EDITOR_FONT_SIZE = 12
EDITOR_FONT_FALLBACKS = (
    "Cascadia Mono",
    "Cascadia Code",
    "Consolas",
    "Lucida Console",
    "Courier New",
)
APP_DIR = Path(__file__).resolve().parent
ENV_PATH = APP_DIR / ".env"
DB_PATH = APP_DIR / "instant-notes.db"
CONFIG_PATH = APP_DIR / "instant-notes.json"
LOG_PATH = APP_DIR / "instant-notes.log"
RECOVERY_DIR = APP_DIR / "recovery"
ICON_PATH = APP_DIR / "note-icon.png"
ICON_ICO_PATH = APP_DIR / "note-icon.ico"

HOTKEY_NEW_ID = 9401
HOTKEY_LIST_ID = 9402

WM_HOTKEY = 0x0312
WM_APP = 0x8000
WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
MOD_NOREPEAT = 0x4000

VK_F9 = 0x78
VK_F10 = 0x79
VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1
VK_MEDIA_PLAY_PAUSE = 0xB3


def load_env_file(path: Path) -> None:
    if not path.exists():
        return

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key:
            os.environ.setdefault(key, value)


load_env_file(ENV_PATH)

F9_SCAN_CODE = int(os.environ.get("INSTANT_NOTES_F9_SCAN_CODE", "67"))
F10_SCAN_CODE = int(os.environ.get("INSTANT_NOTES_F10_SCAN_CODE", "68"))

OPENAI_API_KEY_ENV = "OPENAI_API_KEY"
OPENAI_MODEL_ENV = "OPENAI_NOTES_MODEL"
DEFAULT_OPENAI_MODEL = "gpt-4.1-mini"
SYNC_URL_ENV = "INSTANT_NOTES_SYNC_URL"
SSH_TARGET_ENV = "INSTANT_NOTES_SSH_TARGET"
SSH_SCRIPT_ENV = "INSTANT_NOTES_SSH_SCRIPT"
SSH_TIMEOUT_ENV = "INSTANT_NOTES_SSH_TIMEOUT"
SSH_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

ERROR_ALREADY_EXISTS = 183
PROCESS_PER_MONITOR_DPI_AWARE = 2
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
DPI_AWARENESS_ALREADY_SET = -2147024891
DPI_AWARENESS_CONFIGURED = False


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("message", ctypes.c_uint),
        ("wParam", ctypes.c_size_t),
        ("lParam", ctypes.c_size_t),
        ("time", ctypes.c_uint32),
        ("pt", POINT),
    ]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", ctypes.c_ulong),
        ("scanCode", ctypes.c_ulong),
        ("flags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
shell32 = ctypes.windll.shell32

LOW_LEVEL_KEYBOARD_PROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t,
    ctypes.c_int,
    ctypes.c_size_t,
    ctypes.c_size_t,
)

user32.RegisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
user32.RegisterHotKey.restype = ctypes.c_bool
user32.UnregisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int]
user32.UnregisterHotKey.restype = ctypes.c_bool
user32.GetMessageW.argtypes = [
    ctypes.POINTER(MSG),
    ctypes.c_void_p,
    ctypes.c_uint,
    ctypes.c_uint,
]
user32.GetMessageW.restype = ctypes.c_int
user32.PostThreadMessageW.argtypes = [ctypes.c_ulong, ctypes.c_uint, ctypes.c_size_t, ctypes.c_size_t]
user32.PostThreadMessageW.restype = ctypes.c_bool
user32.SetWindowsHookExW.argtypes = [
    ctypes.c_int,
    LOW_LEVEL_KEYBOARD_PROC,
    ctypes.c_void_p,
    ctypes.c_ulong,
]
user32.SetWindowsHookExW.restype = ctypes.c_void_p
user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_size_t, ctypes.c_size_t]
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
user32.UnhookWindowsHookEx.restype = ctypes.c_bool
kernel32.GetCurrentThreadId.restype = ctypes.c_ulong
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
kernel32.CreateMutexW.restype = ctypes.c_void_p
kernel32.GetLastError.restype = ctypes.c_ulong
shell32.SetCurrentProcessExplicitAppUserModelID.argtypes = [ctypes.c_wchar_p]
shell32.SetCurrentProcessExplicitAppUserModelID.restype = ctypes.c_long


@dataclass(frozen=True)
class NoteRecord:
    id: str
    created_at: str
    updated_at: str
    content: str
    title: str
    title_status: str


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def parse_iso(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.now().astimezone()


def display_date(value: str, with_time: bool = True) -> str:
    fmt = "%Y-%m-%d %H:%M" if with_time else "%Y-%m-%d"
    return parse_iso(value).strftime(fmt)


def display_list_timestamp(value: str) -> str:
    created = parse_iso(value)
    hour = created.hour % 12 or 12
    minute = f"{created.minute:02d}"
    period = "AM" if created.hour < 12 else "PM"
    return f"{created:%d/%m/%Y} {hour}:{minute} {period}"


def collapse_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def trim_title(value: str, max_length: int = 72) -> str:
    value = collapse_whitespace(value.strip().strip("\"'`"))
    value = re.sub(r"^title\s*:\s*", "", value, flags=re.IGNORECASE)
    value = re.sub(r"^\d{4}-\d{2}-\d{2}\s*[-:]\s*", "", value)
    value = value.strip(" -:;,.")
    if len(value) <= max_length:
        return value
    return value[: max_length - 1].rstrip(" -:;,.") + "..."


def content_topic(content: str) -> str:
    for line in content.splitlines():
        candidate = trim_title(line, max_length=58)
        if candidate:
            return candidate

    candidate = trim_title(content, max_length=58)
    return candidate or "Empty note"


def make_full_title(created_at: str, topic: str) -> str:
    return f"{display_date(created_at, with_time=False)} - {trim_title(topic) or 'Untitled'}"


def fallback_title(content: str, created_at: str) -> str:
    return make_full_title(created_at, content_topic(content))


def note_list_name(note: NoteRecord) -> str:
    title = trim_title(note.title)
    if title:
        return title
    return trim_title(content_topic(note.content)) or "Untitled"


def note_list_row(note: NoteRecord) -> str:
    return f"{note_list_name(note)} {display_list_timestamp(note.created_at)}"


def log_message(message: str) -> None:
    try:
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(f"[{now_iso()}] {message}\n")
    except OSError:
        pass


def log_exception(message: str, exc: BaseException | None = None) -> None:
    details = traceback.format_exc()
    if details.strip() == "NoneType: None" and exc is not None:
        details = f"{type(exc).__name__}: {exc}\n"
    log_message(f"{message}\n{details.rstrip()}")


def write_recovery_note(note_id: str, content: str) -> Path | None:
    if is_empty_note(content):
        return None

    try:
        RECOVERY_DIR.mkdir(parents=True, exist_ok=True)
        filename = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{note_id}.txt"
        path = RECOVERY_DIR / filename
        path.write_text(content, encoding="utf-8")
        return path
    except OSError as exc:
        log_exception(f"Could not write recovery note for {note_id}", exc)
        return None


def set_windows_app_id() -> None:
    try:
        shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception as exc:
        log_exception("Could not set Windows AppUserModelID", exc)


def set_dpi_awareness() -> None:
    global DPI_AWARENESS_CONFIGURED
    if DPI_AWARENESS_CONFIGURED:
        return

    try:
        setter = user32.SetProcessDpiAwarenessContext
        setter.argtypes = [ctypes.c_void_p]
        setter.restype = ctypes.c_bool
        context = ctypes.c_void_p(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
        if setter(context):
            DPI_AWARENESS_CONFIGURED = True
            return
    except Exception:
        pass

    try:
        shcore = ctypes.windll.shcore
        setter = shcore.SetProcessDpiAwareness
        setter.argtypes = [ctypes.c_int]
        setter.restype = ctypes.c_long
        result = setter(PROCESS_PER_MONITOR_DPI_AWARE)
        if result in {0, DPI_AWARENESS_ALREADY_SET}:
            DPI_AWARENESS_CONFIGURED = True
            return
    except Exception:
        pass

    try:
        setter = user32.SetProcessDPIAware
        setter.restype = ctypes.c_bool
        if setter():
            DPI_AWARENESS_CONFIGURED = True
            return
    except Exception as exc:
        log_exception("Could not set DPI awareness", exc)


def configure_tk_scaling(root: tk.Misc) -> None:
    try:
        root.tk.call("tk", "scaling", root.winfo_fpixels("1i") / 72.0)
    except tk.TclError as exc:
        log_exception("Could not configure Tk scaling", exc)


def resolve_editor_font(root: tk.Misc) -> tuple[str, int]:
    available = {name.lower(): name for name in tkfont.families(root)}
    for candidate in EDITOR_FONT_FALLBACKS:
        match = available.get(candidate.lower())
        if match:
            return match, EDITOR_FONT_SIZE
    return "TkFixedFont", EDITOR_FONT_SIZE


def is_empty_note(content: str) -> bool:
    return not content.strip()


def is_word_char(char: str) -> bool:
    return char.isalnum() or char == "_"


def previous_word_delete_count(value: str) -> int:
    if not value:
        return 0

    index = len(value)
    while index > 0 and value[index - 1].isspace():
        index -= 1

    if index == 0:
        return len(value)

    if is_word_char(value[index - 1]):
        while index > 0 and is_word_char(value[index - 1]):
            index -= 1
    else:
        while index > 0 and not value[index - 1].isspace() and not is_word_char(value[index - 1]):
            index -= 1

    return len(value) - index


def default_hotkey_triggers() -> dict[str, list[dict[str, int]]]:
    return {
        "new": [
            {"vk": VK_F9},
            {"scan": F9_SCAN_CODE},
            {"vk": VK_MEDIA_PREV_TRACK},
        ],
        "list": [
            {"vk": VK_F10},
            {"scan": F10_SCAN_CODE},
            {"vk": VK_MEDIA_PLAY_PAUSE},
        ],
    }


def normalize_trigger(value: object) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None

    trigger: dict[str, int] = {}
    for key in ("vk", "scan"):
        raw = value.get(key)
        if raw is None:
            continue
        try:
            number = int(str(raw), 0)
        except ValueError:
            continue
        if 0 <= number <= 255:
            trigger[key] = number

    return trigger or None


def load_hotkey_triggers() -> dict[str, list[dict[str, int]]]:
    triggers = default_hotkey_triggers()
    if not CONFIG_PATH.exists():
        return triggers

    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return triggers

    raw_hotkeys = data.get("hotkeys") if isinstance(data, dict) else None
    if not isinstance(raw_hotkeys, dict):
        return triggers

    for kind in ("new", "list"):
        custom = raw_hotkeys.get(kind)
        if not isinstance(custom, list):
            continue

        normalized = [trigger for trigger in (normalize_trigger(item) for item in custom) if trigger]
        if normalized:
            triggers[kind] = normalized

    return triggers


def matches_trigger(key: KBDLLHOOKSTRUCT, triggers: list[dict[str, int]]) -> bool:
    vk_code = int(key.vkCode)
    scan_code = int(key.scanCode)
    for trigger in triggers:
        expected_vk = trigger.get("vk")
        expected_scan = trigger.get("scan")
        if expected_vk is not None and expected_vk != vk_code:
            continue
        if expected_scan is not None and expected_scan != scan_code:
            continue
        return True
    return False


def row_to_note(row: sqlite3.Row) -> NoteRecord:
    return NoteRecord(
        id=row["id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        content=row["content"],
        title=row["title"],
        title_status=row["title_status"],
    )


class NoteStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.sync_event: threading.Event | None = None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()
        self.delete_empty_notes()

    def set_sync_event(self, sync_event: threading.Event) -> None:
        self.sync_event = sync_event

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def session(self) -> sqlite3.Connection:
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.session() as conn:
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
                    title_status TEXT NOT NULL DEFAULT 'pending',
                    title_updated_at TEXT,
                    sync_status TEXT NOT NULL DEFAULT 'pending',
                    last_closed_at TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sync_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    note_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    processed_at TEXT,
                    last_error TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS notes_created_at_idx ON notes(created_at DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS sync_queue_pending_idx ON sync_queue(processed_at, id)"
            )

    def create_note(self) -> NoteRecord:
        note_id = uuid.uuid4().hex
        timestamp = now_iso()
        with self.session() as conn:
            conn.execute(
                """
                INSERT INTO notes (id, created_at, updated_at, content, title, title_status)
                VALUES (?, ?, ?, '', '', 'pending')
                """,
                (note_id, timestamp, timestamp),
            )
            row = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        return row_to_note(row)

    def get_note(self, note_id: str) -> NoteRecord | None:
        with self.session() as conn:
            row = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        return row_to_note(row) if row else None

    def list_notes(self) -> list[NoteRecord]:
        with self.session() as conn:
            rows = conn.execute(
                "SELECT * FROM notes ORDER BY created_at DESC, id DESC"
            ).fetchall()
        return [row_to_note(row) for row in rows if not is_empty_note(row["content"])]

    def delete_note(self, note_id: str) -> None:
        timestamp = now_iso()
        with self.session() as conn:
            row = conn.execute("SELECT id FROM notes WHERE id = ?", (note_id,)).fetchone()
            conn.execute(
                """
                DELETE FROM sync_queue
                WHERE note_id = ?
                  AND processed_at IS NULL
                  AND action = 'upsert_note'
                """,
                (note_id,),
            )
            if row:
                self.enqueue_delete_sync(conn, note_id, timestamp)
            conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))

    def delete_empty_notes(self) -> int:
        with self.session() as conn:
            rows = conn.execute("SELECT id, content FROM notes").fetchall()
            empty_ids = [row["id"] for row in rows if is_empty_note(row["content"])]
            for note_id in empty_ids:
                timestamp = now_iso()
                conn.execute(
                    """
                    DELETE FROM sync_queue
                    WHERE note_id = ?
                      AND processed_at IS NULL
                      AND action = 'upsert_note'
                    """,
                    (note_id,),
                )
                self.enqueue_delete_sync(conn, note_id, timestamp)
                conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        return len(empty_ids)

    def save_content(self, note_id: str, content: str, closed: bool = False) -> None:
        timestamp = now_iso()
        with self.session() as conn:
            if closed:
                conn.execute(
                    """
                    UPDATE notes
                    SET content = ?,
                        updated_at = ?,
                        last_closed_at = ?,
                        title_status = 'pending',
                        sync_status = 'pending'
                    WHERE id = ?
                    """,
                    (content, timestamp, timestamp, note_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE notes
                    SET content = ?,
                        updated_at = ?,
                        sync_status = 'pending'
                    WHERE id = ?
                    """,
                    (content, timestamp, note_id),
                )
            if not is_empty_note(content):
                self.enqueue_sync(conn, note_id, "upsert_note", timestamp)

    def update_title(self, note_id: str, title: str, status: str) -> None:
        timestamp = now_iso()
        with self.session() as conn:
            conn.execute(
                """
                UPDATE notes
                SET title = ?,
                    title_status = ?,
                    title_updated_at = ?,
                    updated_at = ?,
                    sync_status = 'pending'
                WHERE id = ?
                """,
                (title, status, timestamp, timestamp, note_id),
            )
            self.enqueue_sync(conn, note_id, "upsert_note", timestamp)

    def enqueue_sync(
        self,
        conn: sqlite3.Connection,
        note_id: str,
        action: str,
        timestamp: str,
    ) -> None:
        row = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        if not row:
            return

        payload = {
            "id": row["id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "content": row["content"],
            "title": row["title"],
            "title_status": row["title_status"],
            "title_updated_at": row["title_updated_at"],
            "last_closed_at": row["last_closed_at"],
        }
        conn.execute(
            """
            DELETE FROM sync_queue
            WHERE note_id = ?
              AND action = ?
              AND processed_at IS NULL
            """,
            (note_id, action),
        )
        conn.execute(
            """
            INSERT INTO sync_queue (note_id, action, payload, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (note_id, action, json.dumps(payload, ensure_ascii=False), timestamp),
        )
        self.wake_sync()

    def enqueue_delete_sync(
        self,
        conn: sqlite3.Connection,
        note_id: str,
        timestamp: str,
    ) -> None:
        payload = {
            "id": note_id,
            "deleted_at": timestamp,
        }
        conn.execute(
            """
            DELETE FROM sync_queue
            WHERE note_id = ?
              AND processed_at IS NULL
            """,
            (note_id,),
        )
        conn.execute(
            """
            INSERT INTO sync_queue (note_id, action, payload, created_at)
            VALUES (?, 'delete_note', ?, ?)
            """,
            (note_id, json.dumps(payload, ensure_ascii=False), timestamp),
        )
        self.wake_sync()

    def enqueue_all_notes_for_sync(self) -> int:
        timestamp = now_iso()
        with self.session() as conn:
            rows = conn.execute("SELECT id, content FROM notes").fetchall()
            note_ids = [row["id"] for row in rows if not is_empty_note(row["content"])]
            for note_id in note_ids:
                self.enqueue_sync(conn, note_id, "upsert_note", timestamp)
        return len(note_ids)

    def wake_sync(self) -> None:
        if self.sync_event is not None:
            self.sync_event.set()

    def pending_sync_items(self, limit: int = 20) -> list[sqlite3.Row]:
        with self.session() as conn:
            return conn.execute(
                """
                SELECT * FROM sync_queue
                WHERE processed_at IS NULL
                ORDER BY id ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

    def mark_sync_done(self, item_id: int) -> None:
        with self.session() as conn:
            row = conn.execute(
                "SELECT note_id, action FROM sync_queue WHERE id = ?",
                (item_id,),
            ).fetchone()
            conn.execute(
                "UPDATE sync_queue SET processed_at = ?, last_error = NULL WHERE id = ?",
                (now_iso(), item_id),
            )
            if row and row["action"] == "upsert_note":
                conn.execute(
                    "UPDATE notes SET sync_status = 'synced' WHERE id = ?",
                    (row["note_id"],),
                )

    def mark_sync_error(self, item_id: int, error: str) -> None:
        with self.session() as conn:
            conn.execute(
                "UPDATE sync_queue SET last_error = ? WHERE id = ?",
                (error[:500], item_id),
            )


class TitleWorker:
    def __init__(self, store: NoteStore) -> None:
        self.store = store
        self.jobs: queue.Queue[str | None] = queue.Queue()
        self.thread = threading.Thread(target=self.run, name="InstantNotesTitleWorker", daemon=True)
        self.thread.start()

    def enqueue(self, note_id: str) -> None:
        self.jobs.put(note_id)

    def stop(self) -> None:
        self.jobs.put(None)

    def run(self) -> None:
        while True:
            note_id = self.jobs.get()
            if note_id is None:
                return

            try:
                note = self.store.get_note(note_id)
                if not note:
                    continue
                if is_empty_note(note.content):
                    self.store.delete_note(note.id)
                    continue

                title, status = self.generate_title(note.content, note.created_at)
                self.store.update_title(note.id, title, status)
            except Exception as exc:
                log_exception(f"Title worker failed for note {note_id}", exc)

    def generate_title(self, content: str, created_at: str) -> tuple[str, str]:
        fallback = fallback_title(content, created_at)
        api_key = os.environ.get(OPENAI_API_KEY_ENV, "").strip()
        if not api_key:
            return fallback, "missing_api_key"

        prompt = (
            "Create a very short plain-text title for this note. "
            "Use the note content and the creation date for context. "
            "Return only a topic title, 2 to 8 words, with no date, no quotes, no markdown.\n\n"
            f"Created: {display_date(created_at)}\n"
            f"Note:\n{content[:8000]}"
        )
        body = {
            "model": os.environ.get(OPENAI_MODEL_ENV, DEFAULT_OPENAI_MODEL),
            "input": prompt,
            "max_output_tokens": 32,
        }
        request = urllib.request.Request(
            "https://api.openai.com/v1/responses",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=18) as response:
                data = json.loads(response.read().decode("utf-8"))
            topic = self.extract_output_text(data)
            if not topic:
                return fallback, "empty_openai_response"
            return make_full_title(created_at, topic), "openai"
        except Exception as exc:
            return fallback, f"openai_error:{str(exc)[:180]}"

    def extract_output_text(self, data: dict[str, object]) -> str:
        output_text = data.get("output_text")
        if isinstance(output_text, str):
            return trim_title(output_text)

        parts: list[str] = []
        output = data.get("output")
        if isinstance(output, list):
            for item in output:
                if not isinstance(item, dict):
                    continue
                content = item.get("content")
                if not isinstance(content, list):
                    continue
                for content_item in content:
                    if not isinstance(content_item, dict):
                        continue
                    text = content_item.get("text")
                    if isinstance(text, str):
                        parts.append(text)
        return trim_title(" ".join(parts))


class SyncWorker:
    def __init__(self, store: NoteStore) -> None:
        self.store = store
        self.endpoint = os.environ.get(SYNC_URL_ENV, "").strip()
        self.ssh_target = os.environ.get(SSH_TARGET_ENV, "").strip()
        self.ssh_script = os.environ.get(
            SSH_SCRIPT_ENV,
            "~/instant-notes/instant_notes_remote_sync.py",
        ).strip()
        self.ssh_timeout = int(os.environ.get(SSH_TIMEOUT_ENV, "20"))
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()
        self.store.set_sync_event(self.wake_event)
        self.thread: threading.Thread | None = None
        self.enabled = bool(self.endpoint or self.ssh_target)
        if self.enabled:
            self.thread = threading.Thread(target=self.run, name="InstantNotesSyncWorker", daemon=True)
            self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.wake_event.set()

    def run(self) -> None:
        while not self.stop_event.is_set():
            try:
                items = self.store.pending_sync_items()
            except Exception as exc:
                log_exception("Sync worker could not read pending items", exc)
                self.wait_for_next_sync(8)
                continue

            if not items:
                self.wait_for_next_sync(8)
                continue

            for item in items:
                if self.stop_event.is_set():
                    return
                try:
                    if item["action"] == "upsert_note" and self.store.get_note(item["note_id"]) is None:
                        self.store.mark_sync_done(int(item["id"]))
                        continue
                    self.push_item(item)
                    self.store.mark_sync_done(int(item["id"]))
                except Exception as exc:
                    self.store.mark_sync_error(int(item["id"]), str(exc))
                    self.wait_for_next_sync(2)

    def wait_for_next_sync(self, timeout: float) -> None:
        self.wake_event.wait(timeout)
        self.wake_event.clear()

    def push_item(self, item: sqlite3.Row) -> None:
        payload = {
            "queue_id": item["id"],
            "note_id": item["note_id"],
            "action": item["action"],
            "created_at": item["created_at"],
            "note": json.loads(item["payload"]),
        }
        if item["action"] == "delete_note":
            payload["id"] = item["note_id"]
            payload["deleted_at"] = payload["note"].get("deleted_at")

        if self.ssh_target:
            self.push_ssh_item(payload)
            return

        if not self.endpoint:
            return

        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=12) as response:
                response.read()
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code}: {body[:300]}") from exc

    def push_ssh_item(self, payload: dict[str, object]) -> None:
        completed = subprocess.run(
            ["ssh", self.ssh_target, "python3", self.ssh_script],
            input=json.dumps(payload, ensure_ascii=False),
            capture_output=True,
            text=True,
            timeout=self.ssh_timeout,
            creationflags=SSH_NO_WINDOW,
        )
        if completed.returncode != 0:
            message = (completed.stderr or completed.stdout or "ssh sync failed").strip()
            raise RuntimeError(message)


class NoteWindow:
    def __init__(
        self,
        app: "InstantNotesApp",
        note: NoteRecord,
    ) -> None:
        self.app = app
        self.note = note
        self.closed = False
        self.dirty = False
        self.save_after: str | None = None

        self.window = tk.Toplevel(app.root)
        app.apply_icon(self.window)
        self.window.title(note.title or "Instant Note")
        self.window.geometry("760x520")
        self.window.configure(bg=EDITOR_BACKGROUND)
        self.window.protocol("WM_DELETE_WINDOW", self.close)

        self.text = tk.Text(
            self.window,
            undo=True,
            wrap="word",
            font=app.editor_font,
            bg=EDITOR_BACKGROUND,
            fg=EDITOR_FOREGROUND,
            insertbackground=EDITOR_INSERT_BACKGROUND,
            selectbackground="#d9e8ff",
            selectforeground=EDITOR_FOREGROUND,
            borderwidth=0,
            highlightthickness=0,
            padx=10,
            pady=10,
        )
        self.text.pack(fill="both", expand=True)
        self.text.insert("1.0", note.content)
        self.text.edit_modified(False)
        self.text.bind("<<Modified>>", self.on_modified)
        self.text.bind("<Escape>", lambda _event: self.close())
        self.text.bind("<Control-BackSpace>", self.delete_previous_word)
        self.window.bind("<Control-s>", lambda _event: self.save_now())

        self.window.after(1, self.focus)

    def focus(self) -> None:
        if self.closed:
            return
        self.window.deiconify()
        self.window.lift()
        self.window.focus_force()
        self.text.focus_set()

    def on_modified(self, _event: tk.Event) -> None:
        if not self.text.edit_modified():
            return
        self.text.edit_modified(False)
        self.dirty = True
        self.schedule_save()

    def mark_dirty(self) -> None:
        self.dirty = True
        self.schedule_save()

    def delete_previous_word(self, _event: tk.Event) -> str:
        if self.text.tag_ranges("sel"):
            self.text.edit_separator()
            self.text.delete("sel.first", "sel.last")
            self.text.edit_separator()
            self.mark_dirty()
            return "break"

        before_cursor = self.text.get("1.0", "insert")
        delete_count = previous_word_delete_count(before_cursor)
        if delete_count <= 0:
            return "break"

        self.text.edit_separator()
        self.text.delete(f"insert - {delete_count} chars", "insert")
        self.text.edit_separator()
        self.mark_dirty()
        return "break"

    def schedule_save(self) -> None:
        if self.save_after is not None:
            self.window.after_cancel(self.save_after)
        self.save_after = self.window.after(650, self.autosave)

    def current_content(self) -> str:
        return self.text.get("1.0", "end-1c")

    def autosave(self) -> None:
        self.save_after = None
        if not self.dirty or self.closed:
            return
        try:
            self.app.store.save_content(self.note.id, self.current_content(), closed=False)
            self.dirty = False
        except Exception as exc:
            log_exception(f"Autosave failed for note {self.note.id}", exc)
            self.schedule_save()

    def save_now(self) -> str:
        if self.save_after is not None:
            self.window.after_cancel(self.save_after)
            self.save_after = None
        content = self.current_content()
        try:
            self.app.store.save_content(self.note.id, content, closed=False)
            self.dirty = False
        except Exception as exc:
            log_exception(f"Manual save failed for note {self.note.id}", exc)
            write_recovery_note(self.note.id, content)
        return "break"

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            if self.save_after is not None:
                self.window.after_cancel(self.save_after)
                self.save_after = None
            content = self.current_content()
            if is_empty_note(content):
                self.app.store.delete_note(self.note.id)
            else:
                self.app.store.save_content(self.note.id, content, closed=True)
                self.app.title_worker.enqueue(self.note.id)
        except Exception as exc:
            log_exception(f"Close failed for note {self.note.id}", exc)
            try:
                write_recovery_note(self.note.id, self.current_content())
            except Exception as recovery_exc:
                log_exception(f"Recovery after close failed for note {self.note.id}", recovery_exc)
        finally:
            self.app.note_closed(self.note.id)
            try:
                self.window.destroy()
            except tk.TclError as exc:
                log_exception(f"Destroy failed for note {self.note.id}", exc)


class NoteListWindow:
    def __init__(self, app: "InstantNotesApp") -> None:
        self.app = app
        self.note_ids: list[str] = []
        self.refresh_after: str | None = None

        self.window = tk.Toplevel(app.root)
        app.apply_icon(self.window)
        self.window.title("Notes")
        self.window.geometry("720x500")
        self.window.protocol("WM_DELETE_WINDOW", self.close)

        self.frame = tk.Frame(self.window)
        self.frame.pack(fill="both", expand=True)

        self.scrollbar = tk.Scrollbar(self.frame)
        self.scrollbar.pack(side="right", fill="y")

        self.listbox = tk.Listbox(
            self.frame,
            activestyle="dotbox",
            yscrollcommand=self.scrollbar.set,
        )
        self.listbox.pack(side="left", fill="both", expand=True)
        self.scrollbar.configure(command=self.listbox.yview)

        self.listbox.bind("<Double-Button-1>", lambda _event: self.open_selected())
        self.listbox.bind("<Return>", lambda _event: self.open_selected())
        self.listbox.bind("<Escape>", lambda _event: self.close())

        self.refresh()
        self.window.after(1, self.focus)

    def focus(self) -> None:
        self.window.deiconify()
        self.window.lift()
        self.window.focus_force()
        self.listbox.focus_set()

    def refresh(self) -> None:
        if self.refresh_after is not None:
            try:
                self.window.after_cancel(self.refresh_after)
            except tk.TclError:
                pass
            self.refresh_after = None

        selected_id = self.selected_note_id()
        notes = self.app.store.list_notes()
        self.note_ids = [note.id for note in notes]
        self.listbox.delete(0, "end")
        for note in notes:
            self.listbox.insert("end", note_list_row(note))

        if selected_id in self.note_ids:
            index = self.note_ids.index(selected_id)
            self.listbox.selection_set(index)
            self.listbox.activate(index)
        elif self.note_ids:
            self.listbox.selection_set(0)
            self.listbox.activate(0)

        self.refresh_after = self.window.after(2500, self.refresh)

    def selected_note_id(self) -> str | None:
        selection = self.listbox.curselection()
        if not selection:
            return None
        index = int(selection[0])
        if index >= len(self.note_ids):
            return None
        return self.note_ids[index]

    def open_selected(self) -> str:
        note_id = self.selected_note_id()
        if note_id:
            self.app.open_existing_note(note_id)
        return "break"

    def close(self) -> None:
        if self.refresh_after is not None:
            self.window.after_cancel(self.refresh_after)
            self.refresh_after = None
        self.app.list_window = None
        self.window.destroy()


class HotkeyThread:
    def __init__(self) -> None:
        self.events: queue.Queue[tuple[str, str | None]] = queue.Queue()
        self.commands: queue.Queue[str] = queue.Queue()
        self.thread_id = 0
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self.run, name="InstantNotesHotkeys", daemon=True)
        self.thread.start()
        if not self.ready.wait(timeout=3):
            raise RuntimeError("Hotkey thread did not start.")

    def stop(self) -> None:
        self.commands.put("quit")
        if self.thread_id:
            user32.PostThreadMessageW(self.thread_id, WM_APP, 0, 0)

    def run(self) -> None:
        self.thread_id = kernel32.GetCurrentThreadId()
        registered_ids: list[int] = []
        last_event_at: dict[str, float] = {"new": 0.0, "list": 0.0}
        triggers = load_hotkey_triggers()
        hook_proc_ref: LOW_LEVEL_KEYBOARD_PROC | None = None
        hook_handle = None
        register_errors: list[str] = []

        def emit(kind: str) -> None:
            now = time.monotonic()
            if now - last_event_at.get(kind, 0.0) < 0.28:
                return
            last_event_at[kind] = now
            self.events.put((kind, None))

        def register_hotkey(hotkey_id: int, vk: int, label: str) -> None:
            if user32.RegisterHotKey(None, hotkey_id, MOD_NOREPEAT, vk):
                registered_ids.append(hotkey_id)
                return
            register_errors.append(
                f"Could not register {label}. Windows error: {kernel32.GetLastError()}"
            )

        def hook_proc(n_code: int, w_param: int, l_param: int) -> int:
            try:
                if n_code == 0 and w_param in {WM_KEYDOWN, WM_SYSKEYDOWN}:
                    key = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                    if matches_trigger(key, triggers["new"]):
                        emit("new")
                        return 1
                    if matches_trigger(key, triggers["list"]):
                        emit("list")
                        return 1
            except Exception as exc:
                log_exception("Hotkey hook callback failed", exc)
            return user32.CallNextHookEx(hook_handle, n_code, w_param, l_param)

        register_hotkey(HOTKEY_NEW_ID, VK_F9, "F9")
        register_hotkey(HOTKEY_LIST_ID, VK_F10, "F10")

        hook_proc_ref = LOW_LEVEL_KEYBOARD_PROC(hook_proc)
        hook_handle = user32.SetWindowsHookExW(WH_KEYBOARD_LL, hook_proc_ref, None, 0)
        if not hook_handle and not registered_ids:
            errors = register_errors + [
                f"Could not install F9/F10 keyboard hook. Windows error: {kernel32.GetLastError()}"
            ]
            error_text = "\n".join(errors)
            log_message(error_text)
            self.events.put(("error", error_text))

        self.ready.set()

        message = MSG()
        try:
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                if message.message == WM_HOTKEY:
                    if message.wParam == HOTKEY_NEW_ID:
                        emit("new")
                    elif message.wParam == HOTKEY_LIST_ID:
                        emit("list")
                elif message.message == WM_APP:
                    try:
                        if self.commands.get_nowait() == "quit":
                            break
                    except queue.Empty:
                        pass
        finally:
            if hook_handle:
                user32.UnhookWindowsHookEx(hook_handle)
            hook_proc_ref = None
            for hotkey_id in registered_ids:
                user32.UnregisterHotKey(None, hotkey_id)


class InstantNotesApp:
    def __init__(self) -> None:
        set_dpi_awareness()
        set_windows_app_id()
        self.mutex = kernel32.CreateMutexW(None, False, "Local\\InstantNotesSingleton")
        if self.mutex and kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            raise RuntimeError("Instant Notes is already running.")

        self.root = tk.Tk()
        configure_tk_scaling(self.root)
        self.root.withdraw()
        self.root.title(APP_NAME)
        self.root.protocol("WM_DELETE_WINDOW", self.quit)
        self.root.report_callback_exception = self.report_callback_exception
        self.editor_font = resolve_editor_font(self.root)
        self.icon_image = self.load_icon_image()
        self.apply_icon(self.root, default=True)

        self.store = NoteStore(DB_PATH)
        self.title_worker = TitleWorker(self.store)
        self.sync_worker = SyncWorker(self.store)
        if self.sync_worker.enabled:
            self.store.enqueue_all_notes_for_sync()
        self.hotkeys = HotkeyThread()
        self.note_windows: dict[str, NoteWindow] = {}
        self.list_window: NoteListWindow | None = None
        self.quitting = False

        self.root.after(25, self.poll_hotkeys)
        self.root.after(1000, self.watch_hotkeys)

    def load_icon_image(self) -> tk.PhotoImage | None:
        if not ICON_PATH.exists():
            return None

        try:
            return tk.PhotoImage(file=str(ICON_PATH))
        except tk.TclError as exc:
            log_exception(f"Could not load icon from {ICON_PATH}", exc)
            return None

    def apply_icon(self, window: tk.Tk | tk.Toplevel, default: bool = False) -> None:
        if ICON_ICO_PATH.exists():
            try:
                if default:
                    window.iconbitmap(default=str(ICON_ICO_PATH))
                else:
                    window.iconbitmap(str(ICON_ICO_PATH))
            except tk.TclError as exc:
                log_exception(f"Could not apply ico window icon from {ICON_ICO_PATH}", exc)

        if self.icon_image is not None:
            try:
                window.iconphoto(default, self.icon_image)
            except tk.TclError as exc:
                log_exception("Could not apply png window icon", exc)

    def report_callback_exception(
        self,
        exc_type: type[BaseException],
        exc_value: BaseException,
        exc_traceback: object,
    ) -> None:
        details = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        log_message(f"Tk callback failed\n{details.rstrip()}")

    def poll_hotkeys(self) -> None:
        try:
            while True:
                try:
                    kind, value = self.hotkeys.events.get_nowait()
                except queue.Empty:
                    break

                try:
                    if kind == "new":
                        self.new_note()
                    elif kind == "list":
                        self.show_note_list()
                    elif kind == "error" and value:
                        messagebox.showerror(APP_NAME, value)
                except Exception as exc:
                    log_exception(f"Hotkey event {kind!r} failed", exc)
        finally:
            if not self.quitting:
                self.root.after(25, self.poll_hotkeys)

    def watch_hotkeys(self) -> None:
        if self.quitting:
            return

        if not self.hotkeys.thread.is_alive():
            log_message("Hotkey thread stopped; restarting it.")
            try:
                self.hotkeys = HotkeyThread()
            except Exception as exc:
                log_exception("Could not restart hotkey thread", exc)

        self.root.after(1000, self.watch_hotkeys)

    def new_note(self) -> None:
        note = self.store.create_note()
        self.open_note_record(note)

    def open_existing_note(self, note_id: str) -> None:
        existing = self.note_windows.get(note_id)
        if existing:
            existing.focus()
            return

        note = self.store.get_note(note_id)
        if note:
            self.open_note_record(note)

    def open_note_record(self, note: NoteRecord) -> None:
        window = NoteWindow(self, note)
        self.note_windows[note.id] = window

    def note_closed(self, note_id: str) -> None:
        self.note_windows.pop(note_id, None)

    def show_note_list(self) -> None:
        if self.list_window is not None:
            self.list_window.focus()
            self.list_window.refresh()
            return
        self.list_window = NoteListWindow(self)

    def quit(self) -> None:
        self.quitting = True
        self.hotkeys.stop()
        self.title_worker.stop()
        self.sync_worker.stop()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> int:
    set_dpi_awareness()
    try:
        app = InstantNotesApp()
    except Exception as exc:
        root = tk.Tk()
        configure_tk_scaling(root)
        root.withdraw()
        messagebox.showerror(APP_NAME, str(exc))
        return 1
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
