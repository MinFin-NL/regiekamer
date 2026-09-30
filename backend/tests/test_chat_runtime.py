"""The chat runtime, driven by a scripted FunctionModel instead of an LLM."""

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import db
import org
import telemetry
from pydantic_ai.messages import ModelMessage, ModelResponse, RetryPromptPart, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from runtimes.chat import MAX_TOOL_ROUNDS, ChatRuntime, _load_history
from tools import Toolbox


def runtime_for(script) -> ChatRuntime:
    class Scripted(ChatRuntime):
        def _model(self, model_name):
            return FunctionModel(script)

    return Scripted()


def ctx_for(issue: dict) -> dict:
    return {"task_markdown": f"Werk {issue['identifier']} af.", "focus_issue": {"identifier": issue["identifier"]}}


def does_the_work(ref: str):
    """Claim, comment, close, then answer — one step per model call."""
    steps = [
        ("checkout_issue", {"issue": ref}),
        ("add_comment", {"issue": ref, "body": "Uitgezocht: het antwoord is 42."}),
        ("update_issue_status", {"issue": ref, "status": "done"}),
    ]

    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        done = sum(isinstance(p, ToolCallPart) for m in messages if isinstance(m, ModelResponse) for p in m.parts)
        if done < len(steps):
            name, args = steps[done]
            return ModelResponse(parts=[ToolCallPart(name, args)])
        return ModelResponse(parts=[TextPart(f"{ref} is afgerond.")])

    return script


async def test_tools_close_the_issue_and_history_round_trips(company, ceo):
    issue = org.create_issue(company["id"], {"title": "Vraag", "assignee_agent_id": ceo["id"]})
    toolbox = Toolbox(ceo, "run-1")

    result = await runtime_for(does_the_work(issue["identifier"])).run(ceo, ctx_for(issue), toolbox, None)

    assert db.get("issues", issue["id"])["status"] == "done"
    assert [c["tool"] for c in toolbox.calls] == ["checkout_issue", "add_comment", "update_issue_status"]
    assert result.summary == f"{issue['identifier']} is afgerond."
    assert result.input_tokens > 0 and result.output_tokens > 0
    history = _load_history(result.session_ref)
    assert len(history) == 2  # prompt + final answer; tool chatter is dropped


async def test_text_only_answer_gets_nudged_back_to_the_tools(company, ceo):
    issue = org.create_issue(company["id"], {"title": "Vraag", "assignee_agent_id": ceo["id"]})
    work = does_the_work(issue["identifier"])
    nudged = []

    def script(messages, info):
        retries = [p for m in messages for p in getattr(m, "parts", []) if isinstance(p, RetryPromptPart)]
        if not retries:
            return ModelResponse(parts=[TextPart("Ik ga hier morgen mee aan de slag.")])
        nudged.append(retries[0].content)
        return work([m for m in messages if not any(isinstance(p, TextPart) for p in getattr(m, "parts", []))], info)

    await runtime_for(script).run(ceo, ctx_for(issue), Toolbox(ceo, "run-1"), None)

    assert issue["identifier"] in nudged[0]
    assert db.get("issues", issue["id"])["status"] == "done"


async def test_stubborn_text_answer_is_salvaged_for_review(company, ceo):
    issue = org.create_issue(company["id"], {"title": "Vraag", "assignee_agent_id": ceo["id"]})
    answer = "Het antwoord is 42, want dat staat in artikel 1:3 van de Awb, lid 2."
    thinking = "<think>eens kijken</think>"

    result = await runtime_for(lambda m, i: ModelResponse(parts=[TextPart(thinking + answer)])).run(
        ceo, ctx_for(issue), Toolbox(ceo, "run-1"), None)

    assert result.summary == answer
    assert db.get("issues", issue["id"])["status"] == "in_review"
    comments = db.rows("SELECT body FROM comments WHERE issue_id = ?", issue["id"])
    assert comments[-1]["body"].startswith("(Automatisch vastgelegd") and answer in comments[-1]["body"]


def test_history_from_the_old_chat_loop_is_dropped():
    assert _load_history('[{"role": "user", "content": "Werk RK-1 af."}]') == []


async def test_endless_tool_calls_stop_at_the_round_limit(company, ceo):
    issue = org.create_issue(company["id"], {"title": "Vraag", "assignee_agent_id": ceo["id"]})
    loop = lambda m, i: ModelResponse(parts=[ToolCallPart("get_issue", {"issue": issue["identifier"]})])  # noqa: E731

    result = await runtime_for(loop).run(ceo, ctx_for(issue), Toolbox(ceo, "run-1"), None)

    assert result.summary == f"Gestopt na {MAX_TOOL_ROUNDS} toolrondes."
    assert result.input_tokens > 0


@pytest.fixture
def spans():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    telemetry.use(provider)
    yield exporter
    telemetry.shutdown()


def all_attribute_text(finished) -> str:
    return " ".join(str(v) for span in finished for v in span.attributes.values())


async def test_heartbeat_is_traced_without_content(company, ceo, spans):
    issue = org.create_issue(company["id"], {"title": "Vraag", "assignee_agent_id": ceo["id"]})

    await runtime_for(does_the_work(issue["identifier"])).run(ceo, ctx_for(issue), Toolbox(ceo, "run-7"), None)

    finished = spans.get_finished_spans()
    heartbeat = next(s for s in finished if s.name.startswith("heartbeat "))
    assert heartbeat.attributes["regiekamer.run_id"] == "run-7"
    assert heartbeat.attributes["regiekamer.issue"] == issue["identifier"]
    assert heartbeat.attributes["regiekamer.tool_calls"] == 3
    children = [s for s in finished if s.parent and s.parent.span_id == heartbeat.context.span_id]
    assert [s.name for s in children] == [f"invoke_agent {ceo['name']}"]
    tools = sorted(s.name for s in finished if s.name.startswith("execute_tool"))
    assert tools == ["execute_tool add_comment", "execute_tool checkout_issue", "execute_tool update_issue_status"]
    text = all_attribute_text(finished)
    assert "het antwoord is 42" not in text  # tool arguments
    assert ctx_for(issue)["task_markdown"] not in text  # the prompt


async def test_trace_content_is_opt_in(company, ceo, spans, monkeypatch):
    monkeypatch.setenv("AGENT_TRACE_CONTENT", "true")
    issue = org.create_issue(company["id"], {"title": "Vraag", "assignee_agent_id": ceo["id"]})

    await runtime_for(does_the_work(issue["identifier"])).run(ceo, ctx_for(issue), Toolbox(ceo, "run-8"), None)

    assert "het antwoord is 42" in all_attribute_text(spans.get_finished_spans())
