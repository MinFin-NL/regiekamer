"""Chat-model runtimes: Azure OpenAI and a local Ollama, with Pydantic AI
running the tool loop.

`chat` uses the same env vars and API-key auth as invulhulp's backend/llm.py.
`ollama` talks to Ollama's OpenAI-compatible endpoint, so the demo also works
offline with a tool-capable local model (qwen3, llama3.1/3.2,
mistral-small3.1, …). Agents on these runtimes only exist in our database.

Small models often answer in prose instead of calling the tools. An output
validator catches that and sends them back (ModelRetry) up to MAX_NUDGES
times; after that _salvage posts the text on the issue for a human to check.
"""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING

import httpx
import pydantic_ai
from openai import AsyncAzureOpenAI, AsyncOpenAI
from pydantic import ValidationError
from pydantic_ai import Agent, ModelRetry, RunContext, Tool
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter, ModelRequest, ModelResponse, TextPart
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.azure import AzureProvider
from pydantic_ai.providers.ollama import OllamaProvider
from pydantic_ai.usage import RunUsage, UsageLimits

import telemetry
import templates
from runtimes.base import RunResult

if TYPE_CHECKING:
    from pydantic_ai.models import Model

    from tools import Toolbox

# Suppress the Logfire suggestion Pydantic AI prints to stderr on first use.
pydantic_ai.BANNER_ENABLED = False

MAX_TOOL_ROUNDS = int(os.environ.get("AGENT_MAX_TOOL_ROUNDS", "12"))
MAX_NUDGES = 2
HISTORY_LIMIT = 40
# Toolbox returns tool errors as data, so this only covers calls to unknown
# tools and arguments that aren't valid JSON.
TOOL_RETRIES = 3
# Reasoning models served by Ollama (qwen3, deepseek-r1) may inline their thoughts.
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    return default if raw is None else raw.strip().lower() in ("1", "true", "yes", "on")


def _tool(toolbox: Toolbox, spec: dict) -> Tool:
    async def call(**args) -> dict:
        return await toolbox.call(spec["name"], args)

    # Tools touch the same issues (checkout, then comment), so run them in the
    # order the model asked for.
    return Tool.from_schema(call, name=spec["name"], description=spec.get("description"),
                            json_schema=spec["parameters"], sequential=True)


def _nudge(pending: str) -> str:
    return (f"{pending} is nog niet bijgewerkt: tekst in je antwoord komt niet in het issue terecht. "
            f"Claim {pending} met checkout_issue als je dat nog niet deed, zet je uitwerking erin met "
            "add_comment (of delegeer met create_subtask) en zet daarna de status met update_issue_status.")


def _load_history(session_ref: str | None) -> list[ModelMessage]:
    if not session_ref:
        return []
    try:
        return ModelMessagesTypeAdapter.validate_json(session_ref)
    except ValidationError:
        return []  # a session from the old hand-written loop; start fresh


