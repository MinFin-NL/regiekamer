<template>
  <div class="rk-page">
    <section class="rk-hero">
      <div>
        <p class="rk-eyebrow">Missie</p>
        <h1>{{ c.name }}</h1>
        <p class="rk-lead">{{ c.mission }}</p>
      </div>
      <div class="rk-hero__budget">
        <p class="rk-eyebrow">Uitgaven deze maand</p>
        <p class="rk-big">{{ formatCents(c.spent_monthly_cents) }}</p>
        <BudgetMeter :spent="c.spent_monthly_cents" :budget="c.budget_monthly_cents" />
      </div>
    </section>

    <section class="rk-stats">
      <button type="button" class="rk-stat" @click="regie.navigate('/organogram')">
        <span class="rk-big">{{ activeAgents.length }}</span><span>medewerkers</span>
      </button>
      <button type="button" class="rk-stat" @click="regie.navigate('/organogram')">
        <span class="rk-big">{{ running.length }}</span><span>nu aan het werk</span>
      </button>
      <button type="button" class="rk-stat" @click="regie.navigate('/issues')">
        <span class="rk-big">{{ openIssues }}</span><span>open issues</span>
      </button>
      <button type="button" class="rk-stat" :class="{ 'rk-stat--alert': regie.pendingApprovals.length }" @click="regie.navigate('/bestuur')">
        <span class="rk-big">{{ regie.pendingApprovals.length }}</span><span>wacht op het bestuur</span>
      </button>
    </section>

    <div class="rk-columns">
      <section class="rk-panel">
        <h2>Live activiteit</h2>
        <ol class="rk-feed">
          <li v-for="e in regie.activity" :key="e.id" class="rk-feed__item" :class="`rk-feed__item--${e.kind.split('.')[0]}`">
            <span class="rk-feed__icon" aria-hidden="true">{{ e.agent_id ? regie.agentById[e.agent_id]?.icon ?? '•' : '🏛️' }}</span>
            <span class="rk-feed__text">
              <a v-if="e.issue_id" :href="`#/issues/${e.issue_id}`">{{ e.message }}</a>
              <template v-else>{{ e.message }}</template>
            </span>
            <time class="rk-muted">{{ timeAgo(e.created_at) }}</time>
          </li>
          <li v-if="!regie.activity.length" class="rk-muted">Nog niets gebeurd. Wijs een issue toe om te beginnen.</li>
        </ol>
      </section>

      <section class="rk-panel">
        <h2>Zo werkt het</h2>
        <ol class="rk-steps">
          <li><strong>Neem collega's aan</strong> vanuit het <a href="#/organogram">organogram</a>: kies een rol, een budget en een runtime.</li>
          <li><strong>Het bestuur keurt goed</strong>, en pas dan wordt de agent in Azure AI Foundry aangemaakt.</li>
          <li><strong>Wijs een issue toe.</strong> De agent wordt wakker (heartbeat), claimt het issue, werkt het uit of delegeert aan zijn team.</li>
          <li><strong>Alles is zichtbaar</strong>: opmerkingen, subtaken, kosten per heartbeat, en een automatische pauze als het budget op is.</li>
        </ol>
        <label class="rk-toggle">
          <input
            type="checkbox"
            :checked="c.require_board_approval_for_new_agents"
            @change="regie.patchCompany({ require_board_approval_for_new_agents: ($event.target as HTMLInputElement).checked })"
          />
          Nieuwe aannames vereisen goedkeuring van het bestuur
        </label>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { formatCents, timeAgo } from '../api'
import { useRegie } from '../stores/regie'
import BudgetMeter from '../components/BudgetMeter.vue'

const regie = useRegie()
const c = computed(() => regie.company!)
const activeAgents = computed(() => regie.agents.filter((a) => a.status !== 'pending_approval'))
const running = computed(() => regie.agents.filter((a) => a.status === 'running'))
const openIssues = computed(() => regie.issues.filter((i) => !['done', 'cancelled'].includes(i.status)).length)
</script>
