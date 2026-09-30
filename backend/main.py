"""Regiekamer API: the control plane for the agent organisation.

No login: access is limited by the IP allowlist on the frontend's ingress.
"""

import asyncio
import json
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

load_dotenv()

import db  # noqa: E402
import events  # noqa: E402
import org  # noqa: E402
import runtimes  # noqa: E402
import seed  # noqa: E402
import telemetry  # noqa: E402
import templates  # noqa: E402
import worktools  # noqa: E402
from heartbeat import scheduler  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI):
    telemetry.setup()
    db.connect()
    await seed.seed_if_empty()
    scheduler.start()
    yield
    await scheduler.stop()
    await runtimes.close_all()
    telemetry.shutdown()


app = FastAPI(title="Regiekamer", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.environ.get("CORS_ORIGINS", "http://localhost:5173").split(",")],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Every route is `async def` on purpose: the app shares one SQLite connection,
# and keeping all access on the event loop thread (with the heartbeat
# scheduler) means no two statements ever interleave.

# nginx must not buffer the event stream (see nginx.conf).
_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _get_or_404(table: str, id: str) -> dict:
    row = db.get(table, id)
    if not row:
        raise HTTPException(404, f"{table[:-1]} niet gevonden")
    return row


def _bad_request(exc: ValueError) -> HTTPException:
    return HTTPException(400, str(exc))


# ── Schemas ─────────────────────────────────────────────────────────────────


class CompanyPatch(BaseModel):
    name: str | None = None
    mission: str | None = None
    budget_monthly_cents: int | None = None
    require_board_approval_for_new_agents: bool | None = None


class HireRequest(BaseModel):
    template: str | None = None
    name: str | None = None
    title: str | None = None
    icon: str | None = None
    role: str | None = None
    reports_to: str | None = None
    capabilities: str | None = None
    instructions: str | None = None
    runtime: str | None = None
    model: str | None = None
    budget_monthly_cents: int | None = None
    heartbeat_interval_sec: int | None = None
    toolsets: list[str] | None = None


class AgentPatch(BaseModel):
    name: str | None = None
    title: str | None = None
    icon: str | None = None
    reports_to: str | None = None
    capabilities: str | None = None
    instructions: str | None = None
    model: str | None = None
    budget_monthly_cents: int | None = None
    heartbeat_interval_sec: int | None = None
    toolsets: list[str] | None = None


class IssueCreate(BaseModel):
    title: str = Field(min_length=1)
    description: str = ""
    priority: str = "medium"
    status: str = "todo"
    assignee_agent_id: str | None = None
    goal_id: str | None = None
    parent_id: str | None = None


class IssuePatch(BaseModel):
    title: str | None = None
    description: str | None = None
    status: str | None = None
    priority: str | None = None
    assignee_agent_id: str | None = None


class CommentCreate(BaseModel):
    body: str = Field(min_length=1)
    author: str = "Bestuur"


class Decision(BaseModel):
    note: str = ""


# ── Meta ────────────────────────────────────────────────────────────────────


@app.get("/api/health")
async def health():
    return {"status": "ok", "default_runtime": runtimes.default_runtime_name()}


@app.get("/api/templates")
async def list_templates():
    return templates.TEMPLATES


@app.get("/api/toolsets")
async def list_toolsets():
    return worktools.catalog()


@app.get("/api/runtimes")
async def list_runtimes():
    return {"default": runtimes.default_runtime_name(), "runtimes": await runtimes.list_runtimes()}


# ── Companies ───────────────────────────────────────────────────────────────


@app.get("/api/companies")
async def list_companies():
    return [org.company_view(c) for c in db.rows("SELECT * FROM companies ORDER BY created_at")]


@app.get("/api/companies/{cid}")
async def get_company(cid: str):
    return org.company_view(_get_or_404("companies", cid))


@app.patch("/api/companies/{cid}")
async def patch_company(cid: str, body: CompanyPatch):
    _get_or_404("companies", cid)
    changes = body.model_dump(exclude_none=True)
    if "require_board_approval_for_new_agents" in changes:
        changes["require_board_approval_for_new_agents"] = int(changes["require_board_approval_for_new_agents"])
    db.update("companies", cid, **changes)
    events.publish(cid, "company.updated", "Organisatie-instellingen bijgewerkt")
    return org.company_view(db.get("companies", cid))


@app.get("/api/companies/{cid}/org-chart")
async def org_chart(cid: str):
    return org.org_chart(cid)


