<template>
  <div class="rk-page">
    <header class="rk-page__header">
      <div>
        <h1>Issues</h1>
        <p class="rk-muted">Wijs een issue toe aan een collega en die wordt direct wakker.</p>
      </div>
      <nldd-button variant="primary" :text="creating ? 'Annuleren' : 'Nieuw issue'" start-icon="file-badge-plus" @click="creating = !creating" />
    </header>

    <form v-if="creating" class="rk-panel rk-form" @submit.prevent="create">
      <label class="rk-field">
        <span>Titel</span>
        <input v-model="draft.title" required />
      </label>
      <label class="rk-field">
        <span>Omschrijving</span>
        <textarea v-model="draft.description" rows="3" />
      </label>
      <div class="rk-form__row">
        <label class="rk-field">
          <span>Prioriteit</span>
          <select v-model="draft.priority">
            <option v-for="(label, key) in PRIORITY_LABEL" :key="key" :value="key">{{ label }}</option>
          </select>
        </label>
        <label class="rk-field">
          <span>Toewijzen aan</span>
          <select v-model="draft.assignee_agent_id">
            <option :value="null">— later —</option>
            <option v-for="a in assignable" :key="a.id" :value="a.id">{{ a.icon }} {{ a.name }} — {{ a.title }}</option>
          </select>
        </label>
      </div>
      <nldd-button variant="primary" text="Aanmaken" :disabled="!draft.title.trim()" @click="create" />
    </form>

    <div class="rk-kanban">
      <section v-for="col in COLUMNS" :key="col.key" class="rk-kanban__col">
        <h2>{{ col.label }} <span class="rk-muted">{{ byColumn[col.key].length }}</span></h2>
        <button
          v-for="i in byColumn[col.key]"
          :key="i.id"
          type="button"
          class="rk-issue-card"
          :class="{ 'rk-issue-card--sub': i.parent_id }"
          @click="regie.navigate(`/issues/${i.id}`)"
        >
          <span class="rk-issue-card__top">
            <span class="rk-muted">{{ i.identifier }}</span>
            <span class="rk-chip" :class="`rk-chip--${i.priority}`">{{ PRIORITY_LABEL[i.priority] }}</span>
          </span>
          <strong>{{ i.title }}</strong>
          <span class="rk-issue-card__bottom">
            <span v-if="i.assignee">{{ i.assignee.icon }} {{ i.assignee.name }}</span>
            <span v-else class="rk-muted">Niet toegewezen</span>
            <span v-if="i.checkout_run_id" class="rk-dot rk-dot--running" title="Wordt nu aan gewerkt" />
            <span v-if="i.children.length" class="rk-muted">{{ i.children.filter((c) => c.status === 'done').length }}/{{ i.children.length }} subtaken</span>
          </span>
        </button>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { PRIORITY_LABEL, type Issue } from '../api'
import { useRegie } from '../stores/regie'

const COLUMNS = [
  { key: 'todo', label: 'Te doen', statuses: ['backlog', 'todo'] },
  { key: 'in_progress', label: 'Bezig', statuses: ['in_progress'] },
  { key: 'wait', label: 'Review / geblokkeerd', statuses: ['in_review', 'blocked'] },
  { key: 'done', label: 'Klaar', statuses: ['done', 'cancelled'] },
] as const

const regie = useRegie()
const creating = ref(false)
const draft = reactive({ title: '', description: '', priority: 'medium', assignee_agent_id: null as string | null })

const assignable = computed(() => regie.agents.filter((a) => !['pending_approval', 'terminated'].includes(a.status)))
const byColumn = computed(() => {
  const out: Record<string, Issue[]> = {}
  for (const col of COLUMNS) {
    out[col.key] = regie.issues.filter((i) => (col.statuses as readonly string[]).includes(i.status))
  }
  return out
})

async function create() {
  if (!draft.title.trim()) return
  const issue = await regie.createIssue({ ...draft, title: draft.title.trim() })
  if (issue) {
    Object.assign(draft, { title: '', description: '', priority: 'medium', assignee_agent_id: null })
    creating.value = false
  }
}
</script>
