"""Heartbeat scheduler: wakes agents, runs them, books the cost.

Agents don't run continuously: they wake up, do some work and go back to
sleep. A wake has a source: timer, assignment, mention, on_demand,
approval or automation (e.g. a subtask finished).

Guarantees:
  * at most one run per agent at a time;
  * wakes arriving while an agent is queued or running are coalesced into
    one follow-up run (the context names every triggering issue);
  * a run holds its issue checkouts only for its own lifetime;
  * budget is checked before every run: at 100% the agent is auto-paused and
    the board gets a budget_override_required approval; at 80% a warning.
"""

import asyncio
import contextlib
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

import db
import events
import org
import pricing
from runtimes import get_runtime
from tools import Toolbox

log = logging.getLogger("regiekamer.heartbeat")

MAX_CONCURRENT_RUNS = int(os.environ.get("MAX_CONCURRENT_RUNS", "3"))
RUN_TIMEOUT_SEC = float(os.environ.get("RUN_TIMEOUT_SEC", "300"))
TIMER_TICK_SEC = float(os.environ.get("HEARTBEAT_TICK_SEC", "10"))


@dataclass
class WakeRequest:
    agent_id: str
    source: str
    details: list[str] = field(default_factory=list)
    issue_ids: list[str] = field(default_factory=list)

    def merge(self, source: str, detail: str, issue_id: str | None) -> None:
        if detail:
            self.details.append(detail)
        if issue_id and issue_id not in self.issue_ids:
            self.issue_ids.append(issue_id)
        # An explicit trigger beats the timer as the reason we record.
        if self.source == "timer":
            self.source = source