@app.get("/api/companies/{cid}/goals")
async def goals(cid: str):
    return db.rows("SELECT * FROM goals WHERE company_id = ? ORDER BY created_at", cid)


@app.get("/api/companies/{cid}/activity")
async def activity(cid: str, limit: int = 50):
    return events.recent(cid, limit)


@app.get("/api/companies/{cid}/costs")
async def costs(cid: str):
    by_agent = db.rows(
        """SELECT a.id AS agent_id, a.name, a.icon, a.budget_monthly_cents, a.runtime,
                  COALESCE(SUM(c.cost_cents), 0) AS spent_cents,
                  COALESCE(SUM(c.input_tokens), 0) AS input_tokens,
                  COALESCE(SUM(c.output_tokens), 0) AS output_tokens,
                  COUNT(c.id) AS runs
           FROM agents a LEFT JOIN cost_events c ON c.agent_id = a.id AND c.occurred_at >= ?
           WHERE a.company_id = ? GROUP BY a.id ORDER BY spent_cents DESC""",
        db.month_start(), cid,
    )
    return {"company": org.company_view(_get_or_404("companies", cid)), "by_agent": by_agent}


@app.get("/api/companies/{cid}/events")
async def event_stream(cid: str, request: Request):
    queue = events.subscribe(cid)

    async def stream():
        try:
            yield "retry: 3000\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                except TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            events.unsubscribe(cid, queue)

    return StreamingResponse(stream(), media_type="text/event-stream", headers=_SSE_HEADERS)


# ── Agents ──────────────────────────────────────────────────────────────────


@app.get("/api/companies/{cid}/agents")
async def list_agents(cid: str, include_terminated: bool = False):
    return [org.agent_view(a) for a in org.list_agents(cid, include_terminated)]


@app.post("/api/companies/{cid}/agent-hires", status_code=201)
async def hire(cid: str, body: HireRequest):
    _get_or_404("companies", cid)
    try:
        agent = org.hire_agent(cid, body.model_dump(exclude_none=True))
    except ValueError as exc:
        raise _bad_request(exc) from exc
    if agent["status"] != "pending_approval":
        agent = await org.activate_agent(agent["id"])
    return org.agent_view(db.get("agents", agent["id"]))


@app.get("/api/agents/{aid}")
async def get_agent(aid: str):
    agent = _get_or_404("agents", aid)
    return {**org.agent_view(agent), "chain_of_command": org.chain_of_command(agent),
            "prompt": templates.agent_prompt(agent)}


@app.patch("/api/agents/{aid}")
async def patch_agent(aid: str, body: AgentPatch):
    _get_or_404("agents", aid)
    try:
        agent = await org.update_agent(aid, body.model_dump(exclude_none=True))
    except ValueError as exc:
        raise _bad_request(exc) from exc
    return org.agent_view(agent)


@app.post("/api/agents/{aid}/wake")
async def wake_agent(aid: str):
    agent = _get_or_404("agents", aid)
    if not scheduler.wake(aid, "on_demand", "Handmatig gewekt door het bestuur"):
        raise HTTPException(409, f"{agent['name']} kan nu niet gewekt worden (status: {agent['status']})")
    return {"ok": True}


@app.post("/api/agents/{aid}/pause")
async def pause_agent(aid: str):
    _get_or_404("agents", aid)
    org.set_paused(aid, True)
    return org.agent_view(db.get("agents", aid))


@app.post("/api/agents/{aid}/resume")
async def resume_agent(aid: str):
    _get_or_404("agents", aid)
    org.set_paused(aid, False)
    return org.agent_view(db.get("agents", aid))


@app.post("/api/agents/{aid}/reprovision")
async def reprovision_agent(aid: str):
    _get_or_404("agents", aid)
    return org.agent_view(await org.activate_agent(aid))


@app.post("/api/agents/{aid}/terminate")
async def terminate_agent(aid: str):
    agent = _get_or_404("agents", aid)
    if agent["role"] == "ceo" and not agent["reports_to"]:
        raise HTTPException(400, "De directeur kan niet uit dienst; vervang eerst de top van het organogram")
    await org.terminate_agent(aid)
    return {"ok": True}


@app.get("/api/agents/{aid}/runs")
async def agent_runs(aid: str, limit: int = 30):
    runs = db.rows("SELECT * FROM heartbeat_runs WHERE agent_id = ? ORDER BY started_at DESC LIMIT ?", aid, limit)
    return [{**r, "usage": db.loads(r.pop("usage_json"))} for r in runs]