class ChatRuntime:
    name = "chat"
    label = "Azure OpenAI chat (API-key)"
    provider = "azure-openai"

    def __init__(self) -> None:
        self._client: AsyncOpenAI | None = None

    def configured(self) -> bool:
        return bool(os.environ.get("AZURE_OPENAI_ENDPOINT"))

    def default_model(self) -> str:
        return os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-5.3-chat")

    async def models(self) -> list[str]:
        return []

    def _make_client(self) -> AsyncOpenAI:
        return AsyncAzureOpenAI(
            azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
            api_key=os.environ.get("AZURE_OPENAI_API_KEY", ""),
            api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2025-04-01-preview"),
        )

    def client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = self._make_client()
        return self._client

    def _model(self, model_name: str) -> Model:
        reasoning = _env_flag("AZURE_OPENAI_REASONING_MODEL", model_name.startswith(("o1", "o3", "o4")))
        profile = {"openai_system_prompt_role": "developer"} if reasoning else None
        return OpenAIChatModel(model_name, provider=AzureProvider(openai_client=self.client()), profile=profile)

    async def provision(self, agent: dict) -> dict:
        return {"runtime_ref": None, "runtime_version": None}

    async def update(self, agent: dict) -> dict:
        return {}

    async def deprovision(self, agent: dict) -> None:
        return None

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None

    async def run(self, agent: dict, ctx: dict, toolbox: Toolbox, session_ref: str | None) -> RunResult:
        model_name = agent["model"] or self.default_model()
        focus = (ctx.get("focus_issue") or {}).get("identifier")

        pai = Agent(
            self._model(model_name),
            name=agent["name"],
            capabilities=telemetry.agent_capabilities(),
            instructions=templates.agent_prompt(agent),
            tools=[_tool(toolbox, spec) for spec in toolbox.specs],
            retries={"tools": TOOL_RETRIES, "output": MAX_NUDGES},
        )

        @pai.output_validator
        def must_touch_the_issue(run: RunContext[None], output: str) -> str:
            pending = toolbox.unfinished_work(focus)
            if pending and run.retry < MAX_NUDGES:
                raise ModelRetry(_nudge(pending))
            return _THINK.sub("", output).strip()

        history = _load_history(session_ref)
        usage = RunUsage()
        # Parent span for Pydantic AI's spans; the ids link a trace to heartbeat_runs.
        with telemetry.tracer().start_as_current_span(f"heartbeat {agent['name']}", attributes={
            "regiekamer.run_id": toolbox.run_id,
            "regiekamer.agent_id": agent["id"],
            "regiekamer.company_id": agent["company_id"],
            "regiekamer.runtime": self.name,
            "regiekamer.issue": focus or "",
        }) as span:
            try:
                result = await pai.run(ctx["task_markdown"], message_history=history, usage=usage,
                                       usage_limits=UsageLimits(request_limit=MAX_TOOL_ROUNDS))
                final = result.output
            except UsageLimitExceeded:
                final = f"Gestopt na {MAX_TOOL_ROUNDS} toolrondes."
                span.set_attribute("regiekamer.stopped_at_limit", True)

            await self._salvage(toolbox, focus, final)
            span.set_attribute("regiekamer.tool_calls", len(toolbox.calls))
            span.set_attribute("regiekamer.tool_errors", sum(not c["ok"] for c in toolbox.calls))

        # Keep only the prompt and final answer of each turn: the tool calls are
        # already reflected in the issue state the next wake reads.
        turn: list[ModelMessage] = [
            ModelRequest.user_text_prompt(ctx["task_markdown"]),
            ModelResponse(parts=[TextPart(final)], model_name=model_name),
        ]
        new_history = (history + turn)[-HISTORY_LIMIT:]
        return RunResult(
            summary=final[:2000] or "(geen samenvatting)",
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            model=model_name,
            provider=self.provider,
            session_ref=ModelMessagesTypeAdapter.dump_json(new_history).decode(),
            extra={"requests": usage.requests},
        )

    @staticmethod
    async def _salvage(toolbox: Toolbox, focus: str | None, final: str) -> None:
        """Record a text-only answer on the issue when the nudges didn't help.

        Ollama ignores tool_choice="required", so a model can keep answering in
        plain text. Post that answer through the normal tools, marked as such,
        and put the issue in review for a human.
        """
        pending = toolbox.unfinished_work(focus)
        if not pending or len(final) < 40:
            return
        claim = await toolbox.call("checkout_issue", {"issue": pending})
        if "error" in claim:
            return
        await toolbox.call("add_comment", {"issue": pending, "body":
            "(Automatisch vastgelegd: de agent antwoordde zonder de tools te gebruiken. Controleer dit.)\n\n" + final})
        await toolbox.call("update_issue_status", {"issue": pending, "status": "in_review"})


class OllamaRuntime(ChatRuntime):
    """Local models through Ollama. Configured by OLLAMA_BASE_URL.

    Tool schemas and org context take 3-4k tokens before any work starts, more
    than Ollama's default context window. Start the server with
    OLLAMA_CONTEXT_LENGTH=16384 (docker-compose.ollama.yml does this).
    """

    name = "ollama"
    label = "Ollama (lokaal model)"
    provider = "ollama"
    # A 20B+ model on a laptop can take minutes per heartbeat.
    run_timeout_sec = float(os.environ.get("OLLAMA_RUN_TIMEOUT_SEC", "900"))

    def base_url(self) -> str:
        return os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")

    def configured(self) -> bool:
        return bool(os.environ.get("OLLAMA_BASE_URL"))

    def default_model(self) -> str:
        return os.environ.get("OLLAMA_MODEL", "qwen3:8b")

    async def models(self) -> list[str]:
        """Installed models, for the model picker in the hire form."""
        try:
            async with httpx.AsyncClient(timeout=2) as http:
                resp = await http.get(f"{self.base_url()}/api/tags")
                resp.raise_for_status()
                return sorted(m["name"] for m in resp.json().get("models", []) if "embed" not in m["name"])
        except (httpx.HTTPError, ValueError, KeyError):
            return []

    async def provision(self, agent: dict) -> dict:
        # Fail the hire (the agent card shows the error) rather than the first heartbeat.
        model = agent["model"] or self.default_model()
        installed = await self.models()
        if not installed:
            raise RuntimeError(f"Ollama niet bereikbaar op {self.base_url()}")
        if model not in installed and f"{model}:latest" not in installed:
            raise RuntimeError(f"Model '{model}' staat niet in Ollama; draai `ollama pull {model}`")
        return await super().provision(agent)

    def _make_client(self) -> AsyncOpenAI:
        # A single call can take minutes on a laptop CPU.
        return AsyncOpenAI(base_url=f"{self.base_url()}/v1", api_key="ollama", timeout=600)

    def _model(self, model_name: str) -> Model:
        return OllamaModel(model_name, provider=OllamaProvider(openai_client=self.client()))
