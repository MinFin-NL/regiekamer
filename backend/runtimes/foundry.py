"""Azure AI Foundry Agent Service runtime.

What a click on "Aannemen" does here:

  provision  → project.agents.create_version(<name>, PromptAgentDefinition(
                   model=<deployment>, instructions=<role + protocol>,
                   tools=[FunctionTool(...) for each coordination
                          tool and each of the agent's work tools]))
               The agent now shows up in the Foundry portal, with our tool
               schemas, and can be evaluated/traced there like any other.
  update     → create_version again: Foundry keeps the version history.
  deprovision→ project.agents.delete(<name>)
  run        → the OpenAI Responses API on the project endpoint, pointed at
               the agent with `agent_reference`. One Foundry conversation per
               (agent, issue) keeps context between heartbeats. Function calls
               come back as `function_call` output items; we execute them
               against our Toolbox and send `function_call_output` items back
               until the agent answers in plain text.

Our tools are client-side function tools on purpose: the control plane is the
source of truth for issues and the org chart, and a function tool keeps every
mutation inside our process (and our checkout/delegation rules) instead of
giving Foundry direct write access to our database.

Auth is Entra ID only (Agent Service has no API keys): DefaultAzureCredential
picks up the Container App's managed identity in Azure and `az login` locally.
The identity needs the "Azure AI User" role on the Foundry project.

All SDK calls live in this file. azure-ai-projects is pinned in pyproject.toml
(2.x = the GA "v1" Foundry API); if the SDK moves, only this file changes.
"""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING, Any

import templates
from runtimes.base import RunResult

if TYPE_CHECKING:
    from tools import Toolbox

MAX_TOOL_ROUNDS = int(os.environ.get("AGENT_MAX_TOOL_ROUNDS", "12"))


def foundry_agent_name(agent: dict) -> str:
    """Foundry names: alphanumerics and hyphens, max 63, stable per agent."""
    slug = re.sub(r"[^a-z0-9]+", "-", agent["name"].lower()).strip("-")[:40] or "agent"
    return f"rk-{slug}-{agent['id'][:8]}"


class FoundryRuntime:
    name = "foundry"
    label = "Azure AI Foundry Agent Service"

    def __init__(self) -> None:
        self._credential: Any = None
        self._project: Any = None
        self._openai: Any = None

    def configured(self) -> bool:
        return bool(os.environ.get("FOUNDRY_PROJECT_ENDPOINT"))

    def default_model(self) -> str:
        return os.environ.get("FOUNDRY_MODEL_DEPLOYMENT", "gpt-5-mini")

    # ── clients (lazy, so the app starts without Azure config or packages) ──

    def project(self) -> Any:
        if self._project is None:
            if not self.configured():
                raise RuntimeError("FOUNDRY_PROJECT_ENDPOINT is niet ingesteld")
            from azure.ai.projects.aio import AIProjectClient
            from azure.identity.aio import DefaultAzureCredential

            self._credential = DefaultAzureCredential()
            self._project = AIProjectClient(endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"], credential=self._credential)
        return self._project

    def openai(self) -> Any:
        if self._openai is None:
            self._openai = self.project().get_openai_client()
        return self._openai

    async def close(self) -> None:
        for obj in (self._openai, self._project, self._credential):
            if obj is not None:
                await obj.close()
        self._credential = self._project = self._openai = None

    # ── lifecycle ──

    def _definition(self, agent: dict) -> Any:
        from azure.ai.projects.models import FunctionTool, PromptAgentDefinition
        from tools import specs_for_agent

        return PromptAgentDefinition(
            model=agent["model"] or self.default_model(),
            instructions=templates.agent_prompt(agent),
            tools=[
                FunctionTool(name=s["name"], description=s["description"], parameters=s["parameters"], strict=False)
                for s in specs_for_agent(agent)
            ],
        )

    async def _publish_version(self, agent: dict) -> dict:
        name = agent.get("runtime_ref") or foundry_agent_name(agent)
        version = await self.project().agents.create_version(
            name,
            definition=self._definition(agent),
            description=f"{agent['title']} — Regiekamer"[:512],
            metadata={"regiekamer_agent_id": agent["id"], "regiekamer_company_id": agent["company_id"]},
        )
        return {"runtime_ref": name, "runtime_version": str(getattr(version, "version", "") or "")}

    async def provision(self, agent: dict) -> dict:
        return await self._publish_version(agent)

    async def update(self, agent: dict) -> dict:
        return await self._publish_version(agent)

    async def deprovision(self, agent: dict) -> None:
        if agent.get("runtime_ref"):
            await self.project().agents.delete(agent["runtime_ref"])

    # ── heartbeat ──

    async def run(self, agent: dict, ctx: dict, toolbox: Toolbox, session_ref: str | None) -> RunResult:
        if not agent.get("runtime_ref"):
            raise RuntimeError("Agent is nog niet ingericht in Foundry (runtime_ref ontbreekt)")
        client = self.openai()
        conversation_id = session_ref or (await client.conversations.create(
            metadata={"regiekamer_agent_id": agent["id"]})).id
        agent_ref = {"agent_reference": {"name": agent["runtime_ref"], "type": "agent_reference"}}

        pending_input: list[dict] | str = ctx["task_markdown"]
        usage_in = usage_out = 0
        response = None
        for _ in range(MAX_TOOL_ROUNDS):
            response = await client.responses.create(
                conversation=conversation_id, input=pending_input, extra_body=agent_ref
            )
            if response.usage:
                usage_in += response.usage.input_tokens or 0
                usage_out += response.usage.output_tokens or 0
            calls = [item for item in response.output if getattr(item, "type", None) == "function_call"]
            if not calls:
                break
            pending_input = [
                {"type": "function_call_output", "call_id": c.call_id,
                 "output": await toolbox.call_json(c.name, c.arguments)}
                for c in calls
            ]
        summary = (response.output_text if response else "") or f"Gestopt na {MAX_TOOL_ROUNDS} toolrondes."
        return RunResult(
            summary=summary.strip()[:2000],
            input_tokens=usage_in,
            output_tokens=usage_out,
            model=agent["model"] or self.default_model(),
            provider="azure-ai-foundry",
            session_ref=conversation_id,
            extra={"foundry_agent": agent["runtime_ref"], "foundry_version": agent.get("runtime_version"),
                   "response_id": getattr(response, "id", None)},
        )

