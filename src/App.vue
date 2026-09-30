<template>
  <div class="rk-app">
    <nldd-top-navigation-bar logo-title="Rijksoverheid" website-title="Regiekamer" logo-href="#/" website-href="#/">
      <nldd-menu-bar slot="global" accessible-label="Hoofdnavigatie">
        <nldd-menu-bar-item text="Overzicht" icon="house" :current="regie.route.view === 'overzicht'" @select="regie.navigate('/')" />
        <nldd-menu-bar-item
          text="Organogram"
          icon="centralized-structure"
          :current="regie.route.view === 'organogram' || regie.route.view === 'agent'"
          @select="regie.navigate('/organogram')"
        />
        <nldd-menu-bar-item text="Issues" icon="kanban-columns" :current="regie.route.view === 'issues'" @select="regie.navigate('/issues')" />
        <nldd-menu-bar-item
          text="Documenten"
          icon="text-documents"
          :current="regie.route.view === 'documenten'"
          @select="regie.navigate('/documenten')"
        />
        <nldd-menu-bar-item
          :text="regie.pendingApprovals.length ? `Bestuur (${regie.pendingApprovals.length})` : 'Bestuur'"
          icon="handshake"
          :current="regie.route.view === 'bestuur'"
          @select="regie.navigate('/bestuur')"
        />
      </nldd-menu-bar>
    </nldd-top-navigation-bar>

    <main class="rk-main">
      <nldd-banner v-if="regie.error" variant="critical" size="sm" :text="regie.error" dismissible @dismiss="regie.error = null" />

      <template v-if="regie.company">
        <DashboardView v-if="regie.route.view === 'overzicht'" />
        <OrgChartView v-else-if="regie.route.view === 'organogram'" />
        <IssuesView v-else-if="regie.route.view === 'issues'" />
        <DocumentsView v-else-if="regie.route.view === 'documenten'" />
        <BoardView v-else-if="regie.route.view === 'bestuur'" />
        <AgentView v-else-if="regie.route.view === 'agent' && regie.route.param" :key="regie.route.param" :agent-id="regie.route.param" />
      </template>
      <p v-else-if="!regie.error" class="rk-muted">Bezig met laden…</p>
    </main>

    <HireDrawer v-if="regie.hireFor !== undefined" />
    <IssueDrawer v-if="regie.openIssueId" :key="regie.openIssueId" :issue-id="regie.openIssueId" />
  </div>
</template>

<script setup lang="ts">
import { onMounted } from 'vue'
import { useRegie } from './stores/regie'
import DashboardView from './views/DashboardView.vue'
import OrgChartView from './views/OrgChartView.vue'
import IssuesView from './views/IssuesView.vue'
import DocumentsView from './views/DocumentsView.vue'
import BoardView from './views/BoardView.vue'
import AgentView from './views/AgentView.vue'
import HireDrawer from './components/HireDrawer.vue'
import IssueDrawer from './components/IssueDrawer.vue'

const regie = useRegie()
onMounted(() => regie.boot())
</script>
