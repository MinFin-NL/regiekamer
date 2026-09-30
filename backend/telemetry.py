"""OpenTelemetry tracing for agent heartbeats, exported over OTLP.

Off unless OTEL_EXPORTER_OTLP_ENDPOINT (or OTEL_EXPORTER_OTLP_TRACES_ENDPOINT)
is set; the exporter reads the other standard OTEL_* variables itself
(headers, protocol, timeout). Any OTLP backend works: an OpenTelemetry
Collector, Jaeger, Grafana Tempo, or Azure Monitor through a collector.

What ends up in a trace, per heartbeat on a chat runtime:

  heartbeat <agent>            ours: run, agent, issue, runtime ids
   └─ invoke_agent <agent>     Pydantic AI, GenAI semantic conventions
       ├─ chat <model>         one per model request, with token usage
       └─ execute_tool <name>  one per tool call

Prompts, answers and tool arguments/results are left out by default: they
carry issue text, documents and whatever a citizen wrote, and a tracing
backend is not the place for that. AGENT_TRACE_CONTENT=true includes them,
for a local debugging setup.
"""

from __future__ import annotations

import os

from opentelemetry import trace
from pydantic_ai.capabilities import Instrumentation
from pydantic_ai.models.instrumented import InstrumentationSettings

_provider = None


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def enabled() -> bool:
    return _provider is not None


def setup() -> None:
    """Install a global tracer provider with an OTLP exporter, if configured."""
    global _provider
    if _provider is not None or not (os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
                                     or os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")):
        return
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create({"service.name": os.environ.get("OTEL_SERVICE_NAME", "regiekamer-backend")})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)  # global, so later instrumentation (FastAPI, httpx) joins in
    use(provider)


def use(provider) -> None:
    """Make `provider` the one agents trace to. Tests pass one with an in-memory exporter."""
    global _provider
    _provider = provider


def shutdown() -> None:
    """Flush pending spans on app shutdown."""
    global _provider
    if _provider is not None:
        _provider.shutdown()
        _provider = None


def agent_capabilities() -> list[Instrumentation]:
    """What to pass as Agent(capabilities=…): nothing when tracing is off."""
    if _provider is None:
        return []
    return [Instrumentation(settings=InstrumentationSettings(
        tracer_provider=_provider, include_content=_flag("AGENT_TRACE_CONTENT"), include_binary_content=False))]


def tracer() -> trace.Tracer:
    return (_provider or trace.get_tracer_provider()).get_tracer("regiekamer")
