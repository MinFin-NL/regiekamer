"""Activity log + live fan-out.

Every notable thing (hire, wake, comment, status change, cost) is written to
the append-only `activity` table and pushed to the SSE subscribers of that
company, so the UI can refresh the affected view without polling.
"""

import asyncio
import contextlib

import db

_subscribers: dict[str, set[asyncio.Queue]] = {}


def publish(
    company_id: str,
    kind: str,
    message: str,
    *,
    agent_id: str | None = None,
    issue_id: str | None = None,
    run_id: str | None = None,
) -> dict:
    created_at = db.now()
    cur = db.conn().execute(
        "INSERT INTO activity (company_id, kind, message, agent_id, issue_id, run_id, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (company_id, kind, message, agent_id, issue_id, run_id, created_at),
    )
    event = {
        "id": cur.lastrowid,
        "company_id": company_id,
        "kind": kind,
        "message": message,
        "agent_id": agent_id,
        "issue_id": issue_id,
        "run_id": run_id,
        "created_at": created_at,
    }
    for queue in _subscribers.get(company_id, set()):
        with contextlib.suppress(asyncio.QueueFull):
            queue.put_nowait(event)
    return event


def subscribe(company_id: str) -> asyncio.Queue:
    queue: asyncio.Queue = asyncio.Queue(maxsize=500)
    _subscribers.setdefault(company_id, set()).add(queue)
    return queue


def unsubscribe(company_id: str, queue: asyncio.Queue) -> None:
    _subscribers.get(company_id, set()).discard(queue)


def recent(company_id: str, limit: int = 50) -> list[dict]:
    return db.rows(
        "SELECT * FROM activity WHERE company_id = ? ORDER BY id DESC LIMIT ?", company_id, limit
    )
