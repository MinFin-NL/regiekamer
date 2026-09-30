<template>
  <div v-if="agent" class="rk-page">
    <p><a href="#/organogram">← Organogram</a></p>
    <header class="rk-page__header">
      <div class="rk-agent-head">
        <span class="rk-agent-head__icon" aria-hidden="true">{{ agent.icon }}</span>
        <div>
          <h1>{{ agent.name }}</h1>
          <p class="rk-muted">
            {{ agent.title }}
            <template v-if="agent.chain_of_command.length"> · rapporteert aan {{ agent.chain_of_command.map((c) => c.name).join(' → ') }}</template>
          </p>
          <StatusTag :status="agent.status" />
        </div>
      </div>
      <div class="rk-actions">
        <nldd-button variant="primary" text="Nu wekken" start-icon="lightning" :disabled="!canWake" @click="act('wake')" />
        <nldd-button v-if="agent.status === 'paused'" variant="secondary" text="Hervatten" start-icon="media-play" @click="act('resume')" />
        <nldd-button
          v-else-if="agent.status !== 'pending_approval'"
          variant="secondary"
          text="Pauzeren"
          start-icon="media-pause"
          @click="act('pause')"
        />
        <nldd-button v-if="agent.status === 'error'" variant="secondary" text="Opnieuw inrichten" start-icon="arrow-clockwise" @click="act('reprovision')" />
        <nldd-button v-if="agent.reports_to" variant="neutral-transparent" text="Uit dienst" start-icon="dismiss" @click="terminate" />
      </div>
    </header>

    <nldd-banner v-if="agent.error_reason" variant="critical" size="sm" :text="agent.error_reason" />
    <nldd-banner v-if="agent.pause_reason && agent.status === 'paused'" variant="warning" size="sm" :text="agent.pause_reason" />

    <div class="rk-columns">
      <section class="rk-panel">
        <h2>Runtime</h2>
        <dl class="rk-dl">
          <dt>Runtime</dt>
          <dd>{{ runtimeLabel }}</dd>
          <dt>Model</dt>
          <dd>{{ agent.model }}</dd>
          <template v-if="agent.runtime_ref">
            <dt>{{ agent.runtime === 'foundry' ? 'Foundry-agent' : 'Referentie' }}</dt>
            <dd><code>{{ agent.runtime_ref }}</code><template v-if="agent.runtime_version"> · versie {{ agent.runtime_version }}</template></dd>
          </template>
          <dt>Heartbeat</dt>
          <dd>{{ agent.heartbeat_interval_sec ? `elke ${agent.heartbeat_interval_sec / 60} min (als er werk ligt)` : 'alleen bij gebeurtenissen' }}</dd>
          <dt>Laatste heartbeat</dt>
          <dd>{{ timeAgo(agent.last_heartbeat_at) }}</dd>
          <dt>Budget</dt>
          <dd><BudgetMeter :spent="agent.spent_monthly_cents" :budget="agent.budget_monthly_cents" /></dd>
        </dl>

        <h2>Werktools</h2>
        <ToolsetPicker v-model="toolsets" />
        <nldd-button variant="secondary" text="Werktools opslaan" :disabled="!toolsetsChanged" @click="saveToolsets" />

        <h2>Instructies</h2>
        <textarea v-model="instructions" rows="6" class="rk-textarea" />
        <p class="rk-muted">
          {{ agent.runtime === 'foundry' ? 'Opslaan publiceert een nieuwe versie van de Foundry-agent.' : 'Geldt vanaf de volgende heartbeat.' }}
        </p>
        <nldd-button variant="secondary" text="Instructies opslaan" :disabled="instructions === agent.instructions" @click="save" />
        <details>
          <summary>Volledige prompt zoals de agent die krijgt</summary>
          <pre class="rk-pre">{{ agent.prompt }}</pre>
        </details>
      </section>

      <section class="rk-panel">
        <h2>Heartbeats</h2>
        <p v-if="!runs.length" class="rk-muted">Nog geen heartbeats. Wijs een issue toe of klik op ‘Nu wekken’.</p>
        <ol class="rk-runs">
          <li v-for="r in runs" :key="r.id" class="rk-run">
            <p class="rk-run__meta">
              <StatusTag :status="RUN_STATUS[r.status] ?? 'blocked'" />
              <span>{{ r.invocation_source }}</span>
              <span class="rk-muted">{{ timeAgo(r.started_at) }}</span>
              <span v-if="r.usage?.cost_cents !== undefined" class="rk-muted">
                · {{ (r.usage.input_tokens ?? 0) + (r.usage.output_tokens ?? 0) }} tokens · {{ formatCents(r.usage.cost_cents) }}
              </span>
            </p>
            <p v-if="r.trigger_detail" class="rk-muted">{{ r.trigger_detail }}</p>
            <p>{{ r.summary ?? r.error }}</p>
            <p v-if="r.usage?.tool_calls?.length" class="rk-tools">
              <span v-for="(t, idx) in r.usage.tool_calls" :key="idx" class="rk-chip" :class="{ 'rk-chip--critical': !t.ok }">{{ t.tool }}</span>
            </p>
          </li>
        </ol>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { api, formatCents, timeAgo, type AgentDetail, type Run } from '../api'
