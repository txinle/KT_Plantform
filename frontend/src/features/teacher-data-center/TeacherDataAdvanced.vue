<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import Alert from '@/design-system/components/Alert.vue'
import Drawer from '@/design-system/components/Drawer.vue'
import EmptyState from '@/design-system/components/EmptyState.vue'
import MobileCardList from '@/design-system/components/MobileCardList.vue'
import PageHeader from '@/design-system/components/PageHeader.vue'
import Paginator from '@/design-system/components/Paginator.vue'

import { getTeacherDataCenterApi, routeQuery, type DataCenterAdvanced } from './api'

type Preview = NonNullable<DataCenterAdvanced['preview']>
type PreviewColumn = Preview['columns'][number]

interface PreviewField extends PreviewColumn {
  value: string
  internal: boolean
}

interface ReadableRecord {
  key: number
  studentName: string
  studentNumber: string
  questionCode: string
  summary: PreviewField[]
  occurredAt: string
  readableFields: PreviewField[]
  technicalFields: PreviewField[]
}

const route = useRoute()
const router = useRouter()
const courseId = Number(route.params.courseId)
const api = getTeacherDataCenterApi()
const data = ref<DataCenterAdvanced>()
const error = ref('')
const tableSearch = ref('')
const studentSearch = ref(typeof route.query.search === 'string' ? route.query.search : '')
const selectedRecord = ref<ReadableRecord>()

const summaryPriority = [
  'event_type', 'status', 'data_status', 'result', 'is_correct', 'score',
  'response_status', 'condition', 'condition_name', 'capture_mode',
  'quality_flags', 'probability', 'visit_count', 'assigned_sequence_index',
]
const timePriority = [
  'server_received_at', 'submitted_at', 'created_at', 'started_at',
  'completed_at', 'assigned_at', 'committed_at', 'generated_at',
]
const verboseFieldPattern = /(payload|state|metadata|chain|document|configuration|parameter_values|option_order)/i

const groups = computed(() => (data.value?.groups || []).map(group => ({
  ...group,
  tables: group.tables.filter(table => `${table.label} ${table.key}`.toLocaleLowerCase().includes(tableSearch.value.toLocaleLowerCase())),
})).filter(group => !tableSearch.value || group.tables.length))

const records = computed<ReadableRecord[]>(() => {
  const preview = data.value?.preview
  if (!preview) return []
  return preview.rows.map((row, rowIndex) => {
    const fields = preview.columns.map((column, columnIndex): PreviewField => ({
      ...column,
      value: displayValue(row[columnIndex]),
      internal: isInternalIdentifier(column.name),
    }))
    const byName = new Map(fields.map(field => [field.name, field]))
    const summary = summaryPriority
      .map(name => byName.get(name))
      .filter((field): field is PreviewField => Boolean(field && field.value !== '—' && !field.internal))
      .slice(0, 3)
    if (!summary.length) {
      summary.push(...fields.filter(field => (
        !field.internal
        && !identityField(field.name)
        && !timePriority.includes(field.name)
        && !verboseFieldPattern.test(field.name)
        && field.value !== '—'
      )).slice(0, 3))
    }
    return {
      key: rowIndex,
      studentName: fieldValue(byName, 'student_name'),
      studentNumber: fieldValue(byName, 'student_number'),
      questionCode: fieldValue(byName, 'question_code', 'code'),
      summary,
      occurredAt: fieldValue(byName, ...timePriority),
      readableFields: fields.filter(field => !field.internal),
      technicalFields: fields.filter(field => field.internal),
    }
  })
})

