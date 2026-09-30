from types import SimpleNamespace

import db
import org
from conftest import hire
from heartbeat import Scheduler
from runtimes.foundry import FoundryRuntime
from tools import Toolbox


async def test_board_hire_without_approval_is_provisioned(company, ceo):
    agent = await hire(company["id"], template="jurist", name="Jet", reports_to=ceo["id"])
    assert agent["status"] == "idle"
    assert agent["runtime_ref"] == f"mock-{agent['id']}"
    assert agent["title"] == "Jurist"
    assert db.one("SELECT COUNT(*) AS n FROM approvals")["n"] == 0


async def test_hire_with_approval_waits_for_board(company, ceo):
    db.update("companies", company["id"], require_board_approval_for_new_agents=1)
    agent = await hire(company["id"], template="researcher", name="Rik")
    assert agent["status"] == "pending_approval"
    assert agent["reports_to"] == ceo["id"]  # defaults to the director
    approval = db.one("SELECT * FROM approvals WHERE type = 'hire_agent'")
    await org.decide_approval(approval["id"], True)
    assert db.get("agents", agent["id"])["status"] == "idle"


async def test_agent_requested_hire_always_needs_approval(company, ceo):
    toolbox = Toolbox(ceo, "run-1")
    result = await toolbox.call("request_hire", {"template": "data", "name": "Daan", "reason": "cijfers nodig"})
    assert result["status"] == "pending_approval"
    approval = db.one("SELECT * FROM approvals WHERE type = 'hire_agent'")
    assert approval["requested_by_agent_id"] == ceo["id"]


async def test_checkout_is_exclusive_per_run(company, ceo):
    issue = org.create_issue(company["id"], {"title": "x", "assignee_agent_id": ceo["id"]})
    first = await Toolbox(ceo, "run-a").call("checkout_issue", {"issue": issue["identifier"]})
    second = await Toolbox(ceo, "run-b").call("checkout_issue", {"issue": issue["identifier"]})
    assert first["ok"] and first["status"] == "in_progress"
    assert "Conflict" in second["error"]


async def test_status_change_requires_checkout(company, ceo):
    issue = org.create_issue(company["id"], {"title": "x", "assignee_agent_id": ceo["id"]})
    result = await Toolbox(ceo, "run-a").call("update_issue_status", {"issue": issue["identifier"], "status": "done"})
    assert "checkout_issue" in result["error"]


async def test_delegation_only_within_own_team(company, ceo):
    a = await hire(company["id"], template="cto", name="Anna", reports_to=ceo["id"])
    b = await hire(company["id"], template="jurist", name="Bram", reports_to=ceo["id"])
    parent = org.create_issue(company["id"], {"title": "p", "assignee_agent_id": a["id"]})
    toolbox = Toolbox(db.get("agents", a["id"]), "run-a")
    denied = await toolbox.call("create_subtask", {"parent_issue": parent["identifier"], "title": "t",
                                                    "description": "d", "assignee": "Bram"})
    assert "niet onder jou" in denied["error"]
    ok = await Toolbox(ceo, "run-c").call("create_subtask", {"parent_issue": parent["identifier"], "title": "t",
                                                            "description": "d", "assignee": "@bram"})
    assert ok["ok"] and ok["assignee"] == b["name"]


async def test_full_delegation_flow_with_mock_runtime(company, ceo, scheduler):
    await hire(company["id"], template="researcher", name="Rik", reports_to=ceo["id"])
    issue = org.create_issue(company["id"], {"title": "Onderzoek", "assignee_agent_id": ceo["id"]})
    await scheduler.drain()
    issue = db.get("issues", issue["id"])
    child = db.one("SELECT * FROM issues WHERE parent_id = ?", issue["id"])
    assert child["status"] == "done"
    assert issue["status"] == "done"
    assert issue["checkout_run_id"] is None
    assert db.one("SELECT COUNT(*) AS n FROM cost_events")["n"] == 3


