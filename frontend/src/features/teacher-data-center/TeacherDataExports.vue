<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import Alert from '@/design-system/components/Alert.vue'
import PageHeader from '@/design-system/components/PageHeader.vue'

import { getTeacherDataCenterApi, routeQuery, type DataCenterExportCatalog, type DataCenterExportJobMutation, type DataQuery } from './api'

const route = useRoute()
const courseId = Number(route.params.courseId)
const api = getTeacherDataCenterApi()
const data = ref<DataCenterExportCatalog>()
const error = ref('')
const creating = ref(false)
const createdJob = ref<DataCenterExportJobMutation>()
const selectedExperimentId = ref<number | null>(null)
const classMode = ref<'single' | 'all'>('single')
const selectedClassId = ref<number | null>(null)
let pollTimer: number | undefined
let pollAttempts = 0
let pollingActive = true
const MAX_POLL_ATTEMPTS = 40
const activePackage = computed(() => data.value?.selected_package || data.value?.packages[0])
const groups = computed(() => [
  { key: 'course', title: '课程基础资料', note: '始终导出完整课程数据', tables: data.value?.tables.filter(table => table.level === 'course') || [] },
  { key: 'binding', title: '实验 Session 绑定', note: '表2覆盖当前实验全部已发布班级，并包含班级列', tables: data.value?.tables.filter(table => table.level === 'experiment_binding') || [] },
  { key: 'experiment', title: '实验原始记录', note: '单班模式按班筛选；全部班级模式合并为表5和表6', tables: data.value?.tables.filter(table => table.level === 'experiment') || [] },
  { key: 'dictionary', title: '事件说明', note: '固定事件字段说明', tables: data.value?.tables.filter(table => table.level === 'dictionary') || [] },
])
const exportScope = computed<Record<string, unknown>>(() => {
  const scope: Record<string, unknown> = {
    scope_version: 2,
    experiment_id: selectedExperimentId.value,
    class_mode: classMode.value,
  }
  if (classMode.value === 'single') scope.class_id = selectedClassId.value
  return scope
})
const canCreate = computed(() => Boolean(
  selectedExperimentId.value
  && (classMode.value === 'all' || selectedClassId.value)
  && data.value?.preparation.pending_count === 0
  && !activePackage.value?.blocked,
))

function formattedTime(value: string) {
  return new Date(value).toLocaleString('zh-CN')
}

async function createExport() {
  if (!data.value || !activePackage.value || !canCreate.value) return
  creating.value = true
  createdJob.value = undefined
  error.value = ''
  try {
    createdJob.value = await api.createExport(courseId, {
      package: activePackage.value.key,
      confirm_identified: true,
      scope: exportScope.value,
    })
    if (!['succeeded', 'failed', 'expired', 'invalidated'].includes(createdJob.value.status)) {
      pollAttempts = 0
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
  if (!pollingActive || !createdJob.value || pollAttempts >= MAX_POLL_ATTEMPTS) return
  pollTimer = window.setTimeout(() => { void pollJob() }, 1500)
}

async function pollJob() {
  if (!createdJob.value) return
  pollAttempts += 1
  try {
    const result = await api.exportJob(courseId, createdJob.value.job_id)
    if (!pollingActive) return
    createdJob.value = result
    if (['succeeded', 'failed', 'expired', 'invalidated'].includes(createdJob.value.status)) {
      stopPolling()
      return
    }
    schedulePoll()
  } catch (caught) {
    stopPolling()
    error.value = caught instanceof Error ? caught.message : '导出任务状态查询失败，请到导出记录查看。'
  }
}

function applyCatalog(result: DataCenterExportCatalog) {
  data.value = result
  const values = result.scope.values as Record<string, unknown>
  selectedExperimentId.value = Number(values.experiment_id || result.experiments[0]?.id || 0) || null
  classMode.value = values.class_mode === 'all' ? 'all' : 'single'
  selectedClassId.value = Number(values.class_id || result.class_choices[0]?.id || 0) || null
}

async function changeExperiment() {
  if (!selectedExperimentId.value) return
  const requestedMode = classMode.value
  error.value = ''
  try {
    const result = await api.exports(courseId, {
      scope_version: 2,
      experiment_id: selectedExperimentId.value,
      class_mode: 'all',
    })
    applyCatalog(result)
    selectedClassId.value = result.class_choices[0]?.id || null
    if (requestedMode === 'single' && selectedClassId.value) {
      const single = await api.exports(courseId, {
        scope_version: 2,
        experiment_id: selectedExperimentId.value,
        class_mode: 'single',
        class_id: selectedClassId.value,
      })
      applyCatalog(single)
    }
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '实验导出范围加载失败。'
  }
}

async function changeClassMode() {
  if (!selectedExperimentId.value) return
  error.value = ''
  try {
    const query: DataQuery = {
      scope_version: 2,
      experiment_id: selectedExperimentId.value,
      class_mode: classMode.value,
    }
    if (classMode.value === 'single') {
      selectedClassId.value ||= data.value?.class_choices[0]?.id || null
      if (!selectedClassId.value) return
      query.class_id = selectedClassId.value
    }
    applyCatalog(await api.exports(courseId, query))
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '班级导出范围加载失败。'
  }
}

async function changeClass() {
  if (classMode.value === 'single') await changeClassMode()
}

onMounted(async () => {
  try {
    const result = await api.exports(courseId, routeQuery(route.query))
    applyCatalog(result)
    if (!result.scope.is_complete && selectedExperimentId.value) await changeExperiment()
  }
  catch (caught) { error.value = caught instanceof Error ? caught.message : '导出数据加载失败。' }
})
onBeforeUnmount(() => {
  pollingActive = false
  stopPolling()
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

    <section class="kt-card kt-export-scope" aria-labelledby="export-scope-title">
      <div class="kt-section-heading"><div><p class="kt-kicker">导出范围</p><h2 id="export-scope-title">选择一个实验及班级范围</h2></div></div>
      <div class="kt-filter-grid">
        <label>实验
          <select v-model.number="selectedExperimentId" data-testid="experiment" @change="changeExperiment">
            <option v-for="experiment in data.experiments" :key="experiment.id" :value="experiment.id">{{ experiment.name }}</option>
          </select>
        </label>
        <label>班级范围
          <select v-model="classMode" data-testid="class-mode" @change="changeClassMode">
            <option value="single">单个已发布班级</option>
            <option value="all">全部已发布班级</option>
          </select>
        </label>
        <label v-if="classMode === 'single'">班级
          <select v-model.number="selectedClassId" data-testid="class" @change="changeClass">
            <option v-for="item in data.class_choices" :key="item.id" :value="item.id">{{ item.academic_year }} {{ item.name }}</option>
          </select>
        </label>
      </div>
      <p v-if="data.preparation.pending_count" class="kt-warning-text">{{ data.preparation.pending_count }} 名已结束学生的数据正在后台整理，暂不能导出。</p>
    </section>

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
      <Alert v-if="createdJob" tone="success">导出任务状态：{{ createdJob.status_label }}。 <a v-if="createdJob.download_url" :href="createdJob.download_url">下载文件</a> <a :href="createdJob.history_url">查看导出记录</a></Alert>
    </section>
  </template>
</template>