# ── Issues ──────────────────────────────────────────────────────────────────


@app.get("/api/companies/{cid}/issues")
async def list_issues(cid: str, status: str | None = None, assignee_agent_id: str | None = None):
    sql, params = "SELECT * FROM issues WHERE company_id = ?", [cid]
    if status:
        statuses = status.split(",")
        sql += f" AND status IN ({','.join('?' for _ in statuses)})"
        params += statuses
    if assignee_agent_id:
        sql += " AND assignee_agent_id = ?"
        params.append(assignee_agent_id)
    return [org.issue_view(i) for i in db.rows(sql + " ORDER BY created_at DESC", *params)]


@app.post("/api/companies/{cid}/issues", status_code=201)
async def create_issue(cid: str, body: IssueCreate):
    _get_or_404("companies", cid)
    return org.issue_view(org.create_issue(cid, body.model_dump()))


@app.get("/api/issues/{iid}")
async def get_issue(iid: str):
    issue = _get_or_404("issues", iid)
    comments = db.rows(
        """SELECT c.*, a.name AS author_name, a.icon AS author_icon FROM comments c
           LEFT JOIN agents a ON a.id = c.author_agent_id WHERE c.issue_id = ? ORDER BY c.created_at""",
        iid,
    )
    parent = db.get("issues", issue["parent_id"]) if issue["parent_id"] else None
    documents = db.rows("SELECT id, title, version, updated_at FROM documents WHERE issue_id = ? ORDER BY updated_at DESC",
                        iid)
    runs = db.rows(
        """SELECT r.id, r.status, r.summary, r.started_at, r.invocation_source, a.name AS agent_name
           FROM heartbeat_runs r JOIN agents a ON a.id = r.agent_id WHERE r.issue_id = ? ORDER BY r.started_at DESC""",
        iid,
    )
    return {**org.issue_view(issue), "comments": comments, "runs": runs, "documents": documents,
            "parent": {"id": parent["id"], "identifier": parent["identifier"], "title": parent["title"]} if parent else None}


@app.patch("/api/issues/{iid}")
async def patch_issue(iid: str, body: IssuePatch):
    _get_or_404("issues", iid)
    # Only the assignee may be explicitly cleared (null = unassign).
    changes = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None or k == "assignee_agent_id"}
    return org.issue_view(org.update_issue(iid, changes))


@app.post("/api/issues/{iid}/comments", status_code=201)
async def add_comment(iid: str, body: CommentCreate):
    _get_or_404("issues", iid)
    return org.add_comment(iid, body.body, author_user=body.author)


# ── Documents ───────────────────────────────────────────────────────────────


@app.get("/api/companies/{cid}/documents")
async def list_documents(cid: str, q: str | None = None):
    sql, params = "SELECT * FROM documents WHERE company_id = ?", [cid]
    if q:
        sql += " AND (title LIKE ? OR body LIKE ?)"
        params += [f"%{q}%", f"%{q}%"]
    return [{**worktools.document_view(d), "body": d["body"][:300]}
            for d in db.rows(sql + " ORDER BY updated_at DESC", *params)]


@app.get("/api/documents/{did}")
async def get_document(did: str):
    return worktools.document_view(_get_or_404("documents", did))


# ── Approvals (the board) ───────────────────────────────────────────────────


@app.get("/api/companies/{cid}/approvals")
async def list_approvals(cid: str, status: str | None = "pending"):
    sql, params = "SELECT * FROM approvals WHERE company_id = ?", [cid]
    if status and status != "all":
        sql += " AND status = ?"
        params.append(status)
    return [org.approval_view(a) for a in db.rows(sql + " ORDER BY created_at DESC", *params)]


@app.post("/api/approvals/{apid}/approve")
async def approve(apid: str, body: Decision | None = None):
    _get_or_404("approvals", apid)
    try:
        return org.approval_view(await org.decide_approval(apid, True, body.note if body else ""))
    except ValueError as exc:
        raise _bad_request(exc) from exc


@app.post("/api/approvals/{apid}/reject")
async def reject(apid: str, body: Decision | None = None):
    _get_or_404("approvals", apid)
    try:
        return org.approval_view(await org.decide_approval(apid, False, body.note if body else ""))
    except ValueError as exc:
        raise _bad_request(exc) from exc


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
