<template>
  <nldd-tag size="sm" :color="STATUS_COLOR[status] ?? 'neutral'" :text="STATUS_LABEL[status] ?? status" />
</template>

<script setup lang="ts">
import { STATUS_COLOR, STATUS_LABEL } from '../api'

defineProps<{ status: string }>()
</script>
