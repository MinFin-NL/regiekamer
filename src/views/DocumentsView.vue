<template>
  <div class="rk-page">
    <header class="rk-page__header">
      <div>
        <h1>Documenten</h1>
        <p class="rk-muted">Nota's, analyses en concepten die collega's met de werktool Documenten hebben vastgelegd.</p>
      </div>
    </header>

    <div class="rk-docs">
      <section class="rk-panel">
        <label class="rk-field">
          <span>Zoeken</span>
          <input v-model="query" type="search" placeholder="Titel of tekst" />
        </label>
        <p v-if="!documents.length" class="rk-muted">
          {{ query ? 'Niets gevonden.' : 'Nog geen documenten. Geef een collega de werktool Documenten en wijs een issue toe.' }}
        </p>
        <ul class="rk-docs__list">
          <li v-for="d in documents" :key="d.id">
            <a :href="`#/documenten/${d.id}`" :aria-current="d.id === selectedId ? 'page' : undefined">
              <strong>{{ d.title }}</strong>
              <span class="rk-muted">
                {{ d.author?.icon }} {{ d.author?.name ?? 'Onbekend' }} · v{{ d.version }} · {{ timeAgo(d.updated_at) }}
              </span>
            </a>
          </li>
        </ul>
      </section>

      <article v-if="doc" class="rk-panel">
        <h2>{{ doc.title }}</h2>
        <p class="rk-muted">
          {{ doc.author?.icon }} {{ doc.author?.name ?? 'Onbekend' }} · versie {{ doc.version }} · bijgewerkt
          {{ timeAgo(doc.updated_at) }}
          <template v-if="doc.issue">
            · bij <a :href="`#/issues/${doc.issue.id}`">{{ doc.issue.identifier }}</a> {{ doc.issue.title }}
          </template>
        </p>
        <div class="rk-docs__body">{{ doc.body }}</div>
      </article>
      <p v-else-if="documents.length" class="rk-panel rk-muted">Kies links een document.</p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { api, timeAgo, type Document } from '../api'
import { useRegie } from '../stores/regie'

const regie = useRegie()
const documents = ref<Document[]>([])
const doc = ref<Document | null>(null)
const query = ref('')
const selectedId = computed(() => regie.route.param)

async function loadList() {
  try {
    const q = query.value.trim() ? `?q=${encodeURIComponent(query.value.trim())}` : ''
    documents.value = await api.get<Document[]>(`/api/companies/${regie.company!.id}/documents${q}`)
  } catch (e) {
    regie.error = (e as Error).message
  }
}

async function loadDoc() {
  if (!selectedId.value) {
    doc.value = null
    return
  }
  try {
    doc.value = await api.get<Document>(`/api/documents/${selectedId.value}`)
  } catch (e) {
    regie.error = (e as Error).message
  }
}

let debounce: number | undefined
watch(query, () => {
  window.clearTimeout(debounce)
  debounce = window.setTimeout(loadList, 250)
})
watch(selectedId, loadDoc)
// Agents write documents while you watch: refresh on every live event.
watch(() => regie.tick, () => Promise.all([loadList(), loadDoc()]))
onMounted(() => Promise.all([loadList(), loadDoc()]))
</script>
