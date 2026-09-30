<template>
  <div class="rk-drawer-backdrop" @click.self="close">
    <aside class="rk-drawer" role="dialog" aria-modal="true" aria-labelledby="hire-title">
      <header class="rk-drawer__header">
        <div>
          <h2 id="hire-title">Nieuwe collega aannemen</h2>
          <p class="rk-muted">
            {{ manager ? `Rapporteert aan ${manager.name} (${manager.title})` : 'Bovenaan het organogram' }}
          </p>
        </div>
        <nldd-icon-button icon="dismiss" accessible-label="Sluiten" variant="neutral-transparent" @click="close" />
      </header>

      <!-- Stap 1: kies een rol -->
      <section v-if="!selected" class="rk-drawer__body">
        <p>Kies een rol. Alles is daarna nog aan te passen.</p>
        <div class="rk-template-grid">
          <button v-for="t in regie.templates" :key="t.key" type="button" class="rk-template" @click="choose(t)">
            <span class="rk-template__icon" aria-hidden="true">{{ t.icon }}</span>
            <strong>{{ t.title }}</strong>
            <span class="rk-muted">{{ t.description }}</span>
            <span class="rk-template__budget">{{ formatCents(t.budget_monthly_cents) }} / maand</span>
          </button>
          <button type="button" class="rk-template rk-template--blank" @click="choose(null)">
            <span class="rk-template__icon" aria-hidden="true">✨</span>
            <strong>Eigen rol</strong>
            <span class="rk-muted">Begin met een lege functieomschrijving.</span>
          </button>
        </div>
      </section>

      <!-- Stap 2: configureer -->
      <form v-else class="rk-drawer__body rk-form" @submit.prevent="submit">
        <div class="rk-form__row">
          <label class="rk-field rk-field--icon">
            <span>Icoon</span>
            <input v-model="form.icon" maxlength="4" />
          </label>
          <label class="rk-field">
            <span>Naam</span>
            <input v-model="form.name" required placeholder="Bijv. Jet de Jurist" />
          </label>
        </div>
        <label class="rk-field">
          <span>Functietitel</span>
          <input v-model="form.title" required />
        </label>
        <label class="rk-field">
          <span>Rapporteert aan</span>
          <select v-model="form.reports_to">
            <option :value="null">— niemand (top van de organisatie) —</option>
            <option v-for="a in managers" :key="a.id" :value="a.id">{{ a.icon }} {{ a.name }} — {{ a.title }}</option>
          </select>
        </label>
        <label class="rk-field">
          <span>Vaardigheden <small class="rk-muted">(zichtbaar voor collega's bij het delegeren)</small></span>
          <input v-model="form.capabilities" />
        </label>
        <label class="rk-field">
          <span>Instructies</span>
          <textarea v-model="form.instructions" rows="5" />
          <small class="rk-muted">Het vaste heartbeat-protocol en de tools worden automatisch toegevoegd.</small>
        </label>

        <ToolsetPicker v-model="form.toolsets" />

        <fieldset class="rk-field">
          <legend>Waar draait deze collega?</legend>
          <label v-for="r in regie.runtimes" :key="r.name" class="rk-radio" :class="{ 'rk-radio--off': !r.configured }">
            <input v-model="form.runtime" type="radio" :value="r.name" :disabled="!r.configured" />
            <span>
              <strong>{{ r.label }}</strong>
              <span v-if="!r.configured" class="rk-muted"> — niet geconfigureerd</span>
              <br />
              <small class="rk-muted">{{ RUNTIME_HELP[r.name] }}</small>
            </span>
          </label>
        </fieldset>

        <div class="rk-form__row">
          <label class="rk-field">
            <span>Model / deployment</span>
            <input v-model="form.model" :placeholder="runtime?.default_model" list="rk-model-options" />
            <datalist id="rk-model-options">
              <option v-for="m in runtime?.models ?? []" :key="m" :value="m" />
            </datalist>
          </label>
          <label class="rk-field">
            <span>Budget per maand (€)</span>
            <input v-model.number="form.budget_euro" type="number" min="0" step="1" />
          </label>
        </div>
        <label class="rk-field">
          <span>Heartbeat</span>
          <select v-model.number="form.heartbeat_interval_sec">
            <option :value="0">Alleen bij een gebeurtenis (toewijzing, @vermelding, besluit)</option>
            <option :value="300">Ook elke 5 minuten, als er werk ligt</option>
            <option :value="3600">Ook elk uur, als er werk ligt</option>
          </select>
        </label>

        <nldd-banner
          size="sm"
          :variant="regie.company?.require_board_approval_for_new_agents ? 'warning' : 'accent'"
          :text="
            regie.company?.require_board_approval_for_new_agents
              ? 'Het bestuur moet deze aanname eerst goedkeuren. Pas daarna wordt de agent ingericht.'
              : 'De agent wordt direct ingericht en is meteen inzetbaar.'
          "
        />

        <footer class="rk-drawer__footer">
          <nldd-button variant="neutral-transparent" text="Terug" @click="selected = false" />
          <nldd-button variant="primary" text="Aannemen" start-icon="face-smiling-badge-plus" :loading="busy" @click="submit" />
        </footer>
      </form>
    </aside>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { formatCents, type Template } from '../api'
import { useRegie } from '../stores/regie'
import ToolsetPicker from './ToolsetPicker.vue'

const RUNTIME_HELP: Record<string, string> = {
  foundry:
    'Maakt een echte agent aan in Azure AI Foundry (met instructies en tools). Zichtbaar en te evalueren in de Foundry-portal; gesprekken lopen per issue door.',
  chat: 'Roept het Azure OpenAI-model direct aan met tool-calling. Er wordt niets in Azure aangemaakt.',
  ollama: 'Draait op een lokaal model via Ollama, met dezelfde tools. Gratis en offline; kleinere modellen zijn minder betrouwbaar.',
  mock: 'Gescripte demo zonder model: delegeert en rondt af volgens een vast patroon. Kost niets.',
}

const regie = useRegie()
const selected = ref(false)
const busy = ref(false)
const form = reactive({
  template: null as string | null,
  icon: '🤖',
  name: '',
  title: '',
  reports_to: (regie.hireFor ?? null) as string | null,
  capabilities: '',
  instructions: '',
  toolsets: [] as string[],
  runtime: regie.defaultRuntime,
  model: '',
  budget_euro: 10,
  heartbeat_interval_sec: 0,
})

const manager = computed(() => (form.reports_to ? regie.agentById[form.reports_to] : null))
const managers = computed(() => regie.agents.filter((a) => a.status !== 'pending_approval'))
const runtime = computed(() => regie.runtimes.find((r) => r.name === form.runtime))
watch(() => form.runtime, () => (form.model = ''))

function choose(t: Template | null) {
  form.template = t?.key ?? null
  form.icon = t?.icon ?? '🤖'
  form.title = t?.title ?? ''
  form.capabilities = t?.capabilities ?? ''
  form.instructions = t?.instructions ?? ''
  form.toolsets = [...(t?.toolsets ?? ['documenten'])]
  form.budget_euro = (t?.budget_monthly_cents ?? 1000) / 100
  form.name = ''
  selected.value = true
}

function close() {
  regie.hireFor = undefined
}

async function submit() {
  if (busy.value || !form.name.trim() || !form.title.trim()) return
  busy.value = true
  const agent = await regie.hire({
    template: form.template,
    icon: form.icon,
    name: form.name.trim(),
    title: form.title.trim(),
    reports_to: form.reports_to,
    capabilities: form.capabilities,
    instructions: form.instructions,
    toolsets: form.toolsets,
    runtime: form.runtime,
    model: form.model || undefined,
    budget_monthly_cents: Math.round(form.budget_euro * 100),
    heartbeat_interval_sec: form.heartbeat_interval_sec,
  })
  busy.value = false
  if (agent) close()
}
</script>
