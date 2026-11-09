<script setup lang="ts">
import { questionContentSummary } from '@/question-content/question-content'
import { useBankPageUrl } from './bank-page-navigation'
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ApiError } from '@/api/client'
import Alert from '@/design-system/components/Alert.vue'
import Icon from '@/design-system/Icon.vue'
import QuestionGroupImportPreview from './QuestionGroupImportPreview.vue'
import QuestionContent from '@/question-content/QuestionContent.vue'
import {
  getTeacherBankManagementApi,
  type QuestionImportConfirmation,
  type QuestionImportPreview,
  type QuestionImportPreviewRow,
} from './api'
import './question-import.css'
import { getTeacherStandaloneQuestionBankApi, isStandaloneBankRedirect } from './standalone-api'

type PreviewFilter = 'all' | 'ready' | 'draft' | 'duplicate' | 'invalid'
type Mapping = NonNullable<QuestionImportPreviewRow['kc_mapping']>
const props = defineProps<{ standalone?: boolean }>()
const route = useRoute()
const bankPageUrl = useBankPageUrl()
const router = useRouter()
const standaloneApi = props.standalone ? getTeacherStandaloneQuestionBankApi() : undefined
const api = props.standalone ? {
  previewQuestionImport: async (_courseId: number, bankId: number, file: File, images?: File[]) => await standaloneApi!.previewQuestionImport(bankId, file, images) as QuestionImportPreview,
  confirmQuestionImport: async (_courseId: number, bankId: number, previewId: string, rows?: string[], groups?: string[]) => await standaloneApi!.confirmQuestionImport(bankId, previewId, rows, groups) as QuestionImportConfirmation,
} : getTeacherBankManagementApi()
const courseId = Number(route.params.courseId)
const bankId = Number(route.params.bankId)
const bankName = ref('正在读取题库…')
onMounted(async () => {
  try {
    if (props.standalone) {
      const result = await standaloneApi!.detail(bankId, { destination: 'question_import' })
      if (isStandaloneBankRedirect(result)) { await router.replace(bankPageUrl(result.redirect_url)); return }
      bankName.value = result.bank.name
    } else { bankName.value = (await getTeacherBankManagementApi().bankEditor(courseId, bankId)).name }
  } catch (caught) {
    bankName.value = `题库 ${bankId}`
    error.value = caught instanceof Error ? caught.message : '目标题库加载失败。'
  }
})
const fileInput = ref<HTMLInputElement>()
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
const selectedGroupIds = ref<string[]>([])
const activeRowId = ref<string>()
const detailOpen = ref(false)
const mobileDetailOpen = ref(false)
const detailPanel = ref<HTMLElement>()
const closeDetailButton = ref<HTMLButtonElement>()
let detailTrigger: HTMLElement | undefined
const error = ref('')
const busy = ref(false)

