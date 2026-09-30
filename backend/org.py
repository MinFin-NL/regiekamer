"""Company, org chart, hiring, issues and approvals: the domain operations.

The HTTP routes (main.py) and the agent tools (tools.py) both go through here,
so a board member clicking "assign" and an agent calling create_subtask hit
the same rules and the same wake-ups.
"""

import json
from collections.abc import Callable

import db
import events
import templates
import worktools
from runtimes import get_runtime, default_runtime_name, default_model_for

WAKEABLE = {"idle", "running", "error"}
OPEN_ISSUE_STATUSES = ("backlog", "todo", "in_progress", "in_review", "blocked")

# Registered by the heartbeat scheduler at startup (tests register a stub).
_waker: Callable[..., None] | None = None


def set_waker(fn: Callable[..., None] | None) -> None:
    global _waker
    _waker = fn


def wake(agent_id: str | None, source: str, detail: str = "", issue_id: str | None = None) -> None:
    if agent_id and _waker:
        _waker(agent_id, source, detail, issue_id)


# ── Companies and spend ─────────────────────────────────────────────────────


def spent_cents(*, agent_id: str | None = None, company_id: str | None = None) -> float:
    """Spend this calendar month, derived from cost events (no counters to drift)."""
    if agent_id:
        r = db.one(
            "SELECT COALESCE(SUM(cost_cents), 0) AS s FROM cost_events WHERE agent_id = ? AND occurred_at >= ?",
            agent_id, db.month_start(),
        )
    else:
        r = db.one(
            "SELECT COALESCE(SUM(cost_cents), 0) AS s FROM cost_events WHERE company_id = ? AND occurred_at >= ?",
            company_id, db.month_start(),
        )
    return round(r["s"], 4) if r else 0.0


def company_view(company: dict) -> dict:
    return {
        **company,
        "require_board_approval_for_new_agents": bool(company["require_board_approval_for_new_agents"]),
        "spent_monthly_cents": spent_cents(company_id=company["id"]),
    }


# ── Agents ──────────────────────────────────────────────────────────────────


def agent_view(agent: dict) -> dict:
    open_issues = db.one(
        f"SELECT COUNT(*) AS n FROM issues WHERE assignee_agent_id = ? AND status IN {OPEN_ISSUE_STATUSES}",
        agent["id"],
    )["n"]
    return {**agent, "toolsets": templates.agent_toolsets(agent), "spent_monthly_cents": spent_cents(agent_id=agent["id"]),
            "open_issues": open_issues}


def list_agents(company_id: str, include_terminated: bool = False) -> list[dict]:
    sql = "SELECT * FROM agents WHERE company_id = ?"
    if not include_terminated:
        sql += " AND status != 'terminated'"
    return db.rows(sql + " ORDER BY created_at", company_id)


def direct_reports(agent_id: str) -> list[dict]:
    return db.rows(
        "SELECT * FROM agents WHERE reports_to = ? AND status != 'terminated' ORDER BY created_at", agent_id
    )


def subtree_ids(agent_id: str) -> set[str]:
    """The agent itself plus everyone below it in the org chart."""
    seen = {agent_id}
    frontier = [agent_id]
    while frontier:
        nxt = []
        for aid in frontier:
            for r in direct_reports(aid):
                if r["id"] not in seen:
                    seen.add(r["id"])
                    nxt.append(r["id"])
        frontier = nxt
    return seen


def chain_of_command(agent: dict) -> list[dict]:
    chain, cur, guard = [], agent, 0
    while cur and cur.get("reports_to") and guard < 20:
        cur = db.get("agents", cur["reports_to"])
        if cur:
            chain.append({"id": cur["id"], "name": cur["name"], "title": cur["title"]})
        guard += 1
    return chain


def org_chart(company_id: str) -> list[dict]:
    agents = [agent_view(a) for a in list_agents(company_id)]
    by_parent: dict[str | None, list[dict]] = {}
    ids = {a["id"] for a in agents}
    for a in agents:
        parent = a["reports_to"] if a["reports_to"] in ids else None
        by_parent.setdefault(parent, []).append(a)

    def build(parent: str | None) -> list[dict]:
        return [{**a, "reports": build(a["id"])} for a in by_parent.get(parent, [])]

    return build(None)


