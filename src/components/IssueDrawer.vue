<template>
  <div class="rk-drawer-backdrop" @click.self="close">
    <aside class="rk-drawer rk-drawer--wide" role="dialog" aria-modal="true" aria-labelledby="issue-title">
      <header class="rk-drawer__header">
        <div v-if="issue">
          <p class="rk-muted">
            {{ issue.identifier }}
            <template v-if="issue.parent">
              · subtaak van
              <a :href="`#/issues/${issue.parent.id}`">{{ issue.parent.identifier }}</a>
            </template>
          </p>
          <h2 id="issue-title">{{ issue.title }}</h2>
        </div>
        <nldd-icon-button icon="dismiss" accessible-label="Sluiten" variant="neutral-transparent" @click="close" />
      </header>

      <section v-if="issue" class="rk-drawer__body">
        <div class="rk-form__row">
          <label class="rk-field">
            <span>Toegewezen aan</span>
            <select :value="issue.assignee_agent_id ?? ''" @change="assign(($event.target as HTMLSelectElement).value)">
              <option value="">— niemand —</option>
              <option v-for="a in assignable" :key="a.id" :value="a.id">{{ a.icon }} {{ a.name }} — {{ a.title }}</option>
            </select>
          </label>
          <label class="rk-field">
            <span>Status</span>
            <select :value="issue.status" @change="setStatus(($event.target as HTMLSelectElement).value)">
              <option v-for="s in STATUSES" :key="s" :value="s">{{ STATUS_LABEL[s] }}</option>
            </select>
          </label>
        </div>
        <p v-if="issue.checkout_run_id" class="rk-working">
          <span class="rk-dot rk-dot--running" /> {{ issue.assignee?.name }} werkt hier nu aan
        </p>

        <p class="rk-description">{{ issue.description || 'Geen omschrijving.' }}</p>

        <template v-if="issue.children.length">
          <h3>Subtaken</h3>
          <ul class="rk-list">
            <li v-for="c in issue.children" :key="c.id">
              <a :href="`#/issues/${c.id}`">{{ c.identifier }}</a>
              {{ c.title }}
              <span class="rk-muted">— {{ c.assignee_agent_id ? regie.agentById[c.assignee_agent_id]?.name : 'niemand' }}</span>
              <StatusTag :status="c.status" />
            </li>
          </ul>
        </template>

        <template v-if="issue.documents.length">
          <h3>Documenten</h3>
          <ul class="rk-list">
            <li v-for="d in issue.documents" :key="d.id">
              <a :href="`#/documenten/${d.id}`">{{ d.title }}</a>
              <span class="rk-muted"> · versie {{ d.version }} · {{ timeAgo(d.updated_at) }}</span>
            </li>
          </ul>
        </template>

        <h3>Gesprek</h3>
        <ol class="rk-thread">
          <li v-for="c in issue.comments" :key="c.id" class="rk-thread__item" :class="{ 'rk-thread__item--human': !c.author_agent_id }">
            <span class="rk-thread__avatar" aria-hidden="true">{{ c.author_icon ?? '👤' }}</span>
            <div>
              <p class="rk-thread__meta">
                <strong>{{ c.author_name ?? c.author_user ?? 'Bestuur' }}</strong>
                <span class="rk-muted">{{ timeAgo(c.created_at) }}</span>
              </p>
              <p class="rk-thread__body">{{ c.body }}</p>
            </div>
          </li>
          <li v-if="!issue.comments.length" class="rk-muted">Nog geen opmerkingen.</li>
        </ol>
        <form class="rk-comment" @submit.prevent="send">
          <textarea v-model="draft" rows="2" placeholder="Schrijf als bestuur een opmerking; gebruik @Naam om iemand te wekken" />
          <nldd-button variant="secondary" text="Plaatsen" :disabled="!draft.trim()" @click="send" />
        </form>

        <template v-if="issue.runs.length">
          <h3>Heartbeats op dit issue</h3>
          <ul class="rk-list">
            <li v-for="r in issue.runs" :key="r.id">
              <StatusTag :status="r.status === 'succeeded' ? 'done' : r.status === 'running' ? 'in_progress' : 'blocked'" />
              <strong>{{ r.agent_name }}</strong>
              <span class="rk-muted">({{ r.invocation_source }}, {{ timeAgo(r.started_at) }})</span>
              {{ r.summary }}
            </li>
          </ul>
        </template>
      </section>
    </aside>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { api, STATUS_LABEL, timeAgo, type IssueDetail } from '../api'
import { useRegie } from '../stores/regie'
import StatusTag from './StatusTag.vue'

const STATUSES = ['backlog', 'todo', 'in_progress', 'in_review', 'blocked', 'done', 'cancelled']

const props = defineProps<{ issueId: string }>()
const regie = useRegie()
const issue = ref<IssueDetail | null>(null)
const draft = ref('')

const assignable = computed(() => regie.agents.filter((a) => !['pending_approval', 'terminated'].includes(a.status)))

async function load() {
  try {
    issue.value = await api.get<IssueDetail>(`/api/issues/${props.issueId}`)
  } catch (e) {
    regie.error = (e as Error).message
  }
}

onMounted(load)
watch(() => regie.tick, load)

function close() {
  regie.navigate('/issues')
}

async function assign(agentId: string) {
  await regie.patchIssue(props.issueId, { assignee_agent_id: agentId || null })
  await load()
}

async function setStatus(status: string) {
  await regie.patchIssue(props.issueId, { status })
  await load()
}

async function send() {
  if (!draft.value.trim()) return
  await regie.comment(props.issueId, draft.value.trim())
  draft.value = ''
  await load()
}
</script>
