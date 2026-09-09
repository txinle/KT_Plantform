<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
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
const activePackage = computed(() => data.value?.selected_package || data.value?.packages[0])
const groups = computed(() => [
  { key: 'course', title: '课程基础资料', note: '始终导出完整课程数据', tables: data.value?.tables.filter(table => table.level === 'course') || [] },
  { key: 'experiment', title: '实验原始记录', note: '学生和会话筛选仅作用于逐题作答与行为过程', tables: data.value?.tables.filter(table => table.level === 'experiment') || [] },
  { key: 'dictionary', title: '事件说明', note: '固定事件字段说明', tables: data.value?.tables.filter(table => table.level === 'dictionary') || [] },
])

function scopeValues(value: unknown): Record<string, unknown> {
  const result: Record<string, unknown> = {}
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return result
  for (const [key, item] of Object.entries(value)) result[key] = item
  return result
}

function formattedTime(value: string) {
  return new Date(value).toLocaleString('zh-CN')
}

async function createExport() {
  if (!data.value || !activePackage.value || activePackage.value.blocked) return
  creating.value = true
  createdJob.value = undefined
  error.value = ''
  try {
    createdJob.value = await api.createExport(courseId, {
      package: activePackage.value.key,
      confirm_identified: true,
      scope: scopeValues(data.value.scope.values),
    })
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '导出任务创建失败，请核对范围后重试。'
  } finally {
    creating.value = false
  }
}

onMounted(async () => {
  try { data.value = await api.exports(courseId, routeQuery(route.query)) }
  catch (caught) { error.value = caught instanceof Error ? caught.message : '导出数据加载失败。' }
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
      <p>将按范围“{{ data.scope.display }}”生成标准实验数据包。课程基础资料和课程题目固定库始终覆盖完整课程，学生和会话筛选仅作用于逐题作答与行为过程。</p>
      <p>数据包包含学生姓名和学号，请按研究数据管理要求使用。</p>
      <p v-if="activePackage.blocked || !data.scope.is_complete" class="kt-warning-text">请先选择完整的实验和班级范围。</p>
      <form v-else @submit.prevent="createExport">
        <button class="kt-button kt-button--danger" type="submit" :disabled="creating">{{ creating ? '正在生成…' : '导出实验数据包' }}</button>
      </form>
      <Alert v-if="createdJob" tone="success">导出任务状态：{{ createdJob.status_label }}。 <a v-if="createdJob.download_url" :href="createdJob.download_url">下载文件</a> <a :href="createdJob.history_url">查看导出记录</a></Alert>
    </section>
  </template>
</template>
