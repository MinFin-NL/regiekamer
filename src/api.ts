// Thin client for the Regiekamer API. Types mirror backend/db.py.

export type AgentStatus = 'pending_approval' | 'idle' | 'running' | 'paused' | 'error' | 'terminated'
export type IssueStatus = 'backlog' | 'todo' | 'in_progress' | 'in_review' | 'blocked' | 'done' | 'cancelled'

export interface Company {
  id: string
  name: string
  mission: string
  budget_monthly_cents: number
  spent_monthly_cents: number
  require_board_approval_for_new_agents: boolean
  issue_prefix: string
}

export interface Agent {
  id: string
  company_id: string
  name: string
  role: string
  title: string
  icon: string
  template: string | null
  status: AgentStatus
  reports_to: string | null
  capabilities: string
  instructions: string
  runtime: string
  model: string
  runtime_ref: string | null
  runtime_version: string | null
  toolsets: string[]
  heartbeat_interval_sec: number
  budget_monthly_cents: number
  spent_monthly_cents: number
  open_issues: number
  pause_reason: string | null
  error_reason: string | null
  last_heartbeat_at: string | null
}

export interface OrgNode extends Agent {
  reports: OrgNode[]
}

export interface AgentDetail extends Agent {
  chain_of_command: { id: string; name: string; title: string }[]
  prompt: string
}

export interface Template {
  key: string
  role: string
  title: string
  icon: string
  description: string
  capabilities: string
  instructions: string
  budget_monthly_cents: number
  toolsets: string[]
}

export interface Toolset {
  key: string
  label: string
  description: string
  online: boolean
  tools: { name: string; description: string }[]
}

export interface DocumentRef {
  id: string
  title: string
  version: number
  updated_at: string
}

export interface Document extends DocumentRef {
  body: string
  created_at: string
  issue: { id: string; identifier: string; title: string } | null
  author: { id: string; name: string; icon: string } | null
}

export interface RuntimeInfo {
  name: string
  label: string
  configured: boolean
  default_model: string
  models: string[]
}

export interface IssueRef {
  id: string
  identifier: string
  title: string
  status: IssueStatus
  assignee_agent_id: string | null
}

export interface Issue {
  id: string
  identifier: string
  title: string
  description: string
  status: IssueStatus
  priority: 'critical' | 'high' | 'medium' | 'low'
  assignee_agent_id: string | null
  assignee: { id: string; name: string; icon: string } | null
  parent_id: string | null
  checkout_run_id: string | null
  created_by_agent_id: string | null
  children: IssueRef[]
  created_at: string
  updated_at: string
}

export interface Comment {
  id: string
  body: string
  author_agent_id: string | null
  author_user: string | null
  author_name: string | null
  author_icon: string | null
  created_at: string
}

export interface RunSummary {
  id: string
  status: string
  summary: string | null
  started_at: string
  invocation_source: string
  agent_name: string
}

export interface IssueDetail extends Issue {
  comments: Comment[]
  runs: RunSummary[]
  documents: DocumentRef[]
  parent: { id: string; identifier: string; title: string } | null
}

export interface Run {
  id: string
  invocation_source: string
  trigger_detail: string | null
  issue_id: string | null
  status: string
  started_at: string
  finished_at: string | null
  summary: string | null
  error: string | null
  usage: {
    input_tokens?: number
    output_tokens?: number
    cost_cents?: number
    model?: string
    provider?: string
    tool_calls?: { tool: string; ok: boolean }[]
  } | null
}

export interface Approval {
  id: string
  type: 'hire_agent' | 'budget_override_required' | 'request_board_approval'
  status: string
  payload: Record<string, any>
  requested_by: { id: string; name: string; icon: string } | null
  subject_agent: Agent | null
  decision_note: string | null
  created_at: string
}

export interface ActivityEvent {
  id: number
  kind: string
  message: string
  agent_id: string | null
  issue_id: string | null
  run_id: string | null
  created_at: string
}

export interface CostRow {
  agent_id: string
  name: string
  icon: string
  runtime: string
  budget_monthly_cents: number
  spent_cents: number
  input_tokens: number
  output_tokens: number
  runs: number
}

async function request<T>(method: string, url: string, body?: unknown): Promise<T> {
  const res = await fetch(url, {
    method,
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      detail = (await res.json()).detail ?? detail
    } catch {
      /* not JSON */
    }
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
  }
  return res.json() as Promise<T>
}

export const api = {
  get: <T>(url: string) => request<T>('GET', url),
  post: <T>(url: string, body: unknown = {}) => request<T>('POST', url, body),
  patch: <T>(url: string, body: unknown) => request<T>('PATCH', url, body),
}

const euro = new Intl.NumberFormat('nl-NL', { style: 'currency', currency: 'EUR', minimumFractionDigits: 2 })
export const formatCents = (cents: number) => euro.format((cents || 0) / 100)

export function timeAgo(iso: string | null): string {
  if (!iso) return 'nooit'
  const sec = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000)
  if (sec < 60) return 'zojuist'
  if (sec < 3600) return `${Math.floor(sec / 60)} min geleden`
  if (sec < 86400) return `${Math.floor(sec / 3600)} uur geleden`
  return new Date(iso).toLocaleDateString('nl-NL')
}

export const STATUS_LABEL: Record<string, string> = {
  pending_approval: 'Wacht op goedkeuring',
  idle: 'Beschikbaar',
  running: 'Aan het werk',
  paused: 'Gepauzeerd',
  error: 'Fout',
  terminated: 'Uit dienst',
  backlog: 'Backlog',
  todo: 'Te doen',
  in_progress: 'Bezig',
  in_review: 'Ter review',
  blocked: 'Geblokkeerd',
  done: 'Klaar',
  cancelled: 'Geannuleerd',
}

export const STATUS_COLOR: Record<string, string> = {
  pending_approval: 'warning',
  idle: 'success',
  running: 'accent',
  paused: 'neutral',
  error: 'critical',
  terminated: 'neutral',
  todo: 'neutral',
  in_progress: 'accent',
  in_review: 'warning',
  blocked: 'critical',
  done: 'success',
  backlog: 'neutral',
  cancelled: 'neutral',
}

export const PRIORITY_LABEL: Record<string, string> = {
  critical: 'Kritiek',
  high: 'Hoog',
  medium: 'Normaal',
  low: 'Laag',
}
