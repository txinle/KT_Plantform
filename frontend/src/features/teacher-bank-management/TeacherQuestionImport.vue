<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ApiError } from '@/api/client'
import Alert from '@/design-system/components/Alert.vue'
import Icon from '@/design-system/Icon.vue'
import {
  getTeacherBankManagementApi,
  type QuestionImportConfirmation,
  type QuestionImportPreview,
  type QuestionImportPreviewRow,
} from './api'
import './question-import.css'

type PreviewFilter = 'all' | 'ready' | 'duplicate' | 'invalid'
type Mapping = NonNullable<QuestionImportPreviewRow['kc_mapping']>
const route = useRoute()
const router = useRouter()
const api = getTeacherBankManagementApi()
const courseId = Number(route.params.courseId)
const bankId = Number(route.params.bankId)
const fileInput = ref<HTMLInputElement>()
const fileMenu = ref<HTMLDetailsElement>()
const format = ref<'spreadsheet' | 'docx'>('spreadsheet')
const preview = ref<QuestionImportPreview>()
const confirmation = ref<QuestionImportConfirmation>()
const uploadedFile = ref<{ name: string; size: number; uploadedAt: Date }>()
const filter = ref<PreviewFilter>('all')
const search = ref('')
const questionType = ref('')
const showFilters = ref(false)
const page = ref(1)
const pageSize = ref(10)
const selectedIds = ref<string[]>([])
const activeRowId = ref<string>()
const detailOpen = ref(true)
const mobileDetailOpen = ref(false)
const compact = ref(false)
const detailPanel = ref<HTMLElement>()
const closeDetailButton = ref<HTMLButtonElement>()
let detailTrigger: HTMLElement | undefined
let viewport: MediaQueryList | undefined
const error = ref('')
const busy = ref(false)

const acceptedFileTypes = computed(() => format.value === 'docx' ? '.docx' : '.csv,.xlsx')
const rows = computed(() => preview.value?.preview.rows || [])
const readyLabel = computed(() => confirmation.value ? '已导入' : '可导入')
const outcomes = computed(() => new Map(confirmation.value?.rows.map((row) => [row.row_id, row]) || []))
const summary = computed(() => {
  const counts = { ready: 0, duplicate: 0, invalid: 0, unselected: 0 }
  for (const row of rows.value) counts[classification(row)] += 1
  return counts
})
const filters = computed(() => [
  { value: 'all' as const, label: '全部', count: rows.value.length },
  { value: 'ready' as const, label: readyLabel.value, count: summary.value.ready },
  { value: 'duplicate' as const, label: '重复', count: summary.value.duplicate },
  { value: 'invalid' as const, label: '需修正', count: summary.value.invalid },
])
const questionTypes = computed(() => [...new Map(rows.value.map((row) => [row.question_type, row.question_type_label])).entries()])
const filteredRows = computed(() => rows.value.filter((row) => {
  const query = search.value.trim().toLocaleLowerCase()
  return (filter.value === 'all' || classification(row) === filter.value)
    && (!questionType.value || row.question_type === questionType.value)
    && (!query || (String(row.sequence) + ' ' + row.text).toLocaleLowerCase().includes(query))
}))
const pageCount = computed(() => Math.max(1, Math.ceil(filteredRows.value.length / pageSize.value)))
const pageRows = computed(() => filteredRows.value.slice((page.value - 1) * pageSize.value, page.value * pageSize.value))
const selectablePageRows = computed(() => pageRows.value.filter((row) => row.classification === 'ready'))
const pageSelected = computed(() => selectablePageRows.value.length > 0 && selectablePageRows.value.every((row) => selectedIds.value.includes(row.row_id)))
const pagePartlySelected = computed(() => !pageSelected.value && selectablePageRows.value.some((row) => selectedIds.value.includes(row.row_id)))
const canConfirm = computed(() => Boolean(preview.value?.preview.confirmable && selectedIds.value.length && !confirmation.value && !busy.value))
const activeRow = computed(() => rows.value.find((row) => row.row_id === activeRowId.value))
const activeIndex = computed(() => rows.value.findIndex((row) => row.row_id === activeRowId.value))
const detailUrl = computed(() => confirmation.value?.detail_url || '/teacher/courses/' + courseId + '/banks/' + bankId + '/')
const fileSize = computed(() => {
  const size = uploadedFile.value?.size || 0
  return size >= 1024 * 1024 ? (size / 1024 / 1024).toFixed(1) + ' MB' : Math.max(1, Math.round(size / 1024)) + ' KB'
})
const uploadTime = computed(() => {
  const date = uploadedFile.value?.uploadedAt
  if (!date) return ''
  const pad = (part: number) => String(part).padStart(2, '0')
  return date.getFullYear() + '-' + pad(date.getMonth() + 1) + '-' + pad(date.getDate()) + ' ' + pad(date.getHours()) + ':' + pad(date.getMinutes())
})
const fileKind = computed(() => uploadedFile.value?.name.toLowerCase().endsWith('.docx') ? 'word' : 'excel')

