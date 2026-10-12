<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import Alert from '@/design-system/components/Alert.vue'
import PageHeader from '@/design-system/components/PageHeader.vue'

import { getTeacherDataCenterApi, routeQuery, type DataCenterExportCatalog, type DataCenterExportJobMutation } from './api'

const route = useRoute()
const courseId = Number(route.params.courseId)
const api = getTeacherDataCenterApi()
const data = ref<DataCenterExportCatalog>()
const error = ref('')
const creating = ref(false)
const createdJob = ref<DataCenterExportJobMutation>()
let pollTimer: number | undefined
let pollAttempts = 0
let pollStartedAt: number | undefined
let pollingActive = true
const POLL_DELAYS = [5, 15, 30]
let statusController: AbortController | undefined
let statusPromise: Promise<void> | undefined
let disposed = false
const activePackage = computed(() => data.value?.selected_package || data.value?.packages[0])
const groups = computed(() => [
  { key: 'course', title: '课程基础资料', note: '始终导出完整课程数据', tables: data.value?.tables.filter(table => table.level === 'course') || [] },
  { key: 'binding', title: '实验 Session 绑定', note: '表2覆盖当前实验全部已发布班级，并包含班级列', tables: data.value?.tables.filter(table => table.level === 'experiment_binding') || [] },
  { key: 'experiment', title: '实验原始记录', note: '单班模式按班筛选；全部班级模式合并为表5和表6', tables: data.value?.tables.filter(table => table.level === 'experiment') || [] },
  { key: 'dictionary', title: '事件说明', note: '固定事件字段说明', tables: data.value?.tables.filter(table => table.level === 'dictionary') || [] },
])
const exportScope = computed<Record<string, unknown>>(() => {
  return (data.value?.scope.values as Record<string, unknown>) || {}
})
const canCreate = computed(() => Boolean(
  data.value?.scope.is_complete
  && data.value?.preparation.pending_count === 0
  && !activePackage.value?.blocked,
))

function formattedTime(value: string) {
  return new Date(value).toLocaleString('zh-CN')
}

async function createExport() {
  if (creating.value || disposed || !data.value || !activePackage.value || !canCreate.value) return
  creating.value = true
  createdJob.value = undefined
  error.value = ''
  try {
    stopPolling()
    statusController?.abort()
    const result = await api.createExport(courseId, {
      package: activePackage.value.key,
      confirm_identified: true,
      scope: exportScope.value,
    })
    if (disposed) return
    createdJob.value = result
    if (!['succeeded', 'failed', 'expired', 'invalidated'].includes(createdJob.value.status)) {
      pollAttempts = 0
      pollStartedAt = undefined
      schedulePoll()
    }
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '导出任务创建失败，请核对范围后重试。'
  } finally {
    creating.value = false
  }
}

function stopPolling() {
  if (pollTimer !== undefined) window.clearTimeout(pollTimer)
  pollTimer = undefined
}

function schedulePoll() {
  stopPolling()
  if (!pollingActive || disposed || document.visibilityState === 'hidden' || !createdJob.value || pollAttempts >= POLL_DELAYS.length) return
  if (!['queued', 'running'].includes(createdJob.value.status)) return
  pollStartedAt ??= Date.now()
  const delay = Math.max(0, POLL_DELAYS[pollAttempts]! * 1000 - (Date.now() - pollStartedAt))
  pollTimer = window.setTimeout(() => { pollAttempts += 1; void pollJob() }, delay)
}

function pollJob(manual = false): Promise<void> {
  if (statusPromise) return statusPromise
  if (disposed || !createdJob.value || (!manual && !pollingActive)) return Promise.resolve()
  const jobId = createdJob.value.job_id
  statusController = new AbortController()
  const signal = statusController.signal
  const promise = (async () => {
    try {
      const result = await api.exportJob(courseId, jobId, signal)
      if (disposed || signal.aborted || createdJob.value?.job_id !== jobId) return
      createdJob.value = result
      if (!['queued', 'running'].includes(result.status)) stopPolling()
      else if (!manual) schedulePoll()
    } catch (caught) {
      if (disposed || signal.aborted) return
      pollingActive = false
      stopPolling()
      error.value = caught instanceof Error ? caught.message : '导出任务状态查询失败，请到导出记录查看。'
    } finally { statusPromise = undefined }
  })()
  statusPromise = promise
  return promise
}

