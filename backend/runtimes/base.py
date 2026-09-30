"""The runtime contract: where an agent's model calls happen.

The control plane (heartbeat.py) only sees this interface, not whether it is
talking to Azure AI Foundry, a chat model or the scripted mock.

Lifecycle, mapped onto a hire in the UI:
  provision   – agent approved/hired  → Foundry: create agent version
  update      – instructions edited   → Foundry: new agent version
  deprovision – agent terminated      → Foundry: delete agent
  run         – every heartbeat       → Foundry: responses on a conversation
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from tools import Toolbox


@dataclass
class RunResult:
    summary: str
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    provider: str = ""
    # Opaque continuation handle stored per (agent, issue): a Foundry
    # conversation id, or the chat runtimes' Pydantic AI message history.
    session_ref: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class AgentRuntime(Protocol):
    name: str
    label: str

    def configured(self) -> bool: ...

    def default_model(self) -> str: ...

    async def provision(self, agent: dict) -> dict:
        """Return agent fields to store (runtime_ref, runtime_version)."""
        ...

    async def update(self, agent: dict) -> dict: ...

    async def deprovision(self, agent: dict) -> None: ...

    async def run(self, agent: dict, ctx: dict, toolbox: Toolbox, session_ref: str | None) -> RunResult: ...

    async def close(self) -> None: ...