async def test_budget_exhaustion_pauses_and_asks_board(company, ceo, scheduler):
    db.update("agents", ceo["id"], budget_monthly_cents=1)
    db.insert("cost_events", company_id=company["id"], agent_id=ceo["id"], provider="mock", model="mock",
              cost_cents=5, occurred_at=db.now())
    org.create_issue(company["id"], {"title": "x", "assignee_agent_id": ceo["id"]})
    await scheduler.drain()
    agent = db.get("agents", ceo["id"])
    assert agent["status"] == "paused"
    approval = db.one("SELECT * FROM approvals WHERE type = 'budget_override_required'")
    assert approval
    await org.decide_approval(approval["id"], True)
    agent = db.get("agents", ceo["id"])
    assert agent["status"] == "idle" and agent["budget_monthly_cents"] == 2


async def test_wakes_are_coalesced_while_queued(company, ceo):
    s = Scheduler()  # not started: nothing drains the queue
    i1 = org.create_issue(company["id"], {"title": "a"})
    i2 = org.create_issue(company["id"], {"title": "b"})
    assert s.wake(ceo["id"], "timer", "tick")
    assert s.wake(ceo["id"], "assignment", "a", i1["id"])
    assert s.wake(ceo["id"], "mention", "b", i2["id"])
    req = s._pending[ceo["id"]]
    assert s._queue.qsize() == 1
    assert req.source == "assignment"
    assert req.issue_ids == [i1["id"], i2["id"]]


async def test_paused_agent_is_not_woken(company, ceo):
    org.set_paused(ceo["id"], True)
    assert not Scheduler().wake(ceo["id"], "on_demand")


class FakeResponses:
    """Plays back: first a function_call, then a final text answer."""

    def __init__(self):
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        usage = SimpleNamespace(input_tokens=100, output_tokens=20)
        if len(self.calls) == 1:
            call = SimpleNamespace(type="function_call", call_id="c1", name="list_my_issues", arguments="{}")
            return SimpleNamespace(id="r1", output=[call], output_text="", usage=usage)
        return SimpleNamespace(id="r2", output=[SimpleNamespace(type="message")], output_text="Klaar.", usage=usage)


async def test_foundry_runtime_executes_function_calls(company, ceo):
    responses = FakeResponses()

    async def create_conversation(**_):
        return SimpleNamespace(id="conv-1")

    runtime = FoundryRuntime()
    runtime._openai = SimpleNamespace(responses=responses,
                                      conversations=SimpleNamespace(create=create_conversation))
    agent = {**ceo, "runtime_ref": "rk-directeur-dewi-1234", "model": "gpt-5-mini"}
    toolbox = Toolbox(agent, "run-f")
    result = await runtime.run(agent, {"task_markdown": "Werk"}, toolbox, None)

    assert result.summary == "Klaar."
    assert (result.input_tokens, result.output_tokens) == (200, 40)
    assert result.session_ref == "conv-1"
    assert responses.calls[0]["extra_body"]["agent_reference"]["name"] == "rk-directeur-dewi-1234"
    follow_up = responses.calls[1]["input"][0]
    assert follow_up["type"] == "function_call_output" and follow_up["call_id"] == "c1"
    assert toolbox.calls == [{"tool": "list_my_issues", "args": {}, "ok": True}]


async def test_tools_accept_aliases_and_explain_bad_arguments(company, ceo):
    issue = org.create_issue(company["id"], {"title": "x", "assignee_agent_id": ceo["id"]})
    toolbox = Toolbox(ceo, "run-a")
    ref = issue["identifier"].replace("-", "").lower()  # "rk4"
    assert (await toolbox.call("checkout_issue", {"issue_id": f"#{ref}"}))["ok"]
    missing = await toolbox.call("create_subtask", {"parent": issue["identifier"], "title": "t"})
    assert "Ontbrekende argumenten: description, assignee" in missing["error"]
    assert "parent_issue (verplicht)" in missing["error"]


async def test_unfinished_work_detects_claimed_or_untouched_issue(company, ceo):
    issue = org.create_issue(company["id"], {"title": "x", "assignee_agent_id": ceo["id"]})
    ref = issue["identifier"]
    toolbox = Toolbox(ceo, "run-a")
    assert toolbox.unfinished_work(ref) == ref  # woken for it, never touched
    await toolbox.call("checkout_issue", {"issue": ref})
    assert toolbox.unfinished_work(ref) == ref  # claimed, nothing recorded
    await toolbox.call("add_comment", {"issue": ref.lower(), "body": "resultaat"})
    assert toolbox.unfinished_work(ref) is None