const acceptedFileTypes = computed(() => (format.value === 'docx' ? '.docx' : '.csv,.xlsx') + ',.png,.jpg,.jpeg,.webp')
const allRows = computed(() => preview.value?.preview.rows || [])
const groups = computed(() => preview.value?.preview.groups || [])
const groupedRowIds = computed(() => new Set(groups.value.flatMap(group => group.member_row_ids)))
const rows = computed<QuestionImportPreviewRow[]>(() => [
  ...allRows.value.filter(row => !groupedRowIds.value.has(row.row_id)),
  ...groups.value.map(group => ({ row_id: `group:${group.group_id}`, group_id: group.group_id,
    sequence: Math.min(...allRows.value.filter(row => group.member_row_ids.includes(row.row_id)).map(row => row.sequence)),
    title: group.title, instructions: group.instructions, text: group.material, images: group.images, question_type: group.kind,
    question_type_label: ({ reading: '阅读理解', words: '选词填空', cloze: '短文填空' })[group.kind],
    max_score: group.total_max_score, source_label: group.source_group_code, option_count: 0,
    teacher_difficulty: null, options: [], answer: `${group.item_count} 个作答项`, accepted_answers: [], explanation: '',
    kc_mapping: null, classification: group.classification, errors: group.errors })),
].sort((a, b) => a.sequence - b.sequence))
const activeGroup = computed(() => groups.value.find(group => `group:${group.group_id}` === activeRowId.value))
function selected(row: QuestionImportPreviewRow) { return row.row_id.startsWith('group:') ? selectedGroupIds.value.includes(row.group_id!) : selectedIds.value.includes(row.row_id) }
const selectedQuestionCount = computed(() => selectedIds.value.length + selectedGroupIds.value.length)
const readyLabel = computed(() => confirmation.value ? '已创建可用' : '导入后可用')
const draftLabel = computed(() => confirmation.value ? '已创建草稿' : '保存为草稿')
const confirmedActive = computed(() => confirmation.value?.active ?? confirmation.value?.created ?? 0)
const confirmedDraft = computed(() => confirmation.value?.draft ?? 0)
const outcomes = computed(() => new Map(confirmation.value?.rows.map((row) => [row.row_id, row]) || []))
const summary = computed(() => {
  const counts = { ready: 0, draft: 0, duplicate: 0, invalid: 0, unselected: 0 }
  for (const row of rows.value) counts[classification(row)] += 1
  return counts
})
const filters = computed(() => [
  { value: 'all' as const, label: '全部', count: rows.value.length },
  { value: 'ready' as const, label: readyLabel.value, count: rows.value.filter(row => classification(row) === 'ready').length },
  { value: 'draft' as const, label: draftLabel.value, count: rows.value.filter(row => classification(row) === 'draft').length },
  { value: 'duplicate' as const, label: '重复', count: rows.value.filter(row => classification(row) === 'duplicate').length },
  { value: 'invalid' as const, label: '需修正', count: rows.value.filter(row => classification(row) === 'invalid').length },
])
const questionTypes = computed(() => [...new Map(rows.value.map((row) => [row.question_type, row.question_type_label])).entries()])
const filteredRows = computed(() => rows.value.filter((row) => {
  const query = search.value.trim().toLocaleLowerCase()
  return (filter.value === 'all' || classification(row) === filter.value)
    && (!questionType.value || row.question_type === questionType.value)
    && (!query || (String(row.sequence) + ' ' + (row.title || '') + ' ' + row.text).toLocaleLowerCase().includes(query))
}))
const pageCount = computed(() => Math.max(1, Math.ceil(filteredRows.value.length / pageSize.value)))
const pageRows = computed(() => filteredRows.value.slice((page.value - 1) * pageSize.value, page.value * pageSize.value))
const selectablePageRows = computed(() => pageRows.value.filter(isSelectable))
const pageSelected = computed(() => selectablePageRows.value.length > 0 && selectablePageRows.value.every((row) => selected(row)))
const pagePartlySelected = computed(() => !pageSelected.value && selectablePageRows.value.some((row) => selected(row)))
const canConfirm = computed(() => Boolean(preview.value?.preview.confirmable && selectedQuestionCount.value && !confirmation.value && !busy.value))
const activeRow = computed(() => rows.value.find((row) => row.row_id === activeRowId.value))
const activeIndex = computed(() => rows.value.findIndex((row) => row.row_id === activeRowId.value))
const detailUrl = computed(() => confirmation.value?.detail_url || (props.standalone ? `/teacher/banks/${bankId}/` : `/teacher/courses/${courseId}/banks/${bankId}/`))
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