class Scheduler:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._pending: dict[str, WakeRequest] = {}
        self._running: set[str] = set()
        self._tasks: list[asyncio.Task] = []

    # ── public ──

    def wake(self, agent_id: str, source: str, detail: str = "", issue_id: str | None = None) -> bool:
        agent = db.get("agents", agent_id)
        if not agent or agent["status"] not in org.WAKEABLE:
            return False
        if agent_id in self._pending:
            self._pending[agent_id].merge(source, detail, issue_id)
            return True
        req = WakeRequest(agent_id, source)
        req.merge(source, detail, issue_id)
        self._pending[agent_id] = req
        if agent_id not in self._running:
            self._queue.put_nowait(agent_id)
        return True

    def start(self) -> None:
        org.set_waker(self.wake)
        self._recover()
        self._tasks = [asyncio.create_task(self._worker(i)) for i in range(MAX_CONCURRENT_RUNS)]
        self._tasks.append(asyncio.create_task(self._timer()))

    async def stop(self) -> None:
        org.set_waker(None)
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await t
        self._tasks = []

    async def drain(self, timeout: float = 30) -> None:
        """Wait until nothing is queued or running (used by tests)."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while self._pending or self._running:
            if loop.time() > deadline:
                raise TimeoutError("Scheduler did not drain")
            await asyncio.sleep(0.02)

    # ── internals ──

    def _recover(self) -> None:
        """A restart mid-run leaves stale state; clean it up."""
        db.execute("UPDATE heartbeat_runs SET status = 'interrupted', finished_at = ? WHERE status IN ('queued','running')", db.now())
        db.execute("UPDATE agents SET status = 'idle' WHERE status = 'running'")
        db.execute("UPDATE issues SET checkout_run_id = NULL WHERE checkout_run_id IS NOT NULL")

    async def _worker(self, n: int) -> None:
        while True:
            agent_id = await self._queue.get()
            if agent_id in self._running:  # requeued by the run's own finally
                continue
            req = self._pending.pop(agent_id, None)
            if req is None:
                continue
            self._running.add(agent_id)
            try:
                await self.execute(req)
            except Exception:  # never kill the worker
                log.exception("heartbeat for %s crashed", agent_id)
            finally:
                self._running.discard(agent_id)
                if agent_id in self._pending:  # coalesced wake arrived during the run
                    self._queue.put_nowait(agent_id)

    async def _timer(self) -> None:
        while True:
            await asyncio.sleep(TIMER_TICK_SEC)
            try:
                self._tick()
            except Exception:
                log.exception("timer tick failed")

    def _tick(self) -> None:
        now = datetime.now(timezone.utc)
        for a in db.rows("SELECT * FROM agents WHERE heartbeat_interval_sec > 0 AND status IN ('idle','error')"):
            last = datetime.fromisoformat(a["last_heartbeat_at"]) if a["last_heartbeat_at"] else None
            if last and (now - last).total_seconds() < a["heartbeat_interval_sec"]:
                continue
            # Skip timer wakes when there is nothing to do: an empty inbox costs nothing.
            has_work = db.one(
                "SELECT 1 FROM issues WHERE assignee_agent_id = ? AND status IN ('todo','in_progress') LIMIT 1", a["id"]
            )
            if has_work:
                self.wake(a["id"], "timer", "Periodieke heartbeat")
            else:
                db.update("agents", a["id"], last_heartbeat_at=db.now())

    # ── one run ──

    async def execute(self, req: WakeRequest) -> dict:
        agent = db.get("agents", req.agent_id)
        if agent["status"] not in org.WAKEABLE:  # paused or terminated while queued
            return {}
        company_id = agent["company_id"]
        focus_id = req.issue_ids[0] if req.issue_ids else None
        run = db.insert(
            "heartbeat_runs",
            company_id=company_id,
            agent_id=agent["id"],
            invocation_source=req.source,
            trigger_detail="; ".join(req.details)[:1000],
            issue_id=focus_id,
            status="running",
            started_at=db.now(),
        )

        if not self._budget_ok(agent, run["id"]):
            return db.get("heartbeat_runs", run["id"])

        db.update("agents", agent["id"], status="running", last_heartbeat_at=db.now())
        events.publish(company_id, "run.started", f"{agent['name']} is wakker ({req.source})",
                       agent_id=agent["id"], issue_id=focus_id, run_id=run["id"])

        runtime = get_runtime(agent["runtime"])
        toolbox = Toolbox(agent, run["id"])
        ctx = build_context(agent, req)
        issue_key = focus_id or ""
        session = db.one("SELECT session_ref FROM agent_sessions WHERE agent_id = ? AND issue_key = ? AND runtime = ?",
                         agent["id"], issue_key, agent["runtime"])
        try:
            result = await asyncio.wait_for(
                runtime.run(agent, ctx, toolbox, session["session_ref"] if session else None),
                getattr(runtime, "run_timeout_sec", RUN_TIMEOUT_SEC),
            )
        except Exception as exc:
            status = "timed_out" if isinstance(exc, TimeoutError) else "failed"
            message = f"{type(exc).__name__}: {exc}"[:1000]
            db.update("heartbeat_runs", run["id"], status=status, error=message, finished_at=db.now(),
                      usage_json=json.dumps({"tool_calls": toolbox.calls}))
            self._finish(agent, run["id"], status="error", error=message)
            events.publish(company_id, "run.failed", f"Heartbeat van {agent['name']} mislukt: {message[:160]}",
                           agent_id=agent["id"], issue_id=focus_id, run_id=run["id"])
            return db.get("heartbeat_runs", run["id"])

        if result.session_ref:
            db.execute(
                "INSERT INTO agent_sessions (agent_id, issue_key, runtime, session_ref, updated_at) VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(agent_id, issue_key, runtime) DO UPDATE SET session_ref = excluded.session_ref,"
                " updated_at = excluded.updated_at",
                agent["id"], issue_key, agent["runtime"], result.session_ref, db.now(),
            )
        cost = pricing.cost_cents(result.model or agent["model"], result.input_tokens, result.output_tokens,
                                  result.provider)
        usage = {"input_tokens": result.input_tokens, "output_tokens": result.output_tokens,
                 "model": result.model, "provider": result.provider, "cost_cents": cost,
                 "tool_calls": toolbox.calls, **result.extra}
        if result.input_tokens or result.output_tokens:
            db.insert("cost_events", company_id=company_id, agent_id=agent["id"], issue_id=focus_id,
                      heartbeat_run_id=run["id"], provider=result.provider or agent["runtime"],
                      model=result.model or agent["model"], input_tokens=result.input_tokens,
                      output_tokens=result.output_tokens, cost_cents=cost, occurred_at=db.now())
        db.update("heartbeat_runs", run["id"], status="succeeded", summary=result.summary,
                  finished_at=db.now(), usage_json=json.dumps(usage, ensure_ascii=False))
        self._finish(agent, run["id"], status="idle")
        events.publish(company_id, "run.succeeded", f"{agent['name']}: {result.summary[:200]}",
                       agent_id=agent["id"], issue_id=focus_id, run_id=run["id"])
        self._budget_warn(agent)
        return db.get("heartbeat_runs", run["id"])

    def _finish(self, agent: dict, run_id: str, *, status: str, error: str | None = None) -> None:
        db.execute("UPDATE issues SET checkout_run_id = NULL WHERE checkout_run_id = ?", run_id)
        current = db.get("agents", agent["id"])
        # Don't overwrite a pause/termination that happened during the run.
        if current["status"] == "running":
            db.update("agents", agent["id"], status=status, error_reason=error)

    def _budget_ok(self, agent: dict, run_id: str) -> bool:
        budget = agent["budget_monthly_cents"]
        spent = org.spent_cents(agent_id=agent["id"])
        if budget <= 0 or spent < budget:
            return True
        db.update("heartbeat_runs", run_id, status="cancelled", finished_at=db.now(),
                  error="Maandbudget op; agent automatisch gepauzeerd")
        org.set_paused(agent["id"], True, f"Maandbudget van €{budget / 100:.2f} bereikt")
        already = db.one("SELECT 1 FROM approvals WHERE type = 'budget_override_required' AND status = 'pending'"
                         " AND json_extract(payload_json, '$.agent_id') = ?", agent["id"])
        if not already:
            org.create_approval(
                agent["company_id"], "budget_override_required",
                {"agent_id": agent["id"], "spent_cents": spent, "budget_cents": budget,
                 "proposed_budget_cents": budget * 2},
                agent["id"], f"{agent['name']} heeft het maandbudget bereikt en is gepauzeerd",
            )
        return False

    def _budget_warn(self, agent: dict) -> None:
        budget = agent["budget_monthly_cents"]
        if budget <= 0:
            return
        spent = org.spent_cents(agent_id=agent["id"])
        if spent >= 0.8 * budget and spent - _last_cost(agent["id"]) < 0.8 * budget:
            events.publish(agent["company_id"], "budget.warning",
                           f"{agent['name']} zit op {spent / budget:.0%} van het maandbudget", agent_id=agent["id"])


def _last_cost(agent_id: str) -> float:
    r = db.one("SELECT cost_cents FROM cost_events WHERE agent_id = ? ORDER BY occurred_at DESC, rowid DESC LIMIT 1", agent_id)
    return r["cost_cents"] if r else 0.0


def build_context(agent: dict, req: WakeRequest) -> dict:
    """The wake payload: heartbeat context plus a ready-made task prompt."""
    company = db.get("companies", agent["company_id"])
    manager = db.get("agents", agent["reports_to"]) if agent["reports_to"] else None
    reports = [{"name": r["name"], "title": r["title"], "capabilities": r["capabilities"]}
               for r in org.direct_reports(agent["id"]) if r["status"] not in ("pending_approval",)]
    inbox = db.rows(
        f"SELECT identifier, title, status, priority FROM issues WHERE assignee_agent_id = ? AND status IN {org.OPEN_ISSUE_STATUSES}"
        " ORDER BY created_at", agent["id"],
    )
    focus = db.get("issues", req.issue_ids[0]) if req.issue_ids else None
    goal = db.get("goals", focus["goal_id"]) if focus and focus["goal_id"] else db.one(
        "SELECT * FROM goals WHERE company_id = ? AND level = 'company' ORDER BY created_at LIMIT 1", agent["company_id"])

    lines = [
        f"# Heartbeat voor {agent['name']} ({agent['title'] or agent['role']})",
        f"Organisatie: {company['name']}. Missie: {company['mission']}",
    ]
    if goal:
        lines.append(f"Doel: {goal['title']}")
    lines.append(f"Je rapporteert aan: {manager['name']} ({manager['title']})" if manager else "Je rapporteert aan het bestuur.")
    if reports:
        lines.append("Jouw team: " + "; ".join(f"{r['name']} ({r['title']}: {r['capabilities']})" for r in reports))
    else:
        lines.append("Je hebt (nog) geen team; doe het werk zelf of vraag een collega aan met request_hire.")
    lines.append(f"\nWake-reden: {req.source}" + (f" — {'; '.join(req.details)}" if req.details else ""))
    if focus:
        lines += [f"\n## Focus: {focus['identifier']} — {focus['title']}", focus["description"] or "(geen omschrijving)"]
    lines.append("\n## Inbox")
    lines += [f"- {i['identifier']} [{i['status']}, {i['priority']}] {i['title']}" for i in inbox] or ["(leeg)"]
    lines.append("\nWerk volgens het heartbeat-protocol en sluit af met een samenvatting van één zin.")

    return {
        "agent_id": agent["id"],
        "wake_reason": req.source,
        "wake_details": req.details,
        "focus_issue": {"id": focus["id"], "identifier": focus["identifier"], "title": focus["title"]} if focus else None,
        "direct_reports": reports,
        "inbox": inbox,
        "task_markdown": "\n".join(lines),
    }


scheduler = Scheduler()
