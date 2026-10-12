<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'

import Alert from '@/design-system/components/Alert.vue'
import EmptyState from '@/design-system/components/EmptyState.vue'
import PageHeader from '@/design-system/components/PageHeader.vue'
import StatusBadge from '@/design-system/components/StatusBadge.vue'

import {
  getStandaloneExperimentApi,
  type StandaloneCollection,
} from './standalone-api'

const router = useRouter()
const api = getStandaloneExperimentApi()
const collection = ref<StandaloneCollection>()
const loading = ref(true)
const error = ref('')

function statusTone(status: string) {
  if (status === 'draft') return 'warning' as const
  if (status === 'archived') return 'neutral' as const
  return 'success' as const
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const response = await api.collection()
    if (response.kind === 'canonical') {
      await router.replace(response.canonical_url)
      return
    }
    collection.value = response
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '实验列表加载失败。'
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <PageHeader title="实验">
    <template #kicker>研究采集</template>
    <p>管理独立题库实验、参与学生和发布后的采集结果。</p>
    <template #actions>
      <RouterLink class="kt-button" to="/teacher/experiments/create/">新建实验</RouterLink>
    </template>
  </PageHeader>

  <Alert v-if="error" tone="danger">{{ error }}</Alert>
  <p v-if="loading" role="status">正在加载实验…</p>
  <EmptyState v-else-if="!collection?.items.length" title="尚未创建实验">
    <p>创建实验后，可添加学生并发布不可变的研究快照。</p>
    <RouterLink class="kt-button" to="/teacher/experiments/create/">新建实验</RouterLink>
  </EmptyState>
  <section v-else class="kt-card kt-table-card" aria-labelledby="standalone-experiment-list-title">
    <div class="kt-section-heading">
      <h2 id="standalone-experiment-list-title">全部实验</h2>
      <span>{{ collection.items.length }} 个</span>
    </div>
    <div class="kt-table-scroll">
      <table class="kt-table">
        <thead><tr><th>实验</th><th>题库</th><th>排序</th><th>学生</th><th>状态</th><th>更新时间</th></tr></thead>
        <tbody>
          <tr v-for="experiment in collection.items" :key="experiment.id">
            <td>
              <RouterLink :to="`/teacher/experiments/${experiment.id}/`"><strong>{{ experiment.name }}</strong></RouterLink>
              <span class="kt-cell-note">{{ experiment.description || '未填写说明' }}</span>
            </td>
            <td>{{ experiment.question_bank.name }}</td>
            <td>{{ experiment.ordering_mode_label }}</td>
            <td>{{ experiment.student_count }}</td>
            <td><StatusBadge :tone="statusTone(experiment.status)" :label="experiment.status_label" /></td>
            <td>{{ new Date(experiment.updated_at).toLocaleString('zh-CN') }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
