<template>
  <div class="rk-page">
    <header class="rk-page__header">
      <div>
        <h1>Organogram</h1>
        <p class="rk-muted">Klik op <strong>+ Aannemen</strong> onder een collega om iemand in diens team te plaatsen.</p>
      </div>
      <nldd-button variant="primary" text="Nieuwe collega" start-icon="face-smiling-badge-plus" @click="regie.hireFor = topId" />
    </header>

    <div class="rk-org">
      <ul class="rk-org__root">
        <OrgNode v-for="n in regie.orgChart" :key="n.id" :node="n" />
      </ul>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRegie } from '../stores/regie'
import OrgNode from '../components/OrgNode.vue'

const regie = useRegie()
const topId = computed(() => regie.orgChart[0]?.id ?? null)
</script>
