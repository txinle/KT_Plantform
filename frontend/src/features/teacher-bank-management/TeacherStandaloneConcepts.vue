<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import Alert from '@/design-system/components/Alert.vue'
import EmptyState from '@/design-system/components/EmptyState.vue'
import PageHeader from '@/design-system/components/PageHeader.vue'
import StatusBadge from '@/design-system/components/StatusBadge.vue'

import {
  getTeacherStandaloneQuestionBankApi,
  isStandaloneCanonicalRedirect,
  type StandaloneConcept,
} from './standalone-api'

const api = getTeacherStandaloneQuestionBankApi()
const router = useRouter()
const concepts = ref<StandaloneConcept[]>()
const error = ref('')

onMounted(async () => {
  try {
    const response = await api.concepts()
    if (isStandaloneCanonicalRedirect(response)) {
      await router.replace(response.redirect_url)
      return
    }
    concepts.value = response.items
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '知识点加载失败。'
  }
})
</script>

<template>
  <PageHeader title="知识点">
    <template #kicker>独立题库</template>
    <p>这里显示你的私有知识点和可用公共知识点。课程 KC 总库请在具体课程内维护。</p>
    <template #actions><RouterLink class="kt-button kt-button--secondary" to="/teacher/banks/">返回题库</RouterLink></template>
  </PageHeader>
  <Alert v-if="error" tone="danger">{{ error }}</Alert>
  <p v-else-if="!concepts" role="status">正在加载知识点…</p>
  <EmptyState v-else-if="!concepts.length" title="没有可用知识点">
    <p>课程 KC 会在各课程的题库中维护。</p>
  </EmptyState>
  <section v-else class="kt-card kt-table-card" aria-label="知识点列表">
    <div class="kt-table-scroll">
      <table class="kt-table">
        <thead><tr><th>知识点</th><th>路径</th><th>范围</th><th>状态</th></tr></thead>
        <tbody>
          <tr v-for="concept in concepts" :key="concept.id">
            <td><strong>{{ concept.name }}</strong><small v-if="concept.description">{{ concept.description }}</small></td>
            <td>{{ concept.path }}</td>
            <td>{{ concept.scope_label }}</td>
            <td><StatusBadge :label="concept.status_label" :tone="concept.status === 'active' ? 'success' : 'neutral'" /></td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