function classification(row: QuestionImportPreviewRow) {
  const status = outcomes.value.get(row.row_id)?.status
  return status === 'created' ? 'ready' : status || row.classification
}
function rowStatus(row: QuestionImportPreviewRow) {
  const status = outcomes.value.get(row.row_id)?.status || row.classification
  if (status === 'created') return { label: '已创建', tone: 'success' }
  if (status === 'ready') return { label: '可导入', tone: 'success' }
  if (status === 'duplicate') return { label: '重复', tone: 'warning' }
  if (status === 'unselected') return { label: '未选择', tone: 'neutral' }
  return { label: '需修正', tone: 'danger' }
}
function rowErrors(row: QuestionImportPreviewRow) {
  return outcomes.value.get(row.row_id)?.errors ?? row.errors
}
function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'boolean') return value ? '正确' : '错误'
  if (Array.isArray(value)) return value.map(formatValue).join('、')
  return typeof value === 'object' ? JSON.stringify(value) : String(value)
}
function rootKc(row: QuestionImportPreviewRow) {
  return row.kc_mapping?.nodes.find((node) => node.kc_code === row.kc_mapping?.root_kc_code)
}
function uniqueNodes(mapping: Mapping) {
  return [...new Map(mapping.nodes.map((node) => [node.kc_code, node])).values()]
}
function kcSummary(row: QuestionImportPreviewRow) {
  if (!row.kc_mapping) return '未提供 KC 映射'
  return '共 ' + uniqueNodes(row.kc_mapping).length + ' 个 KC · ' + row.kc_mapping.nodes.filter((node) => node.parent_kc_code).length + ' 条关系'
}
function kcEdgeLabel(mapping: Mapping, node: Mapping['nodes'][number]) {
  const current = node.name + '（' + node.kc_code + '）'
  if (!node.parent_kc_code) return '主知识点：' + current
  const parent = mapping.nodes.find((candidate) => candidate.kc_code === node.parent_kc_code)
  return (parent ? parent.name + '（' + parent.kc_code + '）' : node.parent_kc_code) + ' → ' + current
}
const graph = computed(() => {
  const mapping = activeRow.value?.kc_mapping
  if (!mapping) return { width: 320, height: 160, nodes: [], edges: [] }
  const nodes = uniqueNodes(mapping)
  const depths = new Map(nodes.map((node) => [node.kc_code, 0]))
  for (let pass = 0; pass < nodes.length; pass++) {
    for (const edge of mapping.nodes) {
      if (edge.parent_kc_code) depths.set(edge.kc_code, Math.max(depths.get(edge.kc_code) || 0, (depths.get(edge.parent_kc_code) || 0) + 1))
    }
  }
  const maxDepth = Math.max(0, ...depths.values())
  const columns = Array.from({ length: maxDepth + 1 }, (_, depth) => nodes.filter((node) => depths.get(node.kc_code) === depth))
  const height = Math.max(160, Math.max(...columns.map((column) => column.length)) * 82)
  const positioned = columns.flatMap((column, depth) => column.map((node, index) => ({
    ...node, x: 2 + depth * 112, y: (index + .5) * height / column.length - 22,
  })))
  const edges = mapping.nodes.filter((node) => node.parent_kc_code).flatMap((node) => {
    const start = positioned.find((candidate) => candidate.kc_code === node.parent_kc_code)
    const end = positioned.find((candidate) => candidate.kc_code === node.kc_code)
    return start && end ? [{ key: start.kc_code + '>' + end.kc_code, x1: start.x + 86, y1: start.y + 22, x2: end.x - 4, y2: end.y + 22 }] : []
  })
  return { width: Math.max(320, (maxDepth + 1) * 112 - 22), height, nodes: positioned, edges }
})
function isCorrectOption(key: string) {
  const answer = activeRow.value?.answer
  return Array.isArray(answer) ? answer.includes(key) : answer === key
}
function toggleRow(rowId: string, checked: boolean) {
  selectedIds.value = checked ? [...new Set([...selectedIds.value, rowId])] : selectedIds.value.filter((id) => id !== rowId)
}
function togglePage(checked: boolean) {
  for (const row of selectablePageRows.value) toggleRow(row.row_id, checked)
}
function closeFileMenu() {
  if (preview.value && fileMenu.value) fileMenu.value.open = false
}
function clearPreview() {
  preview.value = undefined
  confirmation.value = undefined
  uploadedFile.value = undefined
  selectedIds.value = []
  activeRowId.value = undefined
  filter.value = 'all'
  search.value = ''
  questionType.value = ''
  page.value = 1
}
async function selectFile(event: Event) {
  const input = event.currentTarget as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  clearPreview()
  busy.value = true
  error.value = ''
  try {
    preview.value = await api.previewQuestionImport(courseId, bankId, file)
    uploadedFile.value = { name: file.name, size: file.size, uploadedAt: new Date() }
    selectedIds.value = rows.value.filter((row) => row.classification === 'ready').map((row) => row.row_id)
    activeRowId.value = rows.value[0]?.row_id
    detailOpen.value = true
    mobileDetailOpen.value = false
  } catch (caught) {
    error.value = caught instanceof ApiError && typeof caught.fields.file === 'string'
      ? caught.fields.file
      : caught instanceof Error ? caught.message : '题库文件校验失败。'
  } finally {
    busy.value = false
    input.value = ''
  }
}
async function confirm() {
  if (!preview.value || !canConfirm.value) return
  busy.value = true
  error.value = ''
  try {
    confirmation.value = await api.confirmQuestionImport(courseId, bankId, preview.value.preview_id, selectedIds.value)
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '题库导入确认失败，请重新生成预览。'
  } finally {
    busy.value = false
  }
}
async function inspectRow(row: QuestionImportPreviewRow, event?: Event) {
  detailTrigger = event?.currentTarget instanceof HTMLElement ? event.currentTarget : undefined
  activeRowId.value = row.row_id
  detailOpen.value = true
  mobileDetailOpen.value = true
  if (compact.value) {
    await nextTick()
    closeDetailButton.value?.focus()
  }
}
function closeDetail() {
  detailOpen.value = false
  mobileDetailOpen.value = false
  detailTrigger?.focus()
}
function adjacentQuestion(direction: number) {
  const row = rows.value[activeIndex.value + direction]
  if (row) activeRowId.value = row.row_id
}
function detailKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') { event.preventDefault(); closeDetail(); return }
  if (!compact.value || event.key !== 'Tab') return
  const controls = [...(detailPanel.value?.querySelectorAll<HTMLElement>('button:not([disabled]), a[href], summary, [tabindex="0"]') || [])]
  const first = controls[0]
  const last = controls.at(-1)
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
}
function resize() { compact.value = viewport?.matches ?? false }
watch([filter, search, questionType, pageSize], () => { page.value = 1 })
onMounted(() => {
  viewport = window.matchMedia?.('(max-width: 1199px)')
  resize()
  viewport?.addEventListener('change', resize)
})
onBeforeUnmount(() => viewport?.removeEventListener('change', resize))
</script>

