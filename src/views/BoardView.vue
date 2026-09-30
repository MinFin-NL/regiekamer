<template>
  <div class="rk-page">
    <header class="rk-page__header">
      <div>
        <h1>Bestuur</h1>
        <p class="rk-muted">Jij bent het bestuur. Aannames, budgetverhogingen en vragen boven het mandaat van agents komen hier binnen.</p>
      </div>
    </header>

    <p v-if="!regie.pendingApprovals.length" class="rk-panel rk-muted">Niets om te beslissen.</p>

    <article v-for="a in regie.pendingApprovals" :key="a.id" class="rk-panel rk-approval">
      <header class="rk-approval__header">
        <nldd-tag size="sm" :color="TYPE[a.type].color" :text="TYPE[a.type].label" />
        <span class="rk-muted">
          {{ a.requested_by ? `Aangevraagd door ${a.requested_by.icon} ${a.requested_by.name}` : 'Aangevraagd via de Regiekamer' }}
          · {{ timeAgo(a.created_at) }}
        </span>
      </header>

      <template v-if="a.type === 'hire_agent' && a.subject_agent">
        <h2>{{ a.subject_agent.icon }} {{ a.subject_agent.name }} — {{ a.subject_agent.title }}</h2>
        <dl class="rk-dl">
          <dt>Rapporteert aan</dt>
          <dd>{{ a.subject_agent.reports_to ? regie.agentById[a.subject_agent.reports_to]?.name : '—' }}</dd>
          <dt>Runtime</dt>
          <dd>{{ a.subject_agent.runtime }} · {{ a.subject_agent.model }}</dd>
          <dt>Budget</dt>
          <dd>{{ formatCents(a.subject_agent.budget_monthly_cents) }} per maand</dd>
          <dt v-if="a.payload.reason">Motivatie</dt>
          <dd v-if="a.payload.reason">{{ a.payload.reason }}</dd>
        </dl>
        <details>
          <summary>Instructies</summary>
          <p class="rk-description">{{ a.subject_agent.instructions }}</p>
        </details>
      </template>

      <template v-else-if="a.type === 'budget_override_required' && a.subject_agent">
        <h2>{{ a.subject_agent.icon }} {{ a.subject_agent.name }} heeft het budget bereikt</h2>
        <p>
          Uitgegeven {{ formatCents(a.payload.spent_cents) }} van {{ formatCents(a.payload.budget_cents) }}. De agent is automatisch gepauzeerd.
          Goedkeuren verhoogt het budget naar {{ formatCents(a.payload.proposed_budget_cents) }} en hervat de agent.
        </p>
      </template>

      <template v-else>
        <h2>{{ a.payload.question }}</h2>
        <p v-if="a.payload.issue_id"><a :href="`#/issues/${a.payload.issue_id}`">Bekijk het issue</a></p>
      </template>

      <label class="rk-field">
        <span>Toelichting (optioneel)</span>
        <input v-model="notes[a.id]" />
      </label>
      <footer class="rk-approval__actions">
        <nldd-button variant="primary" text="Goedkeuren" start-icon="check-mark" @click="regie.decide(a.id, true, notes[a.id])" />
        <nldd-button variant="secondary" text="Afwijzen" start-icon="dismiss" @click="regie.decide(a.id, false, notes[a.id])" />
      </footer>
    </article>
  </div>
</template>

<script setup lang="ts">
import { reactive } from 'vue'
import { formatCents, timeAgo } from '../api'
import { useRegie } from '../stores/regie'

const TYPE = {
  hire_agent: { label: 'Aanname', color: 'accent' },
  budget_override_required: { label: 'Budget', color: 'warning' },
  request_board_approval: { label: 'Besluit', color: 'paars' },
} as const

const regie = useRegie()
const notes = reactive<Record<string, string>>({})
</script>
