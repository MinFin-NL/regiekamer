import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import {
  api,
  type ActivityEvent,
  type Agent,
  type Approval,
  type Company,
  type Issue,
  type OrgNode,
  type RuntimeInfo,
  type Template,
  type Toolset,
} from '../api'

export type View = 'overzicht' | 'organogram' | 'issues' | 'documenten' | 'bestuur' | 'agent'

/** Hash routes: #/organogram, #/issues, #/issues/RK-3, #/documenten/<id>, #/bestuur, #/agents/<id>. */
function parseHash(): { view: View; param: string | null } {
  const [, first = '', second = null] = window.location.hash.replace(/^#/, '').split('/')
  if (first === 'agents' && second) return { view: 'agent', param: second }
  if (first === 'issues') return { view: 'issues', param: second }
  if (first === 'documenten') return { view: 'documenten', param: second }
  if (first === 'organogram' || first === 'bestuur') return { view: first, param: null }
  return { view: 'overzicht', param: null }
}

export const useRegie = defineStore('regie', () => {
  const company = ref<Company | null>(null)
  const agents = ref<Agent[]>([])
  const orgChart = ref<OrgNode[]>([])
  const issues = ref<Issue[]>([])
  const approvals = ref<Approval[]>([])
  const templates = ref<Template[]>([])
  const runtimes = ref<RuntimeInfo[]>([])
  const toolsets = ref<Toolset[]>([])
  const defaultRuntime = ref('mock')
  const activity = ref<ActivityEvent[]>([])
  const error = ref<string | null>(null)
  const route = ref(parseHash())
  // Bumped on every live event so open detail panes can refetch.
  const tick = ref(0)

  // Hire drawer: which manager the new agent will report to.
  const hireFor = ref<string | null | undefined>(undefined)
  const openIssueId = computed(() => (route.value.view === 'issues' ? route.value.param : null))

  window.addEventListener('hashchange', () => (route.value = parseHash()))

  const agentById = computed(() => Object.fromEntries(agents.value.map((a) => [a.id, a])))
  const pendingApprovals = computed(() => approvals.value.filter((a) => a.status === 'pending'))
  const cid = () => company.value!.id

  function navigate(hash: string) {
    window.location.hash = hash
  }

  async function refresh() {
    try {
      const [c, a, o, i, ap] = await Promise.all([
        api.get<Company>(`/api/companies/${cid()}`),
        api.get<Agent[]>(`/api/companies/${cid()}/agents`),
        api.get<OrgNode[]>(`/api/companies/${cid()}/org-chart`),
        api.get<Issue[]>(`/api/companies/${cid()}/issues`),
        api.get<Approval[]>(`/api/companies/${cid()}/approvals`),
      ])
      company.value = c
      agents.value = a
      orgChart.value = o
      issues.value = i
      approvals.value = ap
      error.value = null
    } catch (e) {
      error.value = (e as Error).message
    }
  }

  let pending: number | undefined
  function scheduleRefresh() {
    window.clearTimeout(pending)
    pending = window.setTimeout(refresh, 250)
  }

  async function boot() {
    try {
      const companies = await api.get<Company[]>('/api/companies')
      company.value = companies[0]
      const [t, r, ts, act] = await Promise.all([
        api.get<Template[]>('/api/templates'),
        api.get<{ default: string; runtimes: RuntimeInfo[] }>('/api/runtimes'),
        api.get<Toolset[]>('/api/toolsets'),
        api.get<ActivityEvent[]>(`/api/companies/${cid()}/activity?limit=60`),
      ])
      templates.value = t
      runtimes.value = r.runtimes
      toolsets.value = ts
      defaultRuntime.value = r.default
      activity.value = act
      await refresh()
      connect()
    } catch (e) {
      error.value = `Backend niet bereikbaar: ${(e as Error).message}`
    }
  }

  function connect() {
    const source = new EventSource(`/api/companies/${cid()}/events`)
    source.onmessage = (msg) => {
      const event = JSON.parse(msg.data) as ActivityEvent
      activity.value = [event, ...activity.value].slice(0, 200)
      tick.value++
      scheduleRefresh()
    }
    // EventSource reconnects by itself; catch up on anything missed meanwhile.
    source.onopen = () => scheduleRefresh()
  }

  // ── actions ──
  async function run<T>(fn: () => Promise<T>): Promise<T | undefined> {
    try {
      const result = await fn()
      error.value = null
      await refresh()
      return result
    } catch (e) {
      error.value = (e as Error).message
      return undefined
    }
  }

  return {
    company, agents, orgChart, issues, approvals, templates, runtimes, toolsets, defaultRuntime, activity, error, route,
    tick, hireFor, openIssueId, agentById, pendingApprovals,
    navigate, boot, refresh, run,
    hire: (body: Record<string, unknown>) => run(() => api.post<Agent>(`/api/companies/${cid()}/agent-hires`, body)),
    patchAgent: (id: string, body: Record<string, unknown>) => run(() => api.patch<Agent>(`/api/agents/${id}`, body)),
    agentAction: (id: string, action: 'wake' | 'pause' | 'resume' | 'terminate' | 'reprovision') =>
      run(() => api.post(`/api/agents/${id}/${action}`)),
    createIssue: (body: Record<string, unknown>) => run(() => api.post<Issue>(`/api/companies/${cid()}/issues`, body)),
    patchIssue: (id: string, body: Record<string, unknown>) => run(() => api.patch<Issue>(`/api/issues/${id}`, body)),
    comment: (id: string, body: string) => run(() => api.post(`/api/issues/${id}/comments`, { body })),
    decide: (id: string, approve: boolean, note = '') =>
      run(() => api.post(`/api/approvals/${id}/${approve ? 'approve' : 'reject'}`, { note })),
    patchCompany: (body: Record<string, unknown>) => run(() => api.patch<Company>(`/api/companies/${cid()}`, body)),
  }
})