<template>
  <div class="kt-question-import" :class="{ 'has-detail': activeRow && detailOpen, 'is-mobile-inspecting': mobileDetailOpen && detailOpen }">
    <div class="qimp-main" :inert="compact && mobileDetailOpen && detailOpen">
      <nav class="qimp-breadcrumb" aria-label="层级目录">
        <RouterLink :to="detailUrl">题库</RouterLink><Icon name="chevron-right" :size="14" /><span>导入题库</span>
      </nav>
      <header class="qimp-heading">
        <h1>导入题库</h1>
        <p>上传完成后请仔细核对题干、选项、答案与 KC 映射，确认无误后导入系统。</p>
      </header>
      <Alert v-if="error" tone="danger">{{ error }}</Alert>

      <section class="qimp-file" :class="{ 'is-empty': !preview }" aria-label="题库文件">
        <div v-if="uploadedFile" data-file-summary class="qimp-file-info">
          <span class="qimp-file-icon" :class="'is-' + fileKind" aria-hidden="true"><strong>{{ fileKind === 'word' ? 'W' : 'X' }}</strong><span>▤</span></span>
          <div><div class="qimp-file-title"><strong>{{ uploadedFile.name }}</strong><span class="qimp-file-ok" aria-label="解析完成"><Icon name="check" :size="14" /></span></div><p>上传时间：{{ uploadTime }}<span>文件大小：{{ fileSize }}</span></p></div>
        </div>
        <div v-else class="qimp-empty-file">
          <h2>{{ busy ? '正在解析文件…' : '选择题库文件' }}</h2>
          <p>一份文件导入题目与 KC 映射。请使用当前课程已有的知识点。</p>
        </div>
        <div v-if="preview" class="qimp-summary" aria-label="预览状态汇总">
          <strong class="qimp-total">共解析 {{ rows.length }} 道题</strong>
          <span class="is-ready">{{ readyLabel }} <strong>{{ summary.ready }}</strong></span>
          <span class="is-duplicate">重复 <strong>{{ summary.duplicate }}</strong></span>
          <span class="is-invalid">需修正 <strong>{{ summary.invalid }}</strong></span>
        </div>
        <div class="qimp-upload-actions">
          <button class="qimp-button" :class="{ 'is-primary': !preview }" type="button" :disabled="busy" @click="fileInput?.click()">{{ busy ? '正在校验…' : preview ? '重新选择文件' : '选择文件并预览' }}</button>
          <details ref="fileMenu" class="qimp-file-menu" :open="!preview">
            <summary v-if="preview" class="qimp-button qimp-icon-button" aria-label="文件格式与模板"><Icon name="chevron-down" :size="16" /></summary>
            <div class="qimp-file-menu-content">
              <fieldset><legend>文件格式</legend><label><input v-model="format" type="radio" name="question-import-format" value="spreadsheet" @change="closeFileMenu"> Excel / CSV</label><label><input id="question-import-format-docx" v-model="format" type="radio" name="question-import-format" value="docx" @change="closeFileMenu"> Word .docx</label></fieldset>
              <a data-question-template="xlsx" href="/teacher/import-templates/questions.xlsx" download="question_import_template.xlsx">下载 Excel 模板</a>
              <a data-question-template="csv" href="/teacher/import-templates/questions.csv" download="question_import_template.csv">下载 CSV 模板</a>
              <a data-question-template="docx" href="/teacher/import-templates/questions.docx" download="question_import_template.docx">下载 Word 模板</a>
            </div>
          </details>
        </div>
        <input ref="fileInput" class="kt-visually-hidden" type="file" :accept="acceptedFileTypes" aria-label="上传题库文件" @change="selectFile">
      </section>

      <Alert v-if="confirmation" :tone="confirmation.failed ? 'warning' : 'success'">
        <strong>本次导入已完成。</strong>新增 {{ confirmation.created }} 道，跳过 {{ confirmation.skipped }} 道，未完成 {{ confirmation.failed }} 道。
        <span v-if="summary.unselected">另有 {{ summary.unselected }} 道未选择。</span>未完成的题目保留在下方，修正后可重新上传完整文件。
      </Alert>

      <section v-if="preview" data-import-preview class="qimp-preview" aria-label="导入预览">
        <div class="qimp-toolbar">
          <div class="qimp-filters" role="group" aria-label="显示预览行">
            <button v-for="item in filters" :id="item.value === 'invalid' ? 'question-import-filter-invalid' : undefined" :key="item.value" :data-import-filter="item.value" class="qimp-filter" :class="{ 'is-active': filter === item.value }" type="button" :aria-pressed="filter === item.value" @click="filter = item.value">{{ item.label }} <span>{{ item.count }}</span></button>
          </div>
          <label class="qimp-search"><span class="kt-visually-hidden">搜索题目</span><input v-model="search" type="search" placeholder="搜索题目（题号、题干关键词）"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5" /><path d="m15.5 15.5 5 5" /></svg></label>
          <button class="qimp-button qimp-filter-toggle" type="button" :aria-expanded="showFilters" @click="showFilters = !showFilters"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 4h18l-7 8v7l-4 2v-9Z" /></svg>筛选</button>
        </div>
        <div v-if="showFilters" class="qimp-extra-filters"><label>题型<select v-model="questionType"><option value="">全部题型</option><option v-for="[value, label] in questionTypes" :key="value" :value="value">{{ label }}</option></select></label><button class="qimp-link" type="button" @click="questionType = ''; search = ''; filter = 'all'">重置筛选</button></div>
        <div v-if="pageRows.length" class="kt-table-scroll qimp-table-wrap kt-desktop-only">
          <table class="qimp-table kt-question-import__table">
            <colgroup><col class="qimp-col-check"><col class="qimp-col-number"><col class="qimp-col-text"><col class="qimp-col-type"><col class="qimp-col-answer"><col class="qimp-col-kc"><col class="qimp-col-status"><col class="qimp-col-action"></colgroup>
            <thead><tr><th><input type="checkbox" aria-label="选择本页可导入题目" :checked="pageSelected" :indeterminate="pagePartlySelected" :disabled="Boolean(confirmation) || busy || !selectablePageRows.length" @change="togglePage(($event.target as HTMLInputElement).checked)"></th><th title="文件内预览题号，正式题号在导入后分配">题号</th><th>题目</th><th>题型</th><th>答案</th><th>KC 映射</th><th>状态</th><th>操作</th></tr></thead>
            <tbody>
              <tr v-for="row in pageRows" :key="row.row_id" :data-import-row="row.row_id" :class="{ 'is-current': activeRowId === row.row_id && detailOpen }">
                <td><input type="checkbox" :aria-label="'选择题目 ' + row.sequence" :checked="selectedIds.includes(row.row_id)" :disabled="row.classification !== 'ready' || Boolean(confirmation) || busy" @change="toggleRow(row.row_id, ($event.target as HTMLInputElement).checked)"></td>
                <td>{{ row.sequence }}</td>
                <td><p class="qimp-question-text kt-question-import__multiline">{{ row.text || '未读取到题干' }}</p></td>
                <td>{{ row.question_type_label }}</td>
                <td data-answer><span class="qimp-answer-text">{{ formatValue(row.answer) }}</span></td>
                <td><p v-if="rootKc(row)">主：{{ rootKc(row)?.name }}（{{ rootKc(row)?.kc_code }}）</p><p class="qimp-kc-summary"><Icon name="definition" :size="15" />{{ kcSummary(row) }}</p></td>
                <td><span class="qimp-status" :class="'is-' + rowStatus(row).tone">{{ rowStatus(row).label }}</span><p v-if="rowErrors(row).length" class="kt-question-import__errors">{{ rowErrors(row).join('；') }}</p></td>
                <td><button class="qimp-link" type="button" :aria-label="'查看题目 ' + row.sequence" @click="inspectRow(row, $event)">查看</button></td>
              </tr>
            </tbody>
          </table>
        </div>
        <ul v-if="pageRows.length" class="qimp-mobile-list kt-mobile-only">
          <li v-for="row in pageRows" :key="row.row_id" :data-import-mobile-row="row.row_id" class="qimp-mobile-card">
            <div class="qimp-mobile-heading"><label><input type="checkbox" :aria-label="'选择题目 ' + row.sequence" :checked="selectedIds.includes(row.row_id)" :disabled="row.classification !== 'ready' || Boolean(confirmation) || busy" @change="toggleRow(row.row_id, ($event.target as HTMLInputElement).checked)">题目 {{ row.sequence }}</label><span class="qimp-status" :class="'is-' + rowStatus(row).tone">{{ rowStatus(row).label }}</span></div>
            <h3 class="kt-question-import__multiline">{{ row.text || '未读取到题干' }}</h3>
            <dl><div><dt>题型</dt><dd>{{ row.question_type_label }}</dd></div><div><dt>答案</dt><dd>{{ formatValue(row.answer) }}</dd></div></dl>
            <p v-if="rootKc(row)" class="qimp-mobile-kc">主：{{ rootKc(row)?.name }}（{{ rootKc(row)?.kc_code }}）</p><p class="qimp-kc-summary"><Icon name="definition" :size="15" />{{ kcSummary(row) }}</p>
            <p v-if="rowErrors(row).length" class="kt-question-import__errors">{{ rowErrors(row).join('；') }}</p>
            <button class="qimp-link" type="button" :aria-label="'查看题目 ' + row.sequence" @click="inspectRow(row, $event)">查看完整内容与 KC 映射 <Icon name="chevron-right" :size="14" /></button>
          </li>
        </ul>
        <p v-else class="qimp-empty" role="status">当前筛选没有预览行。</p>
        <div class="qimp-pagination">
          <span>已选择 <strong>{{ selectedIds.length }}</strong> 道题</span>
          <div class="qimp-pagination-controls"><button class="qimp-button qimp-icon-button" type="button" aria-label="上一页" :disabled="page <= 1" @click="page--"><Icon name="chevron-left" :size="16" /></button><span class="qimp-page-number" aria-label="当前页">{{ page }}</span><button class="qimp-button qimp-icon-button" type="button" aria-label="下一页" :disabled="page >= pageCount" @click="page++"><Icon name="chevron-right" :size="16" /></button><label><span class="kt-visually-hidden">每页题目数</span><select v-model.number="pageSize"><option :value="10">10 条/页</option><option :value="20">20 条/页</option><option :value="50">50 条/页</option></select></label><span>共 {{ filteredRows.length }} 条</span></div>
        </div>
      </section>

      <footer v-if="preview" class="qimp-footer">
        <p><Icon name="warning" :size="23" /><span>{{ confirmation ? '如仍有未完成行，请修正后重新上传完整文件。' : '确认导入前，请再次检查题目内容与 KC 映射是否正确。' }}</span></p>
        <div><button class="qimp-button is-outline" type="button" :disabled="busy" @click="clearPreview">取消</button><button v-if="confirmation" data-return-detail class="qimp-button is-primary" type="button" @click="router.push(detailUrl)">返回题库详情</button><button v-else data-confirm-import class="qimp-button is-primary" type="button" :disabled="!canConfirm" @click="confirm">{{ busy ? '正在确认…' : '确认导入 ' + selectedIds.length + ' 道题' }}</button><button v-if="confirmation" data-confirm-import class="kt-visually-hidden" disabled type="button">本预览已处理</button></div>
      </footer>
    </div>

    <div v-if="activeRow && detailOpen && compact && mobileDetailOpen" class="qimp-detail-shade" @click="closeDetail" />
    <aside v-if="activeRow && detailOpen" ref="detailPanel" data-question-detail class="qimp-detail" :class="{ 'is-mobile-open': mobileDetailOpen }" :role="compact ? 'dialog' : undefined" :aria-modal="compact && mobileDetailOpen ? true : undefined" aria-labelledby="question-import-detail-title" @keydown="detailKeydown">
      <header><h2 id="question-import-detail-title">题目 {{ activeRow.sequence }} 详细信息</h2><button ref="closeDetailButton" class="qimp-close" type="button" aria-label="关闭题目详情" @click="closeDetail"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M6 18 18 6" /></svg></button></header>
      <div class="qimp-detail-body">
        <section><h3>题干</h3><p class="qimp-detail-value kt-question-import__multiline">{{ activeRow.text || '未读取到题干' }}</p></section>
        <section v-if="activeRow.options.length"><h3>选项</h3><ul class="qimp-options"><li v-for="option in activeRow.options" :key="option.key" :data-option="option.key" :class="{ 'is-correct': isCorrectOption(option.key) }"><span>{{ option.key }}</span><p class="kt-question-import__multiline">{{ option.text }}</p></li></ul></section>
        <section><h3>{{ activeRow.question_type === 'short_answer' ? '参考答案' : '正确答案' }}</h3><p class="qimp-detail-value kt-question-import__multiline">{{ formatValue(activeRow.answer) }}</p></section>
        <section v-if="activeRow.accepted_answers.length"><h3>可接受答案</h3><p class="qimp-detail-value kt-question-import__multiline">{{ activeRow.accepted_answers.join('；') }}</p></section>
        <section><h3>解析</h3><p class="qimp-detail-value kt-question-import__multiline">{{ activeRow.explanation || '未设置解析' }}</p></section>
        <section class="qimp-kc-detail">
          <h3>KC 映射</h3>
          <template v-if="activeRow.kc_mapping">
            <h4>主 KC</h4><div class="qimp-kc-chips"><span v-if="rootKc(activeRow)"><b>{{ rootKc(activeRow)?.kc_code }}</b>{{ rootKc(activeRow)?.name }}</span></div>
            <h4>映射的 KC <small>（共 {{ uniqueNodes(activeRow.kc_mapping).length }} 个）</small></h4>
            <div class="qimp-kc-chips"><span v-for="node in uniqueNodes(activeRow.kc_mapping)" :key="node.kc_code"><b>{{ node.kc_code }}</b>{{ node.name }}</span></div>
            <h4 class="qimp-graph-title">题目结构图</h4>
            <svg class="qimp-graph" :viewBox="'0 0 ' + graph.width + ' ' + graph.height" role="img" aria-label="题目 KC 结构图">
              <defs><marker id="question-import-arrow" markerWidth="7" markerHeight="7" refX="5" refY="3.5" orient="auto"><path d="M1 1 5 3.5 1 6" fill="none" stroke="currentColor" /></marker></defs>
              <line v-for="edge in graph.edges" :key="edge.key" :x1="edge.x1" :y1="edge.y1" :x2="edge.x2" :y2="edge.y2" marker-end="url(#question-import-arrow)" />
              <g v-for="node in graph.nodes" :key="node.kc_code" :transform="'translate(' + node.x + ' ' + node.y + ')'" :class="{ 'is-root': node.kc_code === activeRow.kc_mapping.root_kc_code }"><title>{{ node.kc_code }} {{ node.name }}</title><rect width="86" height="44" rx="21" /><text x="43" y="17" text-anchor="middle">{{ node.kc_code }}</text><text x="43" y="32" text-anchor="middle" class="qimp-graph-name">{{ node.name.length > 8 ? node.name.slice(0, 7) + '…' : node.name }}</text></g>
            </svg>
            <details class="qimp-edge-list"><summary>查看对应关系</summary><ul aria-label="KC 对应关系"><li v-for="(node, index) in activeRow.kc_mapping.nodes" :key="index">{{ kcEdgeLabel(activeRow.kc_mapping, node) }}</li></ul></details>
          </template>
          <p v-else class="qimp-detail-value">未提供 KC 映射</p>
        </section>
        <section v-if="activeRow.teacher_difficulty"><h3>难度</h3><p class="qimp-detail-value">{{ activeRow.teacher_difficulty }}</p></section>
        <section v-if="rowErrors(activeRow).length" class="qimp-detail-errors"><h3>需要修正</h3><ul><li v-for="issue in rowErrors(activeRow)" :key="issue">{{ issue }}</li></ul></section>
        <section class="qimp-source"><h3>原文件位置</h3><p>{{ activeRow.source_label }}</p></section>
      </div>
      <footer class="qimp-detail-footer"><button data-previous-question class="qimp-button" type="button" :disabled="activeIndex <= 0" @click="adjacentQuestion(-1)">上一题</button><button data-next-question class="qimp-button is-outline" type="button" :disabled="activeIndex >= rows.length - 1" @click="adjacentQuestion(1)">下一题</button></footer>
    </aside>
  </div>
</template>