function displayValue(value: unknown) {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function isInternalIdentifier(name: string) {
  if (name === 'student_number' || name === 'question_code') return false
  return name === 'id'
    || name.endsWith('_id')
    || name.endsWith('_ids')
    || name.includes('hash')
    || name.includes('fingerprint')
    || name.includes('high_watermark')
}

function identityField(name: string) {
  return ['student_name', 'student_number', 'question_code', 'code'].includes(name)
}

function fieldValue(fields: Map<string, PreviewField>, ...names: string[]) {
  for (const name of names) {
    const value = fields.get(name)?.value
    if (value && value !== '—') return value
  }
  return '—'
}

async function load(query = routeQuery(route.query)) {
  error.value = ''
  selectedRecord.value = undefined
  try { data.value = await api.advanced(courseId, query) }
  catch (caught) { error.value = caught instanceof Error ? caught.message : '高级数据目录加载失败。' }
}

async function selectTable(table: string) {
  const query = { ...route.query, table, page: undefined }
  await router.push({ query })
  await load(routeQuery(query))
}

async function submitStudentSearch() {
  const query = { ...route.query, search: studentSearch.value.trim() || undefined, page: undefined }
  await router.push({ query })
  await load(routeQuery(query))
}

async function navigate(page: number) {
  const query = { ...route.query }
  if (page === 1) delete query.page
  else query.page = String(page)
  await router.push({ query })
  await load(routeQuery(query))
}

onMounted(() => load())
</script>

<template>
  <PageHeader title="高级数据工具">
    <template #kicker>Schema 与来源</template>
    <p>按语义定位研究数据，用学生、习题和过程摘要快速审阅；完整字段可在记录详情中核对。</p>
  </PageHeader>
  <Alert v-if="error" tone="danger">{{ error }}</Alert>
  <p v-if="!data && !error" role="status">正在加载高级数据工具…</p>
  <template v-else-if="data">
    <form class="kt-card kt-advanced-student-search" role="search" @submit.prevent="submitStudentSearch">
      <label for="advanced-student-search">搜索学生</label>
      <div>
        <input id="advanced-student-search" v-model="studentSearch" type="search" aria-label="按学生姓名或学号搜索" placeholder="输入学生姓名或学号">
        <button class="kt-button" type="submit">搜索当前数据表</button>
      </div>
      <small>搜索由服务端应用到当前实验、班级和版本范围，不只筛选本页。</small>
    </form>
    <div class="kt-advanced-layout">
      <aside class="kt-card kt-advanced-sidebar" aria-label="研究数据分类">
        <label>查找数据表<input v-model="tableSearch" type="search" placeholder="中文名称或表名"></label>
        <details v-for="group in groups" :key="group.key" :open="group.active">
          <summary><span>{{ group.label }}</span><span>{{ group.tables.length }}</span></summary>
          <p>{{ group.description }}</p>
          <div>
            <button v-for="table in group.tables" :key="table.key" type="button" :aria-current="table.active ? 'page' : undefined" @click="selectTable(table.key)">
              <span>{{ table.label }}</span><code>{{ table.key }}</code><small>{{ table.role_label }}</small>
            </button>
          </div>
        </details>
      </aside>
      <section class="kt-advanced-content">
        <div class="kt-section-heading">
          <div><p class="kt-kicker">{{ data.selected_table.source_layer }}</p><h2>{{ data.selected_table.label }}</h2><code>{{ data.selected_table.key }}</code></div>
          <strong>{{ data.selected_table.role_label }}</strong>
        </div>
        <Alert v-if="data.selected_table.role === 'source_of_truth'" tone="info"><strong>权威总事件表 / Source of Truth。</strong>用途分类视图不是并列的原始事实源。</Alert>
        <Alert v-else-if="data.selected_table.role === 'classified_view'" tone="info"><strong>分类视图。</strong>权威来源仍是 assessment_events。</Alert>
        <section class="kt-card kt-technical-info">
          <h3>技术信息</h3>
          <dl><div><dt>数据表</dt><dd><code>{{ data.selected_table.key }}</code></dd></div><div><dt>导出路径</dt><dd><code>{{ data.selected_table.path }}</code></dd></div><div><dt>Schema</dt><dd><code>{{ data.selected_table.schema_version }}</code></dd></div><div><dt>来源</dt><dd>{{ data.selected_table.source_layer }}</dd></div></dl>
        </section>
        <EmptyState v-if="!data.selected_experiment" title="请先在范围栏选择实验"><p>预览只读取当前明确的数据范围。</p></EmptyState>
        <template v-else-if="data.preview">
          <section class="kt-card kt-table-card" data-testid="readable-preview">
            <div class="kt-section-heading">
              <div><h3>可读预览</h3><p>主表隐藏内部唯一标识；点击记录可查看全部原始字段。</p></div>
              <strong>共 {{ data.preview.page.total_items }} 条</strong>
            </div>
            <div v-if="records.length" class="kt-table-scroll kt-desktop-only">
              <table class="kt-table">
                <thead><tr><th>学生</th><th>习题</th><th>事件 / 过程摘要</th><th>时间</th><th>操作</th></tr></thead>
                <tbody>
                  <tr v-for="record in records" :key="record.key">
                    <td><strong>{{ record.studentName }}</strong><small>{{ record.studentNumber }}</small></td>
                    <td>{{ record.questionCode }}</td>
                    <td><dl class="kt-preview-summary"><div v-for="field in record.summary" :key="field.name"><dt>{{ field.description }}</dt><dd>{{ field.value }}</dd></div><div v-if="!record.summary.length"><dt>记录类型</dt><dd>{{ data.selected_table.label }}</dd></div></dl></td>
                    <td>{{ record.occurredAt }}</td>
                    <td><button class="kt-button kt-button--secondary" type="button" @click="selectedRecord = record">查看完整记录</button></td>
                  </tr>
                </tbody>
              </table>
            </div>
            <MobileCardList v-if="records.length" class="kt-mobile-only">
              <li v-for="record in records" :key="record.key" class="kt-mobile-data-card">
                <dl><div><dt>学生</dt><dd>{{ record.studentName }}<small>{{ record.studentNumber }}</small></dd></div><div><dt>习题</dt><dd>{{ record.questionCode }}</dd></div><div><dt>时间</dt><dd>{{ record.occurredAt }}</dd></div></dl>
                <div class="kt-mobile-summary"><p v-for="field in record.summary" :key="field.name"><strong>{{ field.description }}</strong>{{ field.value }}</p></div>
                <button class="kt-button kt-button--secondary" type="button" @click="selectedRecord = record">查看完整记录</button>
              </li>
            </MobileCardList>
            <EmptyState v-if="!records.length" :title="studentSearch.trim() ? '没有匹配该学生的记录' : '当前范围暂无记录'" />
            <Paginator :page="data.preview.page.number" :total-pages="data.preview.page.total_pages" @navigate="navigate" />
          </section>
        </template>
      </section>
    </div>
  </template>
  <Drawer :open="Boolean(selectedRecord)" title="完整记录" @close="selectedRecord = undefined">
    <template v-if="selectedRecord">
      <h2>{{ selectedRecord.studentName }} · {{ selectedRecord.questionCode }}</h2>
      <p>当前数据表：{{ data?.selected_table.label }}。可读字段与技术标识均来自同一条服务端预览记录。</p>
      <section class="kt-record-fields"><h3>记录字段</h3><dl><div v-for="field in selectedRecord.readableFields" :key="field.name"><dt>{{ field.description }}<code>{{ field.name }}</code></dt><dd>{{ field.value }}</dd></div></dl></section>
      <details v-if="selectedRecord.technicalFields.length" class="kt-record-identifiers"><summary>技术标识（{{ selectedRecord.technicalFields.length }} 项）</summary><dl><div v-for="field in selectedRecord.technicalFields" :key="field.name"><dt>{{ field.description }}<code>{{ field.name }}</code></dt><dd>{{ field.value }}</dd></div></dl></details>
    </template>
  </Drawer>
</template>

<style scoped>
.kt-advanced-student-search { display: grid; gap: .45rem; margin-bottom: 1rem; }
.kt-advanced-student-search > div { display: flex; gap: .75rem; }
.kt-advanced-student-search input { flex: 1; min-width: 0; }
.kt-advanced-student-search small, .kt-advanced-sidebar > details > p { color: var(--kt-color-muted); }
.kt-advanced-sidebar > details > p { font-size: .8rem; margin: .45rem 0 0; }
.kt-table td > small, .kt-mobile-data-card small { display: block; color: var(--kt-color-muted); margin-top: .2rem; }
.kt-preview-summary, .kt-record-fields dl, .kt-record-identifiers dl { display: grid; gap: .55rem; margin: 0; }
.kt-preview-summary div, .kt-record-fields dl > div, .kt-record-identifiers dl > div { min-width: 0; }
.kt-preview-summary dt, .kt-record-fields dt, .kt-record-identifiers dt { color: var(--kt-color-muted); font-size: .75rem; }
.kt-preview-summary dd, .kt-record-fields dd, .kt-record-identifiers dd { margin: .15rem 0 0; overflow-wrap: anywhere; white-space: pre-wrap; }
.kt-record-fields code, .kt-record-identifiers code { display: block; margin-top: .15rem; }
.kt-record-identifiers { border-top: 1px solid var(--kt-color-border); margin-top: 1rem; padding-top: 1rem; }
.kt-record-identifiers summary { color: var(--kt-color-primary); cursor: pointer; font-weight: 700; margin-bottom: 1rem; }
.kt-mobile-summary { display: grid; gap: .5rem; margin-bottom: .75rem; }
.kt-mobile-summary p { display: grid; gap: .2rem; margin: 0; overflow-wrap: anywhere; }
@media (max-width: 48rem) {
  .kt-advanced-student-search > div { display: grid; }
}
</style>