def hire_agent(company_id: str, data: dict, *, requested_by_agent_id: str | None = None) -> dict:
    """Create an agent from a template + overrides.

    Board hires skip approval unless the company requires it; hires requested
    by an agent always go to the board.
    """
    company = db.get("companies", company_id)
    if not company:
        raise ValueError("Onbekende organisatie")
    tpl = templates.get_template(data.get("template")) or {}

    def pick(key: str, default=""):
        value = data.get(key)
        return value if value not in (None, "") else tpl.get(key, default)

    runtime = data.get("runtime") or default_runtime_name()
    get_runtime(runtime)  # raises on unknown runtime
    reports_to = data.get("reports_to") or None
    if reports_to and not db.get("agents", reports_to):
        raise ValueError("Leidinggevende bestaat niet")
    if not reports_to:
        ceo = db.one("SELECT id FROM agents WHERE company_id = ? AND role = 'ceo' AND status != 'terminated'", company_id)
        reports_to = ceo["id"] if ceo and pick("role", "general") != "ceo" else None

    name = (data.get("name") or "").strip() or tpl.get("title") or "Nieuwe medewerker"
    # An explicit empty list means "no work tools"; only a missing value falls back to the template.
    toolsets = worktools.validate(data["toolsets"] if data.get("toolsets") is not None else tpl.get("toolsets", []))
    needs_approval = bool(requested_by_agent_id) or bool(company["require_board_approval_for_new_agents"])
    agent = db.insert(
        "agents",
        company_id=company_id,
        name=name,
        role=pick("role", "general"),
        title=pick("title", ""),
        icon=pick("icon", "🤖"),
        template=tpl.get("key"),
        status="pending_approval" if needs_approval else "idle",
        reports_to=reports_to,
        capabilities=pick("capabilities", ""),
        instructions=pick("instructions", ""),
        toolsets=json.dumps(toolsets),
        runtime=runtime,
        model=data.get("model") or default_model_for(runtime),
        heartbeat_interval_sec=int(data.get("heartbeat_interval_sec") or 0),
        budget_monthly_cents=int(pick("budget_monthly_cents", 1000)),
        created_at=db.now(),
    )

    if needs_approval:
        approval = db.insert(
            "approvals",
            company_id=company_id,
            type="hire_agent",
            requested_by_agent_id=requested_by_agent_id,
            payload_json=json.dumps({"agent_id": agent["id"], "reason": data.get("reason", "")}),
            created_at=db.now(),
        )
        who = db.get("agents", requested_by_agent_id)["name"] if requested_by_agent_id else "Het bestuur"
        events.publish(
            company_id, "approval.created",
            f"{who} wil {name} ({agent['title']}) aannemen, wacht op goedkeuring",
            agent_id=agent["id"],
        )
        agent["approval_id"] = approval["id"]
    else:
        events.publish(company_id, "agent.hired", f"{name} ({agent['title']}) is aangenomen", agent_id=agent["id"])
    return agent


async def activate_agent(agent_id: str) -> dict:
    """Provision the agent on its runtime (for Foundry: create the agent version)."""
    agent = db.get("agents", agent_id)
    try:
        fields = await get_runtime(agent["runtime"]).provision(agent)
        db.update("agents", agent_id, status="idle", error_reason=None, **fields)
        events.publish(agent["company_id"], "agent.provisioned",
                       f"{agent['name']} is klaar voor werk ({agent['runtime']})", agent_id=agent_id)
    except Exception as exc:  # surfaced on the agent card, retry via "Opnieuw inrichten"
        db.update("agents", agent_id, status="error", error_reason=f"Inrichten mislukt: {exc}")
        events.publish(agent["company_id"], "agent.error", f"Inrichten van {agent['name']} mislukt: {exc}",
                       agent_id=agent_id)
    return db.get("agents", agent_id)


async def update_agent(agent_id: str, changes: dict) -> dict:
    allowed = {"name", "title", "icon", "reports_to", "capabilities", "instructions", "model",
               "heartbeat_interval_sec", "budget_monthly_cents", "toolsets"}
    changes = {k: v for k, v in changes.items() if k in allowed}
    if "toolsets" in changes:
        changes["toolsets"] = json.dumps(worktools.validate(changes["toolsets"]))
    agent = db.get("agents", agent_id)
    if "reports_to" in changes and changes["reports_to"] in subtree_ids(agent_id):
        raise ValueError("Een medewerker kan niet rapporteren aan zichzelf of een eigen ondergeschikte")
    db.update("agents", agent_id, **changes)
    agent = db.get("agents", agent_id)
    # Instructions, model and tools are baked into the Foundry agent version: publish a new version.
    if {"instructions", "model", "name", "toolsets"} & changes.keys() and agent["status"] not in ("pending_approval", "terminated"):
        fields = await get_runtime(agent["runtime"]).update(agent)
        db.update("agents", agent_id, **fields)
    events.publish(agent["company_id"], "agent.updated", f"{agent['name']} is bijgewerkt", agent_id=agent_id)
    return db.get("agents", agent_id)


