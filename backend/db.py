"""SQLite persistence for the Regiekamer demo.

One process, one connection. The backend runs with exactly one replica (see
azure-pipelines.yml), so there is no cross-process locking to worry about.
On Azure the file lives on the Azure Files share mounted at /data; SMB does
not support the shared-memory WAL file, so journal_mode stays DELETE.
"""

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.environ.get("DB_PATH", "data/regiekamer.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    mission TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    budget_monthly_cents INTEGER NOT NULL DEFAULT 0,
    require_board_approval_for_new_agents INTEGER NOT NULL DEFAULT 1,
    issue_prefix TEXT NOT NULL DEFAULT 'RK',
    issue_counter INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(id),
    name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'general',
    title TEXT NOT NULL DEFAULT '',
    icon TEXT NOT NULL DEFAULT '🤖',
    template TEXT,
    status TEXT NOT NULL DEFAULT 'idle',
    reports_to TEXT REFERENCES agents(id),
    capabilities TEXT NOT NULL DEFAULT '',
    instructions TEXT NOT NULL DEFAULT '',
    runtime TEXT NOT NULL DEFAULT 'mock',
    model TEXT NOT NULL DEFAULT '',
    runtime_ref TEXT,
    runtime_version TEXT,
    toolsets TEXT,
    heartbeat_interval_sec INTEGER NOT NULL DEFAULT 0,
    budget_monthly_cents INTEGER NOT NULL DEFAULT 0,
    pause_reason TEXT,
    error_reason TEXT,
    last_heartbeat_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS goals (
    id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(id),
    parent_id TEXT REFERENCES goals(id),
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    level TEXT NOT NULL DEFAULT 'company',
    status TEXT NOT NULL DEFAULT 'active',
    owner_agent_id TEXT REFERENCES agents(id),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS issues (
    id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(id),
    identifier TEXT NOT NULL,
    goal_id TEXT REFERENCES goals(id),
    parent_id TEXT REFERENCES issues(id),
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'todo',
    priority TEXT NOT NULL DEFAULT 'medium',
    assignee_agent_id TEXT REFERENCES agents(id),
    checkout_run_id TEXT,
    created_by_agent_id TEXT REFERENCES agents(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS comments (
    id TEXT PRIMARY KEY,
    issue_id TEXT NOT NULL REFERENCES issues(id),
    author_agent_id TEXT REFERENCES agents(id),
    author_user TEXT,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS heartbeat_runs (
    id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL,
    agent_id TEXT NOT NULL REFERENCES agents(id),
    invocation_source TEXT NOT NULL,
    trigger_detail TEXT,
    issue_id TEXT,
    status TEXT NOT NULL DEFAULT 'queued',
    started_at TEXT,
    finished_at TEXT,
    summary TEXT,
    error TEXT,
    usage_json TEXT
);

CREATE TABLE IF NOT EXISTS cost_events (
    id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL,
    agent_id TEXT NOT NULL REFERENCES agents(id),
    issue_id TEXT,
    heartbeat_run_id TEXT,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_cents REAL NOT NULL DEFAULT 0,
    occurred_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL,
    type TEXT NOT NULL,
    requested_by_agent_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    payload_json TEXT NOT NULL DEFAULT '{}',
    decision_note TEXT,
    decided_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_sessions (
    agent_id TEXT NOT NULL,
    issue_key TEXT NOT NULL,
    runtime TEXT NOT NULL,
    session_ref TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (agent_id, issue_key, runtime)
);

CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    message TEXT NOT NULL,
    agent_id TEXT,
    issue_id TEXT,
    run_id TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(id),
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 1,
    issue_id TEXT REFERENCES issues(id),
    author_agent_id TEXT REFERENCES agents(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_issues_assignee ON issues(assignee_agent_id, status);
CREATE INDEX IF NOT EXISTS idx_cost_agent ON cost_events(agent_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_runs_agent ON heartbeat_runs(agent_id, started_at);
CREATE INDEX IF NOT EXISTS idx_documents_company ON documents(company_id, updated_at);
"""

_conn: sqlite3.Connection | None = None


def connect(path: str | None = None) -> sqlite3.Connection:
    """Open (or reopen) the database and make sure the schema exists."""
    global _conn
    path = path or DB_PATH
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    _migrate(conn)
    _conn = conn
    return conn


# Columns added after the first release: (table, column, definition).
_ADDED_COLUMNS = [
    # JSON list of worktools.TOOLSETS keys; NULL = the template's defaults.
    ("agents", "toolsets", "TEXT"),
]


# Runtimes that were merged into another: old name -> current name.
_RENAMED_RUNTIMES = {"chat-pai": "chat", "ollama-pai": "ollama"}


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def _migrate(conn: sqlite3.Connection) -> None:
    for table, column, definition in _ADDED_COLUMNS:
        if column not in _columns(conn, table):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    if "runtime" not in _columns(conn, "agents"):
        return
    for old, new in _RENAMED_RUNTIMES.items():
        conn.execute("UPDATE agents SET runtime = ? WHERE runtime = ?", (new, old))
        conn.execute("UPDATE OR REPLACE agent_sessions SET runtime = ? WHERE runtime = ?", (new, old))


def conn() -> sqlite3.Connection:
    if _conn is None:
        return connect()
    return _conn


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def month_start() -> str:
    n = datetime.now(timezone.utc)
    return n.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")


def rows(sql: str, *params: Any) -> list[dict]:
    return [dict(r) for r in conn().execute(sql, params).fetchall()]


def one(sql: str, *params: Any) -> dict | None:
    r = conn().execute(sql, params).fetchone()
    return dict(r) if r else None


def execute(sql: str, *params: Any) -> int:
    """Run a write and return the number of affected rows."""
    return conn().execute(sql, params).rowcount


def insert(table: str, **fields: Any) -> dict:
    fields.setdefault("id", new_id())
    cols = ", ".join(fields)
    marks = ", ".join("?" for _ in fields)
    conn().execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(fields.values()))
    return fields


def update(table: str, id: str, **fields: Any) -> None:
    if not fields:
        return
    sets = ", ".join(f"{k} = ?" for k in fields)
    conn().execute(f"UPDATE {table} SET {sets} WHERE id = ?", (*fields.values(), id))


def get(table: str, id: str) -> dict | None:
    return one(f"SELECT * FROM {table} WHERE id = ?", id)


def loads(value: str | None) -> Any:
    return json.loads(value) if value else None