import { useRegie } from '../stores/regie'
import StatusTag from '../components/StatusTag.vue'
import BudgetMeter from '../components/BudgetMeter.vue'
import ToolsetPicker from '../components/ToolsetPicker.vue'

const RUN_STATUS: Record<string, string> = {
  succeeded: 'done',
  running: 'in_progress',
  queued: 'todo',
  cancelled: 'cancelled',
  failed: 'blocked',
  timed_out: 'blocked',
  interrupted: 'blocked',
}

const props = defineProps<{ agentId: string }>()
const regie = useRegie()
const agent = ref<AgentDetail | null>(null)
const runs = ref<Run[]>([])
const instructions = ref('')
const toolsets = ref<string[]>([])
const toolsetsChanged = computed(() => agent.value && toolsets.value.join() !== agent.value.toolsets.join())

const canWake = computed(() => agent.value && ['idle', 'error'].includes(agent.value.status))
const runtimeLabel = computed(() => regie.runtimes.find((r) => r.name === agent.value?.runtime)?.label ?? agent.value?.runtime)

async function load() {
  try {
    const [a, r] = await Promise.all([
      api.get<AgentDetail>(`/api/agents/${props.agentId}`),
      api.get<Run[]>(`/api/agents/${props.agentId}/runs`),
    ])
    const firstLoad = !agent.value
    const unchanged = agent.value && instructions.value === agent.value.instructions
    const toolsetsUnchanged = !toolsetsChanged.value
    agent.value = a
    runs.value = r
    if (firstLoad || unchanged) instructions.value = a.instructions
    if (firstLoad || toolsetsUnchanged) toolsets.value = [...a.toolsets]
  } catch (e) {
    regie.error = (e as Error).message
  }
}

onMounted(load)
watch(() => regie.tick, load)

async function act(action: 'wake' | 'pause' | 'resume' | 'reprovision') {
  await regie.agentAction(props.agentId, action)
  await load()
}

async function save() {
  await regie.patchAgent(props.agentId, { instructions: instructions.value })
  await load()
}

async function saveToolsets() {
  await regie.patchAgent(props.agentId, { toolsets: toolsets.value })
  await load()
  if (agent.value) toolsets.value = [...agent.value.toolsets]
}

async function terminate() {
  if (!agent.value || !window.confirm(`${agent.value.name} uit dienst nemen? Open werk gaat terug naar de leidinggevende.`)) return
  await regie.agentAction(props.agentId, 'terminate')
  regie.navigate('/organogram')
}
</script>
