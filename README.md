# Regiekamer

A small demo of an AI organisation. You build an org chart of AI colleagues, assign them issues and watch them work: they wake up, claim an issue, work on it or delegate to their team, and book the cost against their own budget. You are the **board**: you approve hires, raise budgets and decide whatever sits above an agent's mandate. The organisation model (heartbeats, checkouts, board approvals) is borrowed from [Paperclip](https://github.com/paperclipai/paperclip) (MIT).

The stack and deployment match invulhulp: FastAPI + Vue/NLDD, two containers on Azure Container Apps, Azure DevOps Pipelines. The agents run on **Azure AI Foundry Agent Service**, on an Azure OpenAI chat model or a local Ollama model (both driven by [Pydantic AI](https://github.com/pydantic/pydantic-ai), MIT), or on a scripted mock that needs no model at all.

## Concepts

| Concept | In the Regiekamer |
|---|---|
| Company | The organisation, with a mission, a monthly budget and an issue prefix (`RK`) |
| Agent | A colleague with a role, instructions, a manager (`reports_to`), a runtime, a model and a monthly budget |
| Board | You. Approvals come in on the *Bestuur* page |
| Issue | A task with status, priority, assignee, subtasks and a comment thread |
| Heartbeat | One work cycle of an agent: wake up → do work → back to sleep |
| Checkout | An exclusive claim on an issue for one heartbeat, so two runs never work the same issue |
| Cost event | Tokens × price per heartbeat. At 80% of budget there is a warning; at 100% the agent is auto-paused |

## What happens when you hire a colleague ("click and play")

```
UI: "+ Aannemen" on an org chart node
 └─ template gallery (Directeur, Onderzoeker, Jurist, …) → pre-filled form
     └─ POST /api/companies/{cid}/agent-hires
         ├─ board approval on? → status pending_approval + approval "hire_agent"
         │                        └─ board approves → activate
         └─ otherwise           → activate
activate = runtime.provision(agent)
         foundry: project.agents.create_version("rk-<name>-<id>",
                    PromptAgentDefinition(model, instructions = role + heartbeat protocol,
                                          tools = FunctionTools))
         chat/ollama/mock: nothing to create
```

**Instructions vs context.** The static part (role, heartbeat protocol, tool schemas) is baked into the Foundry agent version. Everything that changes during the agent's life travels with each heartbeat as the wake message: who your team is, what is in your inbox, why you were woken. Hiring a colleague therefore doesn't require a new version of the manager. Editing the instructions *does* publish a new version; Foundry keeps the version history. *Uit dienst* (terminate) deletes the Foundry agent.

**A heartbeat** (`backend/heartbeat.py`) is triggered by an assignment, an @mention, a finished subtask, a board decision, *Nu wekken*, or optionally a timer (only when there is work). For each agent there is at most one run at a time, and wakes that arrive in the meantime are merged. Each run follows these steps:

1. The budget check comes first. If the budget is used up, the agent is paused and a `budget_override_required` approval is created.
2. The wake context is built: identity, manager, team, inbox, focus issue, and a ready-made prompt.
3. `runtime.run()` starts. Context carries over between heartbeats in **one conversation per (agent, issue)**. Foundry uses the Responses API on the project endpoint with `agent_reference`: `function_call` items are executed against our tools and sent back as `function_call_output` until the agent answers in text. The chat and Ollama runtimes hand the same tools to a Pydantic AI agent, which runs the loop, and store the prompt and final answer of each turn as the conversation history.
4. The token usage becomes a cost event and the checkouts of this run are released.

**The coordination tools are client-side function tools** (`backend/tools.py`): `list_my_issues`, `get_issue`, `checkout_issue`, `add_comment`, `update_issue_status`, `create_subtask`, `get_org_chart`, `request_hire` and `request_board_approval`. We deliberately don't give Foundry direct write access to our data. Every mutation goes through the same rules the UI uses:
- a checkout is atomic
- you can only delegate within your own team
- `done` requires all subtasks to be closed
- a hire requested by an agent always goes to the board

**Work tools** (`backend/worktools.py`) come on top of those, per agent. Each template has defaults (the jurist gets documents + law, the researcher documents + web + open data). You can change them in the hire form and on the agent page. On Foundry, a change publishes a new agent version.

| Toolset | Tools | Source |
|---|---|---|
| `documenten` | `write_document`, `read_document`, `list_documents` | Our database. Versioned by title, optionally linked to an issue; readable under *Documenten* and on the issue |
| `wetten` | `search_law`, `get_law_article` | wetten.overheid.nl: KOOP's SRU search service plus the consolidated BWB XML (current version, cached in memory) |
| `web` | `web_search`, `fetch_url` | Brave Search API if `BRAVE_SEARCH_API_KEY` is set, otherwise DuckDuckGo's HTML page. `fetch_url` refuses anything that is not a public internet address (localhost, LAN, the cloud metadata endpoint), also after redirects |
| `opendata` | `search_datasets`, `get_dataset` | The CKAN API of data.overheid.nl |

Calling a tool outside your own toolsets, an unreachable source, or a wrong argument all come back to the model as an `error` it can react to. In a test, `qwen3:8b` answered a question about Awb article 1:3 on its own in about 2.5 minutes: `search_law` → `get_law_article` → `write_document` → `add_comment` → `done`.

**Why this split.** On Foundry, the agents, versions, conversations, traces and evaluations all live in the Foundry portal. The Regiekamer is the control plane: org chart, work, budget and governance. Agents only change that state through our tools, which run in-process.

### Runtimes (`backend/runtimes/`)

| Runtime | When | Auth |
|---|---|---|
| `foundry` | `FOUNDRY_PROJECT_ENDPOINT` is set | Entra ID only: managed identity on Azure, `az login` locally. Role **Azure AI User** on the project |
| `chat` | `AZURE_OPENAI_ENDPOINT` is set | API key, same as invulhulp. Pydantic AI runs the tool loop |
| `ollama` | `OLLAMA_BASE_URL` is set | none. A local model with the same Pydantic AI loop as `chat`; costs €0 |
| `mock` | always | none. A scripted flow: managers delegate, leaf agents "work" and close the issue |

The `chat` and `ollama` runtimes emit OpenTelemetry traces (`backend/telemetry.py`) when `OTEL_EXPORTER_OTLP_ENDPOINT` is set: a `heartbeat` span with the run, agent and issue ids, and under it Pydantic AI's spans for every model request and tool call, with token usage, in the OTel GenAI conventions. Any OTLP backend works. Prompts, answers and tool arguments are left out unless `AGENT_TRACE_CONTENT=true`; the spans then only show which tools ran, in what order, and how long they took.

`AGENT_RUNTIME_DEFAULT` sets the runtime for new hires; without it, the first configured one of `foundry`, `chat` and `ollama` wins, then `mock`. Each agent keeps its own runtime, so you can mix them in one org chart. Agents hired on the old `chat-pai` and `ollama-pai` runtimes move to `chat` and `ollama` when the backend starts.

## Running locally

```bash
uv sync
cd backend && uv run uvicorn main:app --reload        # :8000, SQLite in data/
npm install && npm run dev                             # :5173, proxies /api
```

If port 8000 is taken, set `API_PROXY_TARGET=http://localhost:<port>` for `npm run dev`. Without any configuration, everything runs on the `mock` runtime. The seed creates one organisation, a director and three unassigned issues. A demo flow:

1. Organogram → *+ Aannemen* under the director → pick a role.
2. Bestuur → approve the hire.
3. Issues → open an issue → assign it to the director. The director delegates to the new colleague, who finishes the subtask, and the director then closes the issue. The live feed on *Overzicht* shows every step.

### Local models with Ollama

The `ollama` runtime runs the same tool loop against a local model, so the whole demo works offline and for free. You need a model that supports tool calling:

| Model | Result in the delegation flow |
|---|---|
| `mistral-small3.1:24b` | Good: delegates, comments, sets statuses. About 1–3 min per heartbeat on a MacBook |
| `qwen2.5:3b` | Fast (seconds), but thin output and weak Dutch |
| `qwen3:8b` (default) | Answered the Awb question above on its own in about 2.5 minutes. `ollama pull qwen3:8b` |

Three ways to run it:

```bash
# 1. Ollama installed natively (fastest on a Mac: uses the GPU), backend with uv
OLLAMA_BASE_URL=http://localhost:11434 OLLAMA_MODEL=qwen3:8b uv run uvicorn main:app --reload

# 2. Native Ollama, app in Docker: put this in .env, then `docker compose up --build`
OLLAMA_BASE_URL=http://host.docker.internal:11434

# 3. Everything in Docker, including Ollama (pulls the model on first start; CPU-only on a Mac)
docker compose -f docker-compose.yml -f docker-compose.ollama.yml up --build
```

When `OLLAMA_BASE_URL` is set, new hires go to Ollama by default. The hire form shows the installed models, and hiring with a model that isn't pulled fails right away with a message saying which `ollama pull` to run. You can mix runtimes, for example the director on Foundry and the researchers local.

Small models often stop halfway ("I'll pick this up next") or answer in plain text instead of through the tools. An output validator on the Pydantic AI agent catches that: if an agent claimed an issue, or was woken for one, and recorded nothing, it gets up to two short nudges (`ModelRetry`) to finish in the same heartbeat. Ollama ignores `tool_choice="required"`, so if the model still answers in plain text after that, the runtime posts that answer on the issue itself, marked *Automatisch vastgelegd*, and sets the issue to `in_review` for a human to check. The tools also accept common wrong argument names (`issue_id`, `parent`, `#rk3`) and answer mistakes with the expected parameters, so the model can correct itself.

**Reliability:** local models vary from run to run. In testing, the same model sometimes delegated neatly and sometimes claimed to have made tool calls it never made. Use local mode to try things out for free and offline. For a demo that must work, use the `mock` runtime (always works) or Foundry.

Ollama's default context window is small. Tool schemas and org context take about 4k tokens before any work starts, so start a native Ollama with `OLLAMA_CONTEXT_LENGTH=16384` (the Docker overlay sets this itself).

With Docker: `docker compose up --build` → http://localhost:8080. For Foundry locally, set `FOUNDRY_PROJECT_ENDPOINT` in `.env` and run `az login`; compose mounts `~/.azure` read-only into the backend.

Tests: `uv run pytest`. They cover hiring with and without approval, checkout conflicts, delegation scope, budget pause and override, merging of wakes, the work tools against mocked HTTP, the Foundry function-call loop against a fake client, and the chat runtime against a scripted Pydantic AI `FunctionModel` (nudges, salvage, round limit, tracing).

## Deployment (same environment as invulhulp)

The Regiekamer runs next to invulhulp: same resource group (`rg-invulhulp-inno-d`), same ACR, same Container Apps environment (`cae-invulhulp-inno-d`), same firewall rules and the same Azure OpenAI model.

- **`azure-pipelines.yml`** builds `regiekamer-backend` and `regiekamer-frontend` into the ACR of `rg-invulhulp-inno-d` and deploys two Container Apps into `cae-invulhulp-inno-d`:
  - `ca-regiekamer-backend-inno-d` is internal and has 1 replica. It gets a `/data` Azure Files mount from its own storage account (`stregiekamerinnod`, share and mount `regiekamer-data`), so invulhulp's data is never touched. New hires default to the `chat` runtime on `gpt-5.3-chat` (API version `2025-04-01-preview`), the same deployment invulhulp uses;
  - `ca-regiekamer-frontend-inno-d` is external, behind the same allowlist as invulhulp: the rules *DWR Next werkplekken* and *ITS*, with IPs from invulhulp's variable group.

Setup, once:

1. **Variable group `invulhulp-secrets`** (already exists): the pipeline reuses it for `AZURE_SERVICE_CONNECTION`, `ALLOWED_IP_1`, `ALLOWED_IP_2`, `AZURE_OPENAI_ENDPOINT` and `AZURE_OPENAI_API_KEY`. Authorize this pipeline to use it.
2. Register `azure-pipelines.yml` as a pipeline in Azure DevOps and run it on `main`.

Foundry is still supported in code, but this deployment doesn't configure it, because invulhulp has no Foundry project. To use it, add `FOUNDRY_PROJECT_ENDPOINT` to the backend and give its managed identity *Azure AI User* on the project.
## Known limitations of this demo

- **Not tested against a real Foundry project yet.** The Foundry runtime follows `azure-ai-projects` 2.7 (the GA "v1" API) and is tested against a fake client. After the first deploy, check the following:
  - hiring creates an agent `rk-…` in the portal
  - *Nu wekken* produces a run with tokens
  - the conversation is visible in the portal
- **Online work tools need outbound internet** from the backend container. On a network without it, only `documenten` works.
- **No login.** The IP allowlist is the only gate. Keycloak can be added the same way as in invulhulp (the BFF pattern in `auth.py`).
- **SQLite on Azure Files** with exactly one backend replica. Sufficient for a demo; for anything more, move to Postgres.
- **Prices** in `backend/pricing.py` are demo defaults (euro cents per 1M tokens) and can be overridden per model via env.
- **ACR pull uses admin credentials**, as in invulhulp; managed-identity pull is the logical next step.
