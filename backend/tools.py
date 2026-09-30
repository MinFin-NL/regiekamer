"""Coordination tools: how an agent reads and changes the organisation.

Every call is scoped to one agent and one heartbeat run, and every mutation
carries the run id. The tools run in-process and go through org.py, so an
agent is held to the same rules as the UI.

TOOL_SPECS holds the JSON schemas of these tools; every agent has them. Work
tools (documents, law, web, open data) live in worktools.py and are switched
on per agent. specs_for_agent() combines both: the Foundry runtime registers
them on the agent version as FunctionTools, the chat runtimes hand them to
Pydantic AI.
"""

import json
import re
from typing import Any

import db
import org
import templates
import worktools

OPEN = org.OPEN_ISSUE_STATUSES

TOOL_SPECS: list[dict] = [
    {
        "name": "list_my_issues",
        "description": "Geeft de open issues die aan jou zijn toegewezen (je inbox), belangrijkste eerst.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_issue",
        "description": "Details, subtaken en opmerkingen van één issue.",
        "parameters": {
            "type": "object",
            "properties": {"issue": {"type": "string", "description": "Issue-id of kenmerk, bijv. RK-3"}},
            "required": ["issue"],
            "additionalProperties": False,
        },
    },
    {
        "name": "checkout_issue",
        "description": "Claim een issue voor deze heartbeat zodat niemand anders er tegelijk aan werkt. "
        "Bij een conflict: niet opnieuw proberen.",
        "parameters": {
            "type": "object",
            "properties": {"issue": {"type": "string"}},
            "required": ["issue"],
            "additionalProperties": False,
        },
    },
    {
        "name": "add_comment",
        "description": "Plaats een opmerking (je resultaat, een vraag of een tussenstand) op een issue.",
        "parameters": {
            "type": "object",
            "properties": {"issue": {"type": "string"}, "body": {"type": "string"}},
            "required": ["issue", "body"],
            "additionalProperties": False,
        },
    },
    {
        "name": "update_issue_status",
        "description": "Zet de status van een issue dat jij hebt geclaimd.",
        "parameters": {
            "type": "object",
            "properties": {
                "issue": {"type": "string"},
                "status": {"type": "string", "enum": ["in_progress", "in_review", "blocked", "done"]},
            },
            "required": ["issue", "status"],
            "additionalProperties": False,
        },
    },
    {
        "name": "create_subtask",
        "description": "Delegeer werk: maak een subtaak onder een issue en wijs die toe aan jezelf of iemand uit je team.",
        "parameters": {
            "type": "object",
            "properties": {
                "parent_issue": {"type": "string"},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "assignee": {"type": "string", "description": "Naam of id van de medewerker"},
            },
            "required": ["parent_issue", "title", "description", "assignee"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_org_chart",
        "description": "Het organogram: wie er werkt, in welke rol en wie aan wie rapporteert.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "request_hire",
        "description": "Vraag het bestuur om een nieuwe collega in jouw team. Het bestuur moet dit goedkeuren.",
        "parameters": {
            "type": "object",
            "properties": {
                "template": {"type": "string", "enum": [t["key"] for t in templates.TEMPLATES]},
                "name": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["template", "name", "reason"],
            "additionalProperties": False,
        },
    },
    {
        "name": "request_board_approval",
        "description": "Leg een beslissing boven jouw mandaat voor aan het (menselijke) bestuur.",
        "parameters": {
            "type": "object",
            "properties": {"question": {"type": "string"}, "issue": {"type": "string"}},
            "required": ["question", "issue"],
            "additionalProperties": False,
        },
    },
]


_SPEC_BY_NAME = {s["name"]: s for s in TOOL_SPECS + worktools.ALL_SPECS}

# Argument names models tend to invent (small local ones especially) → ours.
# A tuple lists candidates; the first one the tool actually has wins.
_ALIASES: dict[str, str | tuple[str, ...]] = {
    "issue_id": "issue", "identifier": "issue", "issue_identifier": "issue", "key": "issue",
    "id": ("issue", "document", "dataset"),
    "parent": "parent_issue", "parent_id": "parent_issue", "parent_issue_id": "parent_issue",
    "comment": "body", "text": "body", "message": "body", "content": "body",
    "assignee_name": "assignee", "assignee_id": "assignee", "agent": "assignee", "to": "assignee",
    "role": "template", "new_status": "status", "state": "status",
    "q": "query", "search": "query", "search_query": "query", "keywords": "query", "term": "query",
    "link": "url", "href": "url", "website": "url", "page": "url",
    "name": ("dataset", "document", "title"), "document_id": "document", "doc": "document", "document_title": "document",
    "dataset_id": "dataset",
    "wet": "law", "bwb_id": "law", "bwbid": "law", "law_id": "law", "law_name": "law",
    "artikel": "article", "article_number": "article", "nr": "article",
}


class ToolError(Exception):
    pass


def normalize_args(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Map aliased argument names onto the schema; unknown extras are dropped."""
    spec = _SPEC_BY_NAME.get(name)
    if not spec:
        return args
    allowed = spec["parameters"]["properties"]
    out: dict[str, Any] = {}
    for key, value in args.items():
        if key not in allowed:
            candidates = _ALIASES.get(key, ())
            key = next((c for c in ((candidates,) if isinstance(candidates, str) else candidates) if c in allowed), key)
        if key in allowed and key not in out:
            out[key] = value
    return out


def specs_for_agent(agent: dict) -> list[dict]:
    """Coordination tools for everyone, plus the agent's own work tools."""
    return TOOL_SPECS + worktools.specs_for(templates.agent_toolsets(agent))


def canonical_ref(ref: Any) -> str:
    """'#rk3', ' RK-3 ' → 'RK-3' (ids pass through unchanged)."""
    ref = str(ref or "").strip().lstrip("#")
    if re.fullmatch(r"[A-Za-z]+\d+", ref):
        ref = re.sub(r"(\d+)$", r"-\1", ref)
    return ref.upper() if re.fullmatch(r"[A-Za-z]+-\d+", ref) else ref


def usage_hint(name: str) -> str:
    spec = _SPEC_BY_NAME[name]
    required = set(spec["parameters"].get("required", []))
    params = ", ".join(f"{p}{' (verplicht)' if p in required else ''}" for p in spec["parameters"]["properties"])
    return f"{name} verwacht: {params or 'geen argumenten'}"


class Toolbox:
    """Tools bound to one agent and one heartbeat run."""

    def __init__(self, agent: dict, run_id: str):
        self.agent = agent
        self.run_id = run_id
        self.company_id = agent["company_id"]
        self.calls: list[dict] = []
        self.specs = specs_for_agent(agent)
        self._names = {s["name"] for s in self.specs}

    async def call(self, name: str, args: dict[str, Any] | None) -> dict:
        """Run a tool; errors come back as data so the model can react to them."""
        args = args or {}
        try:
            if name not in self._names:
                if name in worktools.TOOLSET_OF:
                    raise ToolError(f"{name} hoort niet bij jouw werktools. Beschikbaar: {', '.join(sorted(self._names))}")
                raise ToolError(f"Onbekende tool: {name}. Beschikbaar: {', '.join(sorted(self._names))}")
            missing = [p for p in _SPEC_BY_NAME[name]["parameters"].get("required", [])
                       if not normalize_args(name, args).get(p)]
            if missing:
                raise ToolError(f"Ontbrekende argumenten: {', '.join(missing)}. {usage_hint(name)}")
            if name in worktools.TOOLSET_OF:
                result = await worktools.call(self, name, normalize_args(name, args))
            else:
                result = getattr(self, f"_t_{name}")(**normalize_args(name, args))
                if hasattr(result, "__await__"):
                    result = await result
        except (ToolError, worktools.WorkToolError) as exc:
            result = {"error": str(exc)}
        self.calls.append({"tool": name, "args": normalize_args(name, args), "ok": "error" not in result})
        return result

    def unfinished_work(self, focus: str | None = None) -> str | None:
        """An issue this run should have acted on but didn't.

        Either one it claimed and then left without a comment, delegation or
        status, or the issue it was woken for and never touched. Small (local)
        models often stop with "I'll get to it" or answer in plain text; the chat
        runtimes use this to nudge them back to the tools.
        """
        ok = [c for c in self.calls if c["ok"]]
        claimed = [canonical_ref(c["args"].get("issue")) for c in ok if c["tool"] == "checkout_issue"]
        acted = {canonical_ref(c["args"].get("issue") or c["args"].get("parent_issue")) for c in ok
                 if c["tool"] in ("add_comment", "update_issue_status", "create_subtask", "request_board_approval",
                                  "write_document")}
        pending = next((ref for ref in claimed if ref not in acted), None)
        if pending or acted or not focus:
            return pending
        issue = db.one("SELECT status, assignee_agent_id FROM issues WHERE company_id = ? AND identifier = ?",
                       self.company_id, focus)
        if issue and issue["assignee_agent_id"] == self.agent["id"] and issue["status"] in ("backlog", "todo"):
            return focus
        return None

    async def call_json(self, name: str, arguments: str | None) -> str:
        try:
            args = json.loads(arguments) if arguments else {}
        except json.JSONDecodeError:
            return json.dumps({"error": "Argumenten zijn geen geldige JSON"})
        return json.dumps(await self.call(name, args), ensure_ascii=False)

    # ── lookups ──

    def _issue(self, ref: str) -> dict:
        ref = canonical_ref(ref)
        issue = db.one(
            "SELECT * FROM issues WHERE company_id = ? AND (id = ? OR UPPER(identifier) = UPPER(?))",
            self.company_id, ref, ref,
        )
        if not issue:
            raise ToolError(f"Issue '{ref}' bestaat niet")
        return issue

    def _agent_ref(self, ref: str) -> dict:
        ref = (ref or "").strip().lstrip("@")
        agent = db.one(
            "SELECT * FROM agents WHERE company_id = ? AND status != 'terminated' AND (id = ? OR LOWER(name) = LOWER(?))",
            self.company_id, ref, ref,
        )
        if not agent:
            raise ToolError(f"Medewerker '{ref}' bestaat niet")
        return agent

    def _require_checkout(self, issue: dict) -> None:
        if issue["checkout_run_id"] != self.run_id:
            raise ToolError(f"Claim {issue['identifier']} eerst met checkout_issue")

    @staticmethod
    def _brief(issue: dict) -> dict:
        return {k: issue[k] for k in ("identifier", "title", "status", "priority", "parent_id")} | {
            "description": issue["description"][:500]
        }

    # ── tools ──

    def _t_list_my_issues(self) -> dict:
        issues = db.rows(
            f"""SELECT * FROM issues WHERE assignee_agent_id = ? AND status IN {OPEN}
                ORDER BY CASE priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
                         created_at""",
            self.agent["id"],
        )
        return {"issues": [self._brief(i) for i in issues]}

    def _t_get_issue(self, issue: str) -> dict:
        i = self._issue(issue)
        comments = db.rows(
            """SELECT c.body, c.created_at, COALESCE(a.name, c.author_user, 'Bestuur') AS author
               FROM comments c LEFT JOIN agents a ON a.id = c.author_agent_id
               WHERE c.issue_id = ? ORDER BY c.created_at""",
            i["id"],
        )
        children = db.rows(
            """SELECT i.identifier, i.title, i.status, a.name AS assignee FROM issues i
               LEFT JOIN agents a ON a.id = i.assignee_agent_id WHERE i.parent_id = ?""",
            i["id"],
        )
        parent = db.get("issues", i["parent_id"]) if i["parent_id"] else None
        return {
            **self._brief(i),
            "description": i["description"],
            "parent": parent["identifier"] if parent else None,
            "subtasks": children,
            "comments": comments[-20:],
        }

    def _t_checkout_issue(self, issue: str) -> dict:
        i = self._issue(issue)
        if i["assignee_agent_id"] != self.agent["id"]:
            raise ToolError(f"{i['identifier']} is niet aan jou toegewezen")
        if i["status"] in ("done", "cancelled"):
            raise ToolError(f"{i['identifier']} is al afgesloten")
        # Atomic: only one run can hold the checkout.
        won = db.execute(
            "UPDATE issues SET checkout_run_id = ?, status = CASE WHEN status IN ('backlog','todo') THEN 'in_progress' ELSE status END,"
            " updated_at = ? WHERE id = ? AND (checkout_run_id IS NULL OR checkout_run_id = ?)",
            self.run_id, db.now(), i["id"], self.run_id,
        )
        if not won:
            raise ToolError(f"Conflict: {i['identifier']} is al geclaimd door een andere run. Niet opnieuw proberen.")
        return {"ok": True, "issue": i["identifier"], "status": db.get("issues", i["id"])["status"]}

    def _t_add_comment(self, issue: str, body: str) -> dict:
        i = self._issue(issue)
        org.add_comment(i["id"], body, author_agent_id=self.agent["id"])
        return {"ok": True}

    def _t_update_issue_status(self, issue: str, status: str) -> dict:
        i = self._issue(issue)
        self._require_checkout(i)
        if status not in ("in_progress", "in_review", "blocked", "done"):
            raise ToolError(f"Ongeldige status '{status}'")
        if status == "done":
            open_children = db.one(
                f"SELECT COUNT(*) AS n FROM issues WHERE parent_id = ? AND status IN {OPEN}", i["id"]
            )["n"]
            if open_children:
                raise ToolError(f"{i['identifier']} heeft nog {open_children} open subtaken")
        org.update_issue(i["id"], {"status": status}, by_agent_id=self.agent["id"])
        return {"ok": True, "issue": i["identifier"], "status": status}

    def _t_create_subtask(self, parent_issue: str, title: str, description: str, assignee: str) -> dict:
        parent = self._issue(parent_issue)
        target = self._agent_ref(assignee)
        if target["id"] not in org.subtree_ids(self.agent["id"]):
            raise ToolError(f"{target['name']} valt niet onder jou; je kunt alleen binnen je eigen team delegeren")
        if target["status"] in ("pending_approval", "terminated"):
            raise ToolError(f"{target['name']} is (nog) niet inzetbaar")
        child = org.create_issue(
            self.company_id,
            {"title": title, "description": description, "parent_id": parent["id"],
             "goal_id": parent["goal_id"], "priority": parent["priority"], "assignee_agent_id": target["id"]},
            created_by_agent_id=self.agent["id"],
        )
        return {"ok": True, "issue": child["identifier"], "assignee": target["name"]}

    def _t_get_org_chart(self) -> dict:
        def slim(nodes: list[dict]) -> list[dict]:
            return [
                {"name": n["name"], "title": n["title"], "role": n["role"], "status": n["status"],
                 "capabilities": n["capabilities"], "reports": slim(n["reports"])}
                for n in nodes
            ]
        return {"org": slim(org.org_chart(self.company_id))}

    def _t_request_hire(self, template: str, name: str, reason: str) -> dict:
        if not templates.get_template(template):
            raise ToolError(f"Onbekend sjabloon '{template}'")
        agent = org.hire_agent(
            self.company_id,
            {"template": template, "name": name, "reports_to": self.agent["id"], "reason": reason,
             "runtime": self.agent["runtime"]},
            requested_by_agent_id=self.agent["id"],
        )
        return {"ok": True, "status": "pending_approval", "agent": agent["name"],
                "note": "Het bestuur beslist; je wordt gewekt zodra er een besluit is."}

    def _t_request_board_approval(self, question: str, issue: str) -> dict:
        i = self._issue(issue)
        org.create_approval(
            self.company_id, "request_board_approval", {"question": question, "issue_id": i["id"]},
            self.agent["id"], f"{self.agent['name']} vraagt het bestuur ({i['identifier']}): {question[:140]}",
        )
        return {"ok": True, "note": "Voorgelegd aan het bestuur; je wordt gewekt zodra er een besluit is."}
