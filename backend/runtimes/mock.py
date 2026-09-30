"""Scripted runtime: the whole org works without any model or Azure access.

It follows the same heartbeat protocol an LLM agent is told to follow, and it
only acts through the Toolbox, so it exercises exactly the code paths the
real runtimes use. Managers delegate to their reports; leaves "do" the work.
"""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING

from runtimes.base import RunResult

if TYPE_CHECKING:
    from tools import Toolbox

MOCK_DELAY_SEC = float(os.environ.get("MOCK_DELAY_SEC", "1.5"))


class MockRuntime:
    name = "mock"
    label = "Demo (gescript, geen model)"

    def configured(self) -> bool:
        return True

    def default_model(self) -> str:
        return "mock"

    async def provision(self, agent: dict) -> dict:
        return {"runtime_ref": f"mock-{agent['id']}", "runtime_version": "1"}

    async def update(self, agent: dict) -> dict:
        return {"runtime_version": str(int(agent.get("runtime_version") or 1) + 1)}

    async def deprovision(self, agent: dict) -> None:
        return None

    async def close(self) -> None:
        return None

    async def run(self, agent: dict, ctx: dict, toolbox: Toolbox, session_ref: str | None) -> RunResult:
        await asyncio.sleep(MOCK_DELAY_SEC)  # so the UI visibly shows "running"
        inbox = (await toolbox.call("list_my_issues", {}))["issues"]
        if not inbox:
            return self._result("Geen open werk in mijn inbox.")

        focus = ctx.get("focus_issue") or {}
        issue = next((i for i in inbox if i["identifier"] == focus.get("identifier")), inbox[0])
        ref = issue["identifier"]

        claimed = await toolbox.call("checkout_issue", {"issue": ref})
        if "error" in claimed:
            return self._result(f"{ref} kon ik niet claimen: {claimed['error']}")

        details = await toolbox.call("get_issue", {"issue": ref})
        subtasks = details["subtasks"]
        open_subtasks = [s for s in subtasks if s["status"] not in ("done", "cancelled")]
        reports = ctx.get("direct_reports") or []

        if subtasks and open_subtasks:
            await toolbox.call("add_comment", {"issue": ref, "body":
                f"Tussenstand: {len(subtasks) - len(open_subtasks)} van {len(subtasks)} subtaken afgerond. Ik wacht op de rest."})
            return self._result(f"{ref}: wacht op {len(open_subtasks)} subtaken.")

        if subtasks:
            lines = "\n".join(f"- {s['identifier']} ({s['assignee'] or '—'}): {s['title']}" for s in subtasks)
            await toolbox.call("add_comment", {"issue": ref, "body":
                f"Alle deelresultaten zijn binnen. Samenvatting voor het bestuur:\n{lines}\n\nConclusie: klaar voor besluitvorming."})
            await toolbox.call("update_issue_status", {"issue": ref, "status": "done"})
            return self._result(f"{ref} afgerond op basis van {len(subtasks)} subtaken.")

        if reports:
            delegate = reports[sum(map(ord, ref)) % len(reports)]
            sub = await toolbox.call("create_subtask", {
                "parent_issue": ref,
                "title": f"Uitwerken: {issue['title']}",
                "description": f"Werk vanuit jouw rol ({delegate['title']}) een bijdrage uit voor {ref}.\n\n{issue['description']}",
                "assignee": delegate["name"],
            })
            await toolbox.call("add_comment", {"issue": ref, "body":
                f"Gedelegeerd aan {delegate['name']} ({delegate['title']}) als {sub.get('issue', '?')}."})
            await toolbox.call("update_issue_status", {"issue": ref, "status": "in_progress"})
            return self._result(f"{ref} gedelegeerd aan {delegate['name']}.")

        work = ("1. Vraagstelling afgebakend.\n2. Drie opties op een rij gezet met voor- en nadelen.\n"
                "3. Aanbeveling: optie 2, met de aannames hierboven.\n(Dit is gescripte demo-output.)")
        if any(s["name"] == "write_document" for s in toolbox.specs):
            doc = await toolbox.call("write_document", {"title": f"{ref} — {issue['title']}", "issue": ref,
                                                        "body": f"# {issue['title']}\n\n{work}"})
            await toolbox.call("add_comment", {"issue": ref, "body":
                f"[{agent['title'] or agent['role']}] Uitwerking vastgelegd in document '{doc.get('title', ref)}'."})
        else:
            await toolbox.call("add_comment", {"issue": ref, "body":
                f"[{agent['title'] or agent['role']}] Uitwerking van '{issue['title']}':\n{work}"})
        await toolbox.call("update_issue_status", {"issue": ref, "status": "done"})
        return self._result(f"{ref} uitgewerkt en afgerond.")

    def _result(self, summary: str) -> RunResult:
        return RunResult(summary=summary, input_tokens=1800, output_tokens=350, model="mock", provider="mock")