function groupResult(row: QuestionImportPreviewRow) {
  if (!confirmation.value || !row.row_id.startsWith('group:')) return undefined
  const group = groups.value.find(group => group.group_id === row.group_id)
  const results = group?.member_row_ids.map(id => outcomes.value.get(id)).filter(item => item !== undefined) || []
  if (!results.length) return undefined
  const errors = results.flatMap(item => item.status === 'created' || item.status === 'updated' ? item.draft_reasons || [] : item.errors || [])
  const kind = results.some(item => item.status === 'invalid') ? 'invalid' : results.some(item => item.status === 'unselected') ? 'unselected' : results.every(item => item.status === 'duplicate') ? 'duplicate' : results.some(item => (item.status === 'created' || item.status === 'updated') && item.question_status === 'draft') ? 'draft' : 'ready'
  return { kind, errors }
}
function classification(row: QuestionImportPreviewRow) {
  const result = groupResult(row)
  if (result) return result.kind as 'ready' | 'draft' | 'duplicate' | 'invalid' | 'unselected'
  const outcome = outcomes.value.get(row.row_id)
  if (outcome?.status === 'created' || outcome?.status === 'updated') return outcome.question_status === 'draft' ? 'draft' : 'ready'
  return outcome?.status || row.classification
}
function isSelectable(row: QuestionImportPreviewRow) {
  return row.classification === 'ready' || row.classification === 'draft'
}
function rowStatus(row: QuestionImportPreviewRow) {
  const result = groupResult(row)
  if (result) return { label: ({ ready: '已创建可用', draft: '已保存为草稿', invalid: '需修正', duplicate: '重复', unselected: '未选择' })[result.kind as 'ready'], tone: result.kind === 'ready' ? 'success' : result.kind === 'invalid' ? 'danger' : 'warning' }

  const outcome = outcomes.value.get(row.row_id)
  const status = outcome?.status || row.classification
  if (outcome?.status === 'created' && outcome.question_status === 'draft') return { label: '已保存为草稿', tone: 'warning' }
  if (outcome?.status === 'updated' && outcome.question_status === 'draft') return { label: '已更新草稿', tone: 'warning' }
  if (outcome?.status === 'updated') return { label: '已修复为可用', tone: 'success' }
  if (status === 'created') return { label: '已创建可用', tone: 'success' }
  if (status === 'ready') return { label: '导入后可用', tone: 'success' }
  if (status === 'draft') return { label: '保存为草稿', tone: 'warning' }
  if (status === 'duplicate') return { label: '重复', tone: 'warning' }
  if (status === 'unselected') return { label: '未选择', tone: 'neutral' }
  return { label: '需修正', tone: 'danger' }
}
function rowErrors(row: QuestionImportPreviewRow) {
  const result = groupResult(row)
  if (result) return result.errors
  const outcome = outcomes.value.get(row.row_id)
  if ((outcome?.status === 'created' || outcome?.status === 'updated') && outcome.question_status === 'draft') return outcome.draft_reasons
  return outcome?.errors ?? row.errors
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
    ...node, x: 2 + depth * 172, y: (index + .5) * height / column.length - 28,
  })))
  const edges = mapping.nodes.filter((node) => node.parent_kc_code).flatMap((node) => {
    const start = positioned.find((candidate) => candidate.kc_code === node.parent_kc_code)
    const end = positioned.find((candidate) => candidate.kc_code === node.kc_code)
    return start && end ? [{ key: start.kc_code + '>' + end.kc_code, x1: start.x + 140, y1: start.y + 28, x2: end.x - 4, y2: end.y + 28 }] : []
  })
  return { width: Math.max(320, (maxDepth + 1) * 172 - 28), height, nodes: positioned, edges }
})
function isCorrectOption(key: string) {
  const answer = activeRow.value?.answer
  return Array.isArray(answer) ? answer.includes(key) : answer === key
}
function toggleRow(rowId: string, checked: boolean) {
  if (rowId.startsWith('group:')) {
    const id = rowId.slice(6)
    selectedGroupIds.value = checked ? [...new Set([...selectedGroupIds.value, id])] : selectedGroupIds.value.filter(value => value !== id)
    return
  }
  selectedIds.value = checked ? [...new Set([...selectedIds.value, rowId])] : selectedIds.value.filter((id) => id !== rowId)
}
function togglePage(checked: boolean) {
  for (const row of selectablePageRows.value) toggleRow(row.row_id, checked)
}
function clearPreview() {
  preview.value = undefined
  confirmation.value = undefined
  uploadedFile.value = undefined
  selectedIds.value = []
  selectedGroupIds.value = []
  activeRowId.value = undefined
  filter.value = 'all'
  search.value = ''
  questionType.value = ''
  page.value = 1
}
async function selectFile(event: Event) {
  const input = event.currentTarget as HTMLInputElement
  const files = Array.from(input.files || [])
  if (!files.length) return
  clearPreview()
  const documents = files.filter(file => /\.(docx|xlsx|csv)$/i.test(file.name))
  if (documents.length !== 1 || files.some(file => !/\.(docx|xlsx|csv|png|jpe?g|webp)$/i.test(file.name))) {
    error.value = '请选择一个 Word、Excel 或 CSV 题库文件，可同时选择其中引用的 PNG、JPG 或 WebP 图片。'
    input.value = ''
    return
  }
  const file = documents[0]!
  const images = files.filter(candidate => candidate !== file)
  busy.value = true
  error.value = ''
  try {
    preview.value = images.length ? await api.previewQuestionImport(courseId, bankId, file, images) : await api.previewQuestionImport(courseId, bankId, file)
    uploadedFile.value = { name: file.name, size: file.size, uploadedAt: new Date() }
    selectedIds.value = rows.value.filter(row => !row.row_id.startsWith('group:') && isSelectable(row)).map((row) => row.row_id)
    selectedGroupIds.value = groups.value.filter(group => ['ready', 'draft'].includes(group.classification)).map(group => group.group_id)
    activeRowId.value = rows.value[0]?.row_id
    detailOpen.value = false
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
    confirmation.value = groups.value.length
      ? await api.confirmQuestionImport(courseId, bankId, preview.value.preview_id, selectedIds.value, selectedGroupIds.value)
      : await api.confirmQuestionImport(courseId, bankId, preview.value.preview_id, selectedIds.value)
    if (props.standalone) await router.push(bankPageUrl(confirmation.value.detail_url))
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
  {
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
  if (event.key !== 'Tab') return
  const controls = [...(detailPanel.value?.querySelectorAll<HTMLElement>('button:not([disabled]), a[href], summary, [tabindex="0"]') || [])]
  const first = controls[0]
  const last = controls.at(-1)
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
}
watch([filter, search, questionType, pageSize], () => { page.value = 1 })
</script>

<template>
  <div class="kt-question-import" :class="{ 'has-detail': activeRow && detailOpen, 'is-mobile-inspecting': mobileDetailOpen && detailOpen }">
    <div class="qimp-main" :inert="detailOpen">
      <nav class="qimp-breadcrumb" aria-label="层级目录">
        <RouterLink :to="bankPageUrl(detailUrl)">题库</RouterLink><Icon name="chevron-right" :size="14" /><span>导入题库</span>
      </nav>
      <header class="qimp-heading">
        <div class="qimp-heading-row"><h1>导入题目</h1><RouterLink class="kt-button kt-button--secondary" :to="bankPageUrl(detailUrl)">返回题库</RouterLink></div>
        <p>上传完成后请仔细核对题干、选项、答案与 KC 映射，确认无误后导入系统。</p>
      </header>
      <Alert v-if="error" tone="danger">{{ error }}</Alert>

      <div class="kt-import-flow"><span>01 选择文件</span><span>→</span><span>02 校验预览</span><span>→</span><span>03 确认导入</span></div><section class="qimp-file kt-site-import-upload" aria-label="题库文件">
        <div class="kt-site-import-controls"><label>目标题库<input :value="bankName" aria-label="目标题库" readonly></label><label>文件格式<select id="question-import-format" v-model="format"><option value="spreadsheet">Excel / CSV</option><option value="docx">Word .docx</option></select></label><button class="qimp-button is-primary" type="button" :disabled="busy" @click="fileInput?.click()">{{ busy ? '正在校验…' : preview ? '重新选择文件' : '选择文件并预览' }}</button></div>
        <div class="kt-site-import-templates"><span>下载全题型模板：</span><a data-question-template="xlsx" href="/api/v1/teacher/import-templates/question-groups.xlsx" download="question_import_template.xlsx">Excel</a><a data-question-template="csv" href="/api/v1/teacher/import-templates/question-groups.csv" download="question_import_template.csv">CSV</a><a data-question-template="docx" href="/api/v1/teacher/import-templates/question-groups.docx" download="question_import_template.docx">Word</a><span>11 种题型 · 12 道完整题目 · 22 个作答项</span><span v-if="uploadedFile" data-file-summary>{{ uploadedFile.name }} · {{ fileSize }} · {{ uploadTime }}</span></div>
        <p class="kt-picker-note">可同时选择一个题库文件及其中引用的图片。CSV 使用 <code v-pre>{{image:文件名.png}}</code> 引用同名图片。</p>
        <a href="/api/v1/teacher/import-templates/question-images.zip" download>下载配套图片示例 ZIP</a>
        <input ref="fileInput" class="kt-visually-hidden" type="file" multiple :accept="acceptedFileTypes" aria-label="上传题库文件" @change="selectFile">
      </section>

      <section v-if="!preview" data-import-empty class="qimp-empty-import"><h2>选择文件后先检查内容</h2><p>按完整题目选择；阅读小题与文章空位作为同一道题一并导入。<br>可用与草稿允许选择，重复和错误不会重复入库。</p></section>
      <Alert v-if="confirmation" :tone="confirmation.failed ? 'warning' : 'success'">
        <strong>本次导入已完成。</strong>新增 {{ confirmation.created }} 道<span v-if="confirmation.updated">，修复 {{ confirmation.updated }} 道</span>，其中可用 {{ confirmedActive }} 道、草稿 {{ confirmedDraft }} 道；跳过 {{ confirmation.skipped }} 道，未完成 {{ confirmation.failed }} 道。
        <span v-if="summary.unselected">另有 {{ summary.unselected }} 道未选择。</span><span v-if="confirmation.failed">未完成的导入行保留在下方，修正后可重新上传完整文件。</span><span v-else-if="confirmedDraft">草稿已成功保存，可在题库中继续补全。</span>
      </Alert>

      <section v-if="preview && rows.length" data-import-preview class="qimp-preview" aria-label="导入预览">
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
            <colgroup><col class="qimp-col-check"><col class="qimp-col-number"><col class="qimp-col-title"><col class="qimp-col-text"><col class="qimp-col-type"><col class="qimp-col-answer"><col class="qimp-col-kc"><col class="qimp-col-status"><col class="qimp-col-action"></colgroup>
            <thead><tr><th><input type="checkbox" aria-label="选择本页可导入题目" :checked="pageSelected" :indeterminate="pagePartlySelected" :disabled="Boolean(confirmation) || busy || !selectablePageRows.length" @change="togglePage(($event.target as HTMLInputElement).checked)"></th><th title="文件内预览题号，正式题号在导入后分配">题号</th><th>标题</th><th>题干</th><th>题型</th><th>答案</th><th>KC 映射</th><th>状态</th><th>操作</th></tr></thead>
            <tbody>
              <tr v-for="row in pageRows" :key="row.row_id" :data-import-row="row.row_id" :class="{ 'is-current': activeRowId === row.row_id && detailOpen }">
                <td><input type="checkbox" :aria-label="'选择题目 ' + row.sequence" :checked="selected(row)" :disabled="!isSelectable(row) || Boolean(confirmation) || busy" @change="toggleRow(row.row_id, ($event.target as HTMLInputElement).checked)"></td>
                <td>{{ row.sequence }}</td>
                <td data-question-title>{{ questionContentSummary(row.title || '—') }}</td>
                <td><p class="qimp-question-text kt-question-import__multiline">{{ questionContentSummary(row.text || '未读取到题干') }}</p></td>
                <td>{{ row.question_type_label }}</td>
                <td data-answer><span class="qimp-answer-text">{{ questionContentSummary(formatValue(row.answer)) }}</span></td>
                <td><p v-if="row.group_id && row.row_id.startsWith('group:')">成员 KC 在详情中逐项核对</p><p v-if="rootKc(row)">主：{{ rootKc(row)?.name }}（{{ rootKc(row)?.kc_code }}）</p><p class="qimp-kc-summary"><Icon name="definition" :size="15" />{{ kcSummary(row) }}</p></td>
                <td><span class="qimp-status" :class="'is-' + rowStatus(row).tone">{{ rowStatus(row).label }}</span><p v-if="rowErrors(row).length" class="kt-question-import__errors" :class="{ 'is-draft-reasons': classification(row) === 'draft' }">{{ rowErrors(row).join('；') }}</p></td>
                <td><button class="qimp-link" type="button" :aria-label="'查看题目 ' + row.sequence" @click="inspectRow(row, $event)">查看</button></td>
              </tr>
            </tbody>
          </table>
        </div>
        <ul v-if="pageRows.length" class="qimp-mobile-list kt-mobile-only">
          <li v-for="row in pageRows" :key="row.row_id" :data-import-mobile-row="row.row_id" class="qimp-mobile-card">
            <div class="qimp-mobile-heading"><label><input type="checkbox" :aria-label="'选择题目 ' + row.sequence" :checked="selected(row)" :disabled="!isSelectable(row) || Boolean(confirmation) || busy" @change="toggleRow(row.row_id, ($event.target as HTMLInputElement).checked)">题目 {{ row.sequence }}</label><span class="qimp-status" :class="'is-' + rowStatus(row).tone">{{ rowStatus(row).label }}</span></div>
            <p data-question-title>标题：{{ questionContentSummary(row.title || '—') }}</p>
            <h3 class="kt-question-import__multiline">{{ questionContentSummary(row.text || '未读取到题干') }}</h3>
            <dl><div><dt>题型</dt><dd>{{ row.question_type_label }}</dd></div><div><dt>答案</dt><dd>{{ questionContentSummary(formatValue(row.answer)) }}</dd></div></dl>
            <p v-if="rootKc(row)" class="qimp-mobile-kc">主：{{ rootKc(row)?.name }}（{{ rootKc(row)?.kc_code }}）</p><p class="qimp-kc-summary"><Icon name="definition" :size="15" />{{ kcSummary(row) }}</p>
            <p v-if="rowErrors(row).length" class="kt-question-import__errors" :class="{ 'is-draft-reasons': classification(row) === 'draft' }">{{ rowErrors(row).join('；') }}</p>
            <button class="qimp-link" type="button" :aria-label="'查看题目 ' + row.sequence" @click="inspectRow(row, $event)">查看完整内容与 KC 映射 <Icon name="chevron-right" :size="14" /></button>
          </li>
        </ul>
        <p v-else class="qimp-empty" role="status">当前筛选没有预览行。</p>
        <div class="qimp-pagination">
          <span>已选择 <strong>{{ selectedQuestionCount }}</strong> 道完整题</span>
          <div class="qimp-pagination-controls"><button class="qimp-button qimp-icon-button" type="button" aria-label="上一页" :disabled="page <= 1" @click="page--"><Icon name="chevron-left" :size="16" /></button><span class="qimp-page-number" aria-label="当前页">{{ page }}</span><button class="qimp-button qimp-icon-button" type="button" aria-label="下一页" :disabled="page >= pageCount" @click="page++"><Icon name="chevron-right" :size="16" /></button><label><span class="kt-visually-hidden">每页题目数</span><select v-model.number="pageSize"><option :value="10">10 条/页</option><option :value="20">20 条/页</option><option :value="50">50 条/页</option></select></label><span>共 {{ filteredRows.length }} 条</span></div>
        </div>
      </section>

      <footer v-if="preview" class="qimp-footer">
        <p><Icon name="warning" :size="23" /><span>{{ confirmation ? confirmation.failed ? '如仍有未完成行，请修正后重新上传完整文件。' : confirmedDraft ? '草稿可在题库中继续补全，保存后会自动转为可用。' : '全部所选题目已导入为可用题目。' : '确认导入前，请再次检查题目内容与 KC 映射是否正确。' }}</span></p>
        <div><button class="qimp-button is-outline" type="button" :disabled="busy" @click="clearPreview">取消</button><button v-if="confirmation" data-return-detail class="qimp-button is-primary" type="button" @click="router.push(bankPageUrl(detailUrl))">返回题库详情</button><button v-else data-confirm-import :data-confirm-standalone-import="standalone ? '' : undefined" class="qimp-button is-primary" type="button" :disabled="!canConfirm" @click="confirm">{{ busy ? '正在确认…' : '确认导入 ' + selectedQuestionCount + ' 道题' }}</button><button v-if="confirmation" data-confirm-import class="kt-visually-hidden" disabled type="button">本预览已处理</button></div>
      </footer>
    </div>

    <div v-if="activeRow && detailOpen" class="qimp-detail-shade" @click="closeDetail" />
    <aside v-if="activeRow && detailOpen" ref="detailPanel" data-question-detail class="qimp-detail" :class="{ 'is-mobile-open': mobileDetailOpen }" role="dialog" aria-modal="true" aria-labelledby="question-import-detail-title" @keydown="detailKeydown">
      <header><h2 id="question-import-detail-title">题目 {{ activeRow.sequence }} 详细信息</h2><button ref="closeDetailButton" class="qimp-close" type="button" aria-label="关闭题目详情" @click="closeDetail"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M6 18 18 6" /></svg></button></header>
      <div class="qimp-detail-body">
        <QuestionGroupImportPreview v-if="activeGroup" v-model="selectedGroupIds" :groups="[{ ...activeGroup, errors: rowErrors(activeRow) }]" :rows="allRows.map(row => ({ ...row, errors: rowErrors(row) }))" :disabled="busy || Boolean(confirmation)" detail-only /><template v-else>
          <section v-if="activeRow.title"><h3>标题</h3><p>{{ activeRow.title }}</p></section>
          <section v-if="activeRow.instructions"><h3>作答说明</h3><p class="kt-question-import__multiline">{{ activeRow.instructions }}</p></section>
          <section v-if="activeRow.question_type === 'numeric'"><h3>误差容限</h3><p>绝对：{{ activeRow.absolute_tolerance ?? '未设置' }} · 相对：{{ activeRow.relative_tolerance ?? '未设置' }}</p></section>
          <section><h3>题干</h3><p class="qimp-detail-value kt-question-import__multiline"><QuestionContent :text="activeRow.text || '未读取到题干'" :images="activeRow.images" /></p></section>
          <section v-if="activeRow.options.length"><h3>选项</h3><ul class="qimp-options"><li v-for="option in activeRow.options" :key="option.key" :data-option="option.key" :class="{ 'is-correct': isCorrectOption(option.key) }"><span>{{ option.key }}</span><p class="kt-question-import__multiline"><QuestionContent :text="option.text" :images="activeRow.images" option /></p></li></ul></section>
          <section><h3>{{ activeRow.question_type === 'short_answer' ? '参考答案' : '正确答案' }}</h3><p class="qimp-detail-value kt-question-import__multiline"><QuestionContent :text="formatValue(activeRow.answer)" :images="activeRow.images" /></p></section>
          <section v-if="activeRow.accepted_answers.length"><h3>可接受答案</h3><p class="qimp-detail-value kt-question-import__multiline">{{ activeRow.accepted_answers.join('；') }}</p></section>
          <section><h3>解析</h3><p class="qimp-detail-value kt-question-import__multiline"><QuestionContent :text="activeRow.explanation || '未设置解析'" :images="activeRow.images" /></p></section>
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
                <g v-for="node in graph.nodes" :key="node.kc_code" :transform="'translate(' + node.x + ' ' + node.y + ')'" :class="{ 'is-root': node.kc_code === activeRow.kc_mapping.root_kc_code }"><title>{{ node.kc_code }} {{ node.name }}</title><rect width="140" height="56" rx="8" /><text x="70" y="22" text-anchor="middle">{{ node.kc_code }}</text><text x="70" y="43" text-anchor="middle" class="qimp-graph-name">{{ node.name.length > 8 ? node.name.slice(0, 7) + '…' : node.name }}</text></g>
              </svg>
              <details class="qimp-edge-list"><summary>查看对应关系</summary><ul aria-label="KC 对应关系"><li v-for="(node, index) in activeRow.kc_mapping.nodes" :key="index">{{ kcEdgeLabel(activeRow.kc_mapping, node) }}</li></ul></details>
            </template>
            <p v-else class="qimp-detail-value">未提供 KC 映射</p>
          </section>
          <section v-if="activeRow.teacher_difficulty"><h3>难度</h3><p class="qimp-detail-value">{{ activeRow.teacher_difficulty }}</p></section>
          <section v-if="rowErrors(activeRow).length" class="qimp-detail-errors" :class="{ 'is-draft-reasons': classification(activeRow) === 'draft' }"><h3>{{ classification(activeRow) === 'draft' ? '草稿原因' : '需要修正' }}</h3><ul><li v-for="issue in rowErrors(activeRow)" :key="issue">{{ issue }}</li></ul></section>
          <section class="qimp-source"><h3>原文件位置</h3><p>{{ activeRow.source_label }}</p></section>
        </template>
      </div>
      <footer class="qimp-detail-footer"><button data-previous-question class="qimp-button" type="button" :disabled="activeIndex <= 0" @click="adjacentQuestion(-1)">上一题</button><button data-next-question class="qimp-button is-outline" type="button" :disabled="activeIndex >= rows.length - 1" @click="adjacentQuestion(1)">下一题</button></footer>
    </aside>
  </div>
</template>
