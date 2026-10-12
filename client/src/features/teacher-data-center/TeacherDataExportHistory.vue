<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import Alert from '@/design-system/components/Alert.vue'
import EmptyState from '@/design-system/components/EmptyState.vue'
import MobileCardList from '@/design-system/components/MobileCardList.vue'
import PageHeader from '@/design-system/components/PageHeader.vue'
import Paginator from '@/design-system/components/Paginator.vue'
import StatusBadge from '@/design-system/components/StatusBadge.vue'

import { getTeacherDataCenterApi, routeQuery, type DataCenterExportHistoryCollection } from './api'

const route = useRoute()
const router = useRouter()
const courseId = Number(route.params.courseId)
const api = getTeacherDataCenterApi()
const data = ref<DataCenterExportHistoryCollection>()
const error = ref('')
const removingJobId = ref('')
const currentPage = computed(() => Number(route.query.page || 1))
let latestRequest = 0

function tone(status: string) {
  if (status === 'succeeded') return 'success' as const
  if (status === 'failed' || status === 'invalidated') return 'danger' as const
  return 'warning' as const
}

async function load(page = currentPage.value) {
  const request = ++latestRequest
  error.value = ''
  data.value = undefined
  try {
    const result = await api.history(courseId, { ...routeQuery(route.query), page })
    if (request === latestRequest) data.value = result
  } catch (caught) {
    if (request === latestRequest) error.value = caught instanceof Error ? caught.message : '导出记录加载失败。'
  }
}

async function navigate(page: number) {
  const query = { ...route.query, page: page === 1 ? undefined : String(page) }
  await router.push({ query })
  await load(page)
}

async function removeArtifact(job: DataCenterExportHistoryCollection['items'][number]) {
  if (!job.delete_url) return
  removingJobId.value = job.job_id
  error.value = ''
  try {
    await api.removeExport(job.delete_url)
    await load(currentPage.value)
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '导出文件移除失败，请刷新后重试。'
  } finally {
    removingJobId.value = ''
  }
}

onMounted(() => load())
</script>

<template>
  <PageHeader title="导出记录">
    <template #kicker>实验数据导出历史</template>
    <p>查看当前实验、班级、学生和作答会话范围下的导出记录。文件移除后，导出范围和操作记录仍会保留。</p>
  </PageHeader>
  <Alert v-if="error" tone="danger">{{ error }}</Alert>
  <p v-if="!data && !error" role="status">正在加载导出记录…</p>
  <EmptyState v-else-if="data && !data.items.length" title="当前范围尚无导出记录" />
  <div v-else-if="data" class="kt-card kt-table-card">
    <div class="kt-table-scroll kt-desktop-only">
      <table class="kt-table">
        <thead><tr><th>数据包</th><th>实验</th><th>班级</th><th>状态</th><th>创建者</th><th>创建时间</th><th>操作</th></tr></thead>
        <tbody><tr v-for="job in data.items" :key="job.job_id"><td>{{ job.package_label }}</td><td>{{ job.experiment_name || '未记录实验' }}</td><td>{{ job.class_name }}</td><td><StatusBadge :label="job.status_label" :tone="tone(job.status)" /><span v-if="job.artifact_removal_label" class="kt-cell-note">{{ job.artifact_removal_label }}</span></td><td>{{ job.created_by_name }}</td><td>{{ new Date(job.created_at).toLocaleString('zh-CN') }}</td><td><div class="kt-inline-actions"><a v-if="job.download_url" :href="job.download_url">下载</a><details v-if="job.delete_url"><summary class="kt-danger-summary">移除文件</summary><div class="kt-inline-panel kt-inline-panel--danger"><p>只移除导出文件，导出范围和操作记录继续保留。</p><button class="kt-button kt-button--danger" type="button" :disabled="removingJobId === job.job_id" @click="removeArtifact(job)">{{ removingJobId === job.job_id ? '正在移除…' : '确认移除' }}</button></div></details></div></td></tr></tbody>
      </table>
    </div>
    <MobileCardList class="kt-mobile-only">
      <li v-for="job in data.items" :key="job.job_id" class="kt-mobile-data-card">
        <div class="kt-section-heading"><strong>{{ job.package_label }}</strong><StatusBadge :label="job.status_label" :tone="tone(job.status)" /></div>
        <p>{{ job.experiment_name || '未记录实验' }} · {{ job.class_name }} · {{ job.created_by_name }}</p>
        <p v-if="job.artifact_removal_label">{{ job.artifact_removal_label }}</p><small>{{ new Date(job.created_at).toLocaleString('zh-CN') }}</small>
        <div class="kt-inline-actions"><a v-if="job.download_url" class="kt-button kt-button--secondary" :href="job.download_url">下载</a><details v-if="job.delete_url"><summary class="kt-danger-summary">移除文件</summary><div class="kt-inline-panel kt-inline-panel--danger"><button class="kt-button kt-button--danger" type="button" :disabled="removingJobId === job.job_id" @click="removeArtifact(job)">{{ removingJobId === job.job_id ? '正在移除…' : '确认移除' }}</button></div></details></div>
      </li>
    </MobileCardList>
    <Paginator :page="data.page.number" :total-pages="data.page.total_pages" @navigate="navigate" />
  </div>
</template>
