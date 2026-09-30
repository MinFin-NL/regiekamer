<template>
  <li class="rk-org__node">
    <div class="rk-agent-card" :class="`rk-agent-card--${node.status}`">
      <button class="rk-agent-card__body" type="button" @click="regie.navigate(`/agents/${node.id}`)">
        <span class="rk-agent-card__icon" aria-hidden="true">{{ node.icon }}</span>
        <span class="rk-agent-card__text">
          <strong>{{ node.name }}</strong>
          <span class="rk-muted">{{ node.title }}</span>
        </span>
        <span class="rk-dot" :class="`rk-dot--${node.status}`" :title="STATUS_LABEL[node.status]" />
      </button>
      <div class="rk-agent-card__meta">
        <span class="rk-chip" :title="runtimeLabel">{{ node.runtime }}</span>
        <span v-if="node.open_issues" class="rk-chip">{{ node.open_issues }} open</span>
        <StatusTag v-if="node.status !== 'idle'" :status="node.status" />
      </div>
      <BudgetMeter :spent="node.spent_monthly_cents" :budget="node.budget_monthly_cents" compact />
      <button
        class="rk-agent-card__hire"
        type="button"
        :title="`Nieuwe collega onder ${node.name} aannemen`"
        :disabled="node.status === 'pending_approval'"
        @click="regie.hireFor = node.id"
      >
        + Aannemen
      </button>
    </div>
    <ul v-if="node.reports.length" class="rk-org__children">
      <OrgNode v-for="child in node.reports" :key="child.id" :node="child" />
    </ul>
  </li>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { STATUS_LABEL, type OrgNode as OrgNodeT } from '../api'
import { useRegie } from '../stores/regie'
import StatusTag from './StatusTag.vue'
import BudgetMeter from './BudgetMeter.vue'

defineOptions({ name: 'OrgNode' })
const props = defineProps<{ node: OrgNodeT }>()
const regie = useRegie()
const runtimeLabel = computed(() => regie.runtimes.find((r) => r.name === props.node.runtime)?.label ?? props.node.runtime)
</script>
