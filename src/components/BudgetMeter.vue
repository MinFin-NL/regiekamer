<template>
  <div class="rk-budget" :title="`${formatCents(spent)} van ${formatCents(budget)} deze maand`">
    <div class="rk-budget__bar">
      <div class="rk-budget__fill" :class="`rk-budget__fill--${level}`" :style="{ width: `${Math.min(100, pct)}%` }" />
    </div>
    <span v-if="!compact" class="rk-budget__label">{{ formatCents(spent) }} / {{ budget > 0 ? formatCents(budget) : '∞' }}</span>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { formatCents } from '../api'

const props = defineProps<{ spent: number; budget: number; compact?: boolean }>()
const pct = computed(() => (props.budget > 0 ? (props.spent / props.budget) * 100 : 0))
const level = computed(() => (pct.value >= 100 ? 'critical' : pct.value >= 80 ? 'warning' : 'ok'))
</script>