function onVisibilityChange() {
  if (document.visibilityState !== 'hidden') return
  pollingActive = false
  stopPolling()
  statusController?.abort()
}

function applyCatalog(result: DataCenterExportCatalog) {
  data.value = result
}

onMounted(async () => {
  document.addEventListener('visibilitychange', onVisibilityChange)
  try {
    const result = await api.exports(courseId, routeQuery(route.query))
    if (!disposed) applyCatalog(result)
  }
  catch (caught) { error.value = caught instanceof Error ? caught.message : '导出数据加载失败。' }
})
onBeforeUnmount(() => {
  disposed = true
  pollingActive = false
  stopPolling()
  statusController?.abort()
  document.removeEventListener('visibilitychange', onVisibilityChange)
})
</script>

<template>
  <PageHeader title="导出研究数据">
    <template #kicker>可复核数据包</template>
    <p>将当前实验、班级、学生和作答会话范围冻结为一个 ZIP 数据包。</p>
  </PageHeader>
  <Alert v-if="error" tone="danger">{{ error }}</Alert>
  <p v-if="!data && !error" role="status">正在加载研究数据导出…</p>
  <template v-else-if="data">
    <Alert tone="warning"><strong>实名数据确认：</strong>{{ data.identified_data_notice }}</Alert>
    <p v-if="data.preparation.pending_count" class="kt-warning-text">{{ data.preparation.pending_count }} 名已结束学生的数据正在后台整理，暂不能导出。</p>

    <section class="kt-card kt-export-package">
      <div class="kt-section-heading">
        <div>
          <p class="kt-kicker">唯一标准格式</p>
          <h2>标准实验数据包（ZIP）</h2>
          <p>{{ activePackage?.description }}</p>
        </div>
        <small>目录生成于 {{ formattedTime(data.generated_at) }}</small>
      </div>
      <section v-for="group in groups" :key="group.key" class="kt-export-file-group" :aria-label="group.title">
        <div><h3>{{ group.title }}</h3><p>{{ group.note }}</p></div>
        <ul class="kt-export-file-list">
          <li v-for="table in group.tables" :key="table.key" data-testid="export-table">
            <div><strong>{{ table.path }}</strong><span>{{ table.description }}</span></div>
            <small>{{ table.format.toUpperCase() }} · {{ table.total_items }} 条记录</small>
          </li>
        </ul>
      </section>
    </section>

    <section v-if="activePackage" class="kt-card kt-export-confirm">
      <h2>确认导出</h2>
      <p>将按当前选择生成标准实验数据包。表1、3、4、题目固定库和事件字典复用课程资产；表2覆盖当前实验全部已发布班级；表5、6按所选班级范围生成。</p>
      <p>数据包包含学生姓名和学号，请按研究数据管理要求使用。</p>
      <p v-if="!canCreate" class="kt-warning-text">请完成范围选择，并等待终态学生数据整理完成。</p>
      <form @submit.prevent="createExport">
        <button class="kt-button kt-button--danger" type="submit" :disabled="creating || !canCreate">{{ creating ? '正在提交…' : '导出实验数据包' }}</button>
      </form>
      <Alert v-if="createdJob" tone="success"><span v-if="createdJob.reused">复用已有任务。</span>导出任务状态：{{ createdJob.status_label }}。  <a v-if="createdJob.download_url" :href="createdJob.download_url">下载文件</a> <a :href="createdJob.history_url">查看导出记录</a> <button type="button" class="kt-button" data-testid="check-export-status" @click="pollJob(true)">检查导出状态</button></Alert>
    </section>
  </template>
</template>