async def terminate_agent(agent_id: str) -> None:
    agent = db.get("agents", agent_id)
    try:
        await get_runtime(agent["runtime"]).deprovision(agent)
    except Exception as exc:
        events.publish(agent["company_id"], "agent.error", f"Opruimen runtime van {agent['name']} mislukt: {exc}",
                       agent_id=agent_id)
    db.update("agents", agent_id, status="terminated")
    # Reports move up one level; open work goes back to the manager.
    db.execute("UPDATE agents SET reports_to = ? WHERE reports_to = ?", agent["reports_to"], agent_id)
    db.execute(
        f"UPDATE issues SET assignee_agent_id = ?, checkout_run_id = NULL WHERE assignee_agent_id = ? AND status IN {OPEN_ISSUE_STATUSES}",
        agent["reports_to"], agent_id,
    )
    events.publish(agent["company_id"], "agent.terminated", f"{agent['name']} is uit dienst", agent_id=agent_id)
    wake(agent["reports_to"], "automation", f"{agent['name']} is uit dienst; open werk is teruggelegd")


def set_paused(agent_id: str, paused: bool, reason: str = "") -> None:
    agent = db.get("agents", agent_id)
    if paused:
        db.update("agents", agent_id, status="paused", pause_reason=reason or "Gepauzeerd door het bestuur")
        events.publish(agent["company_id"], "agent.paused", f"{agent['name']} is gepauzeerd: {reason or 'door het bestuur'}",
                       agent_id=agent_id)
    else:
        db.update("agents", agent_id, status="idle", pause_reason=None)
        events.publish(agent["company_id"], "agent.resumed", f"{agent['name']} is hervat", agent_id=agent_id)


# ── Issues ──────────────────────────────────────────────────────────────────


def issue_view(issue: dict) -> dict:
    assignee = db.get("agents", issue["assignee_agent_id"]) if issue["assignee_agent_id"] else None
    children = db.rows("SELECT id, identifier, title, status, assignee_agent_id FROM issues WHERE parent_id = ?", issue["id"])
    return {
        **issue,
        "assignee": {"id": assignee["id"], "name": assignee["name"], "icon": assignee["icon"]} if assignee else None,
        "children": children,
    }


def create_issue(company_id: str, data: dict, *, created_by_agent_id: str | None = None) -> dict:
    company = db.get("companies", company_id)
    counter = company["issue_counter"] + 1
    db.update("companies", company_id, issue_counter=counter)
    ts = db.now()
    issue = db.insert(
        "issues",
        company_id=company_id,
        identifier=f"{company['issue_prefix']}-{counter}",
        goal_id=data.get("goal_id"),
        parent_id=data.get("parent_id"),
        title=data["title"],
        description=data.get("description", ""),
        status=data.get("status") or "todo",
        priority=data.get("priority") or "medium",
        assignee_agent_id=data.get("assignee_agent_id"),
        created_by_agent_id=created_by_agent_id,
        created_at=ts,
        updated_at=ts,
    )
    by = db.get("agents", created_by_agent_id)["name"] if created_by_agent_id else "Het bestuur"
    events.publish(company_id, "issue.created", f"{by} maakte {issue['identifier']}: {issue['title']}",
                   agent_id=created_by_agent_id, issue_id=issue["id"])
    if issue["assignee_agent_id"]:
        wake(issue["assignee_agent_id"], "assignment", f"{issue['identifier']} is aan je toegewezen", issue["id"])
    return issue


def update_issue(issue_id: str, changes: dict, *, by_agent_id: str | None = None) -> dict:
    allowed = {"title", "description", "status", "priority", "assignee_agent_id", "goal_id"}
    changes = {k: v for k, v in changes.items() if k in allowed}
    before = db.get("issues", issue_id)
    if "assignee_agent_id" in changes and changes["assignee_agent_id"] != before["assignee_agent_id"]:
        changes["checkout_run_id"] = None
    db.update("issues", issue_id, updated_at=db.now(), **changes)
    issue = db.get("issues", issue_id)
    by = db.get("agents", by_agent_id)["name"] if by_agent_id else "Het bestuur"

    if "status" in changes and changes["status"] != before["status"]:
        events.publish(issue["company_id"], "issue.status",
                       f"{by} zette {issue['identifier']} op '{issue['status']}'",
                       agent_id=by_agent_id, issue_id=issue_id)
        # Finished sub-work wakes whoever owns the parent, like a manager checking in.
        if issue["status"] in ("done", "blocked") and issue["parent_id"]:
            parent = db.get("issues", issue["parent_id"])
            if parent and parent["assignee_agent_id"] and parent["assignee_agent_id"] != by_agent_id:
                wake(parent["assignee_agent_id"], "automation",
                     f"Subtaak {issue['identifier']} is '{issue['status']}'", parent["id"])
    if changes.get("assignee_agent_id") and changes["assignee_agent_id"] != before["assignee_agent_id"]:
        events.publish(issue["company_id"], "issue.assigned",
                       f"{by} wees {issue['identifier']} toe", agent_id=changes["assignee_agent_id"], issue_id=issue_id)
        wake(changes["assignee_agent_id"], "assignment", f"{issue['identifier']} is aan je toegewezen", issue_id)
    return issue


