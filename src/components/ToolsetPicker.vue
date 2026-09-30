<template>
  <fieldset class="rk-field">
    <legend>Werktools</legend>
    <small class="rk-muted">Naast de vaste tools voor issues, delegeren en het bestuur.</small>
    <label v-for="t in regie.toolsets" :key="t.key" class="rk-radio">
      <input type="checkbox" :checked="modelValue.includes(t.key)" @change="toggle(t.key)" />
      <span>
        <strong>{{ t.label }}</strong>
        <span v-if="t.online" class="rk-muted"> · internet</span>
        <br />
        <small class="rk-muted">{{ t.description }}</small>
        <span class="rk-tools">
          <code v-for="tool in t.tools" :key="tool.name" class="rk-chip" :title="tool.description">{{ tool.name }}</code>
        </span>
      </span>
    </label>
  </fieldset>
</template>

<script setup lang="ts">
import { useRegie } from '../stores/regie'

const props = defineProps<{ modelValue: string[] }>()
const emit = defineEmits<{ 'update:modelValue': [value: string[]] }>()
const regie = useRegie()

function toggle(key: string) {
  const next = props.modelValue.includes(key) ? props.modelValue.filter((k) => k !== key) : [...props.modelValue, key]
  // Keep the catalog order so "changed?" comparisons stay simple.
  emit('update:modelValue', regie.toolsets.map((t) => t.key).filter((k) => next.includes(k)))
}
</script>
