"""Runtime registry. AGENT_RUNTIME_DEFAULT picks the default for new hires;
each agent stores its own runtime, so they can be mixed in one org chart
(for example the director on Foundry, a researcher on a local Ollama model)."""

import os

from runtimes.base import AgentRuntime, RunResult
from runtimes.chat import ChatRuntime, OllamaRuntime
from runtimes.foundry import FoundryRuntime
from runtimes.mock import MockRuntime

_RUNTIMES: dict[str, AgentRuntime] = {
    "foundry": FoundryRuntime(),
    "chat": ChatRuntime(),
    "ollama": OllamaRuntime(),
    "mock": MockRuntime(),
}

__all__ = ["AgentRuntime", "RunResult", "get_runtime", "list_runtimes", "default_runtime_name",
           "default_model_for", "register_runtime", "close_all"]


def get_runtime(name: str) -> AgentRuntime:
    try:
        return _RUNTIMES[name]
    except KeyError:
        raise ValueError(f"Onbekende runtime '{name}'") from None


def register_runtime(runtime: AgentRuntime) -> None:
    """Used by tests to swap in fakes."""
    _RUNTIMES[runtime.name] = runtime


async def list_runtimes() -> list[dict]:
    out = []
    for r in _RUNTIMES.values():
        models = await r.models() if r.configured() and hasattr(r, "models") else []
        out.append({"name": r.name, "label": r.label, "configured": r.configured(),
                    "default_model": r.default_model(), "models": models})
    return out


def default_runtime_name() -> str:
    explicit = os.environ.get("AGENT_RUNTIME_DEFAULT", "").strip()
    if explicit:
        return explicit
    for name in ("foundry", "chat", "ollama"):
        if _RUNTIMES[name].configured():
            return name
    return "mock"


def default_model_for(runtime: str) -> str:
    return get_runtime(runtime).default_model()


async def close_all() -> None:
    for r in _RUNTIMES.values():
        await r.close()