def add_comment(issue_id: str, body: str, *, author_agent_id: str | None = None, author_user: str | None = None) -> dict:
    issue = db.get("issues", issue_id)
    comment = db.insert("comments", issue_id=issue_id, author_agent_id=author_agent_id,
                        author_user=author_user if not author_agent_id else None, body=body, created_at=db.now())
    db.update("issues", issue_id, updated_at=db.now())
    who = db.get("agents", author_agent_id)["name"] if author_agent_id else (author_user or "Bestuur")
    events.publish(issue["company_id"], "issue.comment", f"{who} op {issue['identifier']}: {body[:140]}",
                   agent_id=author_agent_id, issue_id=issue_id)

    # @mentions wake the mentioned agents; a human comment also wakes the assignee.
    to_wake = set()
    for a in list_agents(issue["company_id"]):
        if f"@{a['name'].lower()}" in body.lower() and a["id"] != author_agent_id:
            to_wake.add((a["id"], "mention"))
    if not author_agent_id and issue["assignee_agent_id"]:
        to_wake.add((issue["assignee_agent_id"], "mention"))
    for aid, source in to_wake:
        wake(aid, source, f"Nieuwe opmerking op {issue['identifier']}", issue_id)
    return comment


# ── Approvals ───────────────────────────────────────────────────────────────


def approval_view(approval: dict) -> dict:
    payload = db.loads(approval["payload_json"]) or {}
    requester = db.get("agents", approval["requested_by_agent_id"]) if approval["requested_by_agent_id"] else None
    subject = db.get("agents", payload["agent_id"]) if payload.get("agent_id") else None
    return {
        **approval,
        "payload": payload,
        "requested_by": {"id": requester["id"], "name": requester["name"], "icon": requester["icon"]} if requester else None,
        "subject_agent": agent_view(subject) if subject else None,
    }


def create_approval(company_id: str, type_: str, payload: dict, requested_by_agent_id: str | None, message: str) -> dict:
    approval = db.insert("approvals", company_id=company_id, type=type_, requested_by_agent_id=requested_by_agent_id,
                         payload_json=json.dumps(payload), created_at=db.now())
    events.publish(company_id, "approval.created", message, agent_id=requested_by_agent_id)
    return approval


async def decide_approval(approval_id: str, approve: bool, note: str = "") -> dict:
    approval = db.get("approvals", approval_id)
    if not approval or approval["status"] != "pending":
        raise ValueError("Deze goedkeuring is al afgehandeld")
    status = "approved" if approve else "rejected"
    db.update("approvals", approval_id, status=status, decision_note=note, decided_at=db.now())
    payload = db.loads(approval["payload_json"]) or {}
    company_id = approval["company_id"]
    verdict = "goedgekeurd" if approve else "afgewezen"

    if approval["type"] == "hire_agent":
        agent = db.get("agents", payload["agent_id"])
        if approve:
            await activate_agent(agent["id"])
        else:
            db.update("agents", agent["id"], status="terminated")
        events.publish(company_id, "approval.decided", f"Aanname van {agent['name']} {verdict}", agent_id=agent["id"])
    elif approval["type"] == "budget_override_required":
        agent = db.get("agents", payload["agent_id"])
        if approve:
            new_budget = int(payload.get("proposed_budget_cents") or agent["budget_monthly_cents"] * 2 or 1000)
            db.update("agents", agent["id"], budget_monthly_cents=new_budget)
            set_paused(agent["id"], False)
        events.publish(company_id, "approval.decided", f"Budgetverhoging voor {agent['name']} {verdict}", agent_id=agent["id"])
    else:  # request_board_approval
        text = f"Het bestuur heeft {verdict}: {payload.get('question', '')}" + (f"\nToelichting: {note}" if note else "")
        if payload.get("issue_id"):
            add_comment(payload["issue_id"], text, author_user="Bestuur")
        events.publish(company_id, "approval.decided", text[:200], agent_id=approval["requested_by_agent_id"])

    if approval["requested_by_agent_id"]:
        wake(approval["requested_by_agent_id"], "approval", f"Je verzoek is {verdict}", payload.get("issue_id"))
    return db.get("approvals", approval_id)
