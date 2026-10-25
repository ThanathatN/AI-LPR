"""SQLite storage layer."""
import copy
import json
import sqlite3
import threading
from datetime import datetime

from .config import DB_PATH, DEFAULT_SETTINGS

_lock = threading.RLock()
_conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
_conn.row_factory = sqlite3.Row
_conn.execute("PRAGMA journal_mode=WAL")
_conn.execute("PRAGMA foreign_keys=ON")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    salt TEXT NOT NULL,
    full_name TEXT DEFAULT '',
    role TEXT NOT NULL DEFAULT 'operator',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT, last_login TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT, expires_at TEXT
);
CREATE TABLE IF NOT EXISTS groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'allow',
    color TEXT DEFAULT '#3b82f6',
    auto_open INTEGER NOT NULL DEFAULT 1,
    outputs TEXT DEFAULT '[]',
    schedule TEXT DEFAULT '{"days":[0,1,2,3,4,5,6],"start":"00:00","end":"23:59"}',
    description TEXT DEFAULT '',
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS vehicles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plate TEXT NOT NULL,
    plate_key TEXT NOT NULL,
    province TEXT DEFAULT '',
    owner TEXT DEFAULT '',
    phone TEXT DEFAULT '',
    department TEXT DEFAULT '',
    vehicle_type TEXT DEFAULT 'car',
    brand TEXT DEFAULT '',
    color TEXT DEFAULT '',
    group_id INTEGER REFERENCES groups(id) ON DELETE SET NULL,
    valid_from TEXT DEFAULT '',
    valid_to TEXT DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    note TEXT DEFAULT '',
    created_at TEXT, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_vehicles_key ON vehicles(plate_key);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    plate TEXT, province TEXT, raw_text TEXT,
    confidence REAL,
    vehicle_id INTEGER, owner TEXT, group_name TEXT, group_color TEXT, list_type TEXT,
    decision TEXT, reason TEXT, gate_action TEXT,
    source TEXT, snapshot TEXT, plate_img TEXT,
    latency_ms INTEGER, camera TEXT
);
CREATE INDEX IF NOT EXISTS ix_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS ix_events_plate ON events(plate);
CREATE TABLE IF NOT EXISTS io_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL, port_type TEXT, port_id INTEGER, state TEXT, source TEXT, detail TEXT
);
CREATE INDEX IF NOT EXISTS ix_io_ts ON io_events(ts);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL, username TEXT, action TEXT, detail TEXT
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS cameras (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    host TEXT DEFAULT '', http_port INTEGER DEFAULT 80, rtsp_port INTEGER DEFAULT 554,
    username TEXT DEFAULT 'admin', password TEXT DEFAULT '',
    stream TEXT DEFAULT '101', source_override TEXT DEFAULT '',
    lane TEXT DEFAULT 'entry',
    sort INTEGER DEFAULT 0,
    config TEXT DEFAULT '{}',
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS ai_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT, image TEXT NOT NULL,
    label TEXT NOT NULL, province TEXT DEFAULT '',
    read_key TEXT DEFAULT '', status TEXT DEFAULT 'verified', source TEXT DEFAULT '',
    event_id INTEGER, camera_id INTEGER
);
CREATE INDEX IF NOT EXISTS ix_samples_event ON ai_samples(event_id);
CREATE TABLE IF NOT EXISTS ai_models (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT, name TEXT, onnx TEXT, weights TEXT,
    samples INTEGER, val_samples INTEGER, base_acc REAL, acc REAL, epochs INTEGER, note TEXT DEFAULT ''
);
"""

# columns added after the first release (ALTER TABLE is a no-op when they exist)
MIGRATIONS = [
    ("events", "camera_id", "INTEGER"),
    ("events", "direction", "TEXT DEFAULT ''"),
    ("io_events", "camera_id", "INTEGER"),
    ("groups", "cameras", "TEXT DEFAULT '[]'"),
    ("cameras", "capture", "TEXT DEFAULT 'sdk'"),      # sdk = HCNetSDK (port 8000) | rtsp = OpenCV/FFmpeg
    ("cameras", "sdk_port", "INTEGER DEFAULT 8000"),
    ("cameras", "channel", "INTEGER DEFAULT 1"),
]

with _lock:
    _conn.executescript(SCHEMA)
    for table, col, typ in MIGRATIONS:
        if col not in {r[1] for r in _conn.execute(f"PRAGMA table_info({table})")}:
            _conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
    _conn.commit()


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def query(sql: str, args=()) -> list:
    with _lock:
        cur = _conn.execute(sql, args)
        return [dict(r) for r in cur.fetchall()]


def one(sql: str, args=()):
    rows = query(sql, args)
    return rows[0] if rows else None


def execute(sql: str, args=()) -> int:
    with _lock:
        cur = _conn.execute(sql, args)
        _conn.commit()
        return cur.lastrowid


def insert(table: str, data: dict) -> int:
    cols = ",".join(data.keys())
    marks = ",".join("?" for _ in data)
    return execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(data.values()))


def update(table: str, row_id: int, data: dict):
    if not data:
        return
    sets = ",".join(f"{k}=?" for k in data)
    execute(f"UPDATE {table} SET {sets} WHERE id=?", (*data.values(), row_id))


# ---------------------------------------------------------------- settings
def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


_settings_cache = None


def get_settings() -> dict:
    global _settings_cache
    if _settings_cache is None:
        stored = {r["key"]: json.loads(r["value"]) for r in query("SELECT key, value FROM settings")}
        _settings_cache = _merge(DEFAULT_SETTINGS, stored)
    return copy.deepcopy(_settings_cache)


def save_settings(section: str, value: dict):
    global _settings_cache
    current = get_settings().get(section, {})
    merged = _merge(current, value) if isinstance(value, dict) else value
    execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (section, json.dumps(merged, ensure_ascii=False)))
    _settings_cache = None


def audit(username: str, action: str, detail: str = ""):
    insert("audit", {"ts": now_iso(), "username": username, "action": action, "detail": detail})


def log_io(port_type: str, port_id: int, state: str, source: str, detail: str = "", camera_id=None):
    insert("io_events", {"ts": now_iso(), "port_type": port_type, "port_id": port_id,
                         "state": state, "source": source, "detail": detail, "camera_id": camera_id})
