<script setup lang="ts">
import { computed, inject, onBeforeUnmount, ref, watch, type Ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { getAuthenticationGeneration } from '@/app/bootstrap'
import { ApiError } from '@/api/client'

import Alert from '@/design-system/components/Alert.vue'
import EmptyState from '@/design-system/components/EmptyState.vue'
import Paginator from '@/design-system/components/Paginator.vue'

import ResearchDataIcon from './ResearchDataIcon.vue'
import ResearchBehaviorCompactTable from './ResearchBehaviorCompactTable.vue'
import ResearchBehaviorTable from './ResearchBehaviorTable.vue'
import ResearchCards from './ResearchCards.vue'
import ResearchPageHeading from './ResearchPageHeading.vue'
import ResearchQuestionSummary from './ResearchQuestionSummary.vue'
import ResearchResponsesResultTable from './ResearchResponsesResultTable.vue'
import ResearchResponsesTable from './ResearchResponsesTable.vue'
import ResearchSessionSummary from './ResearchSessionSummary.vue'
import {
  getTeacherDataCenterApi,
  routeQuery,
  type DataCenterAdvanced,
  type DataQuery,
  type ResearchSession,
  type ResearchStudent,
  type ResearchPreparation,
} from './api'

const route = useRoute()
const router = useRouter()
const courseId = computed(() => Number(route.params.courseId))
const api = getTeacherDataCenterApi()
const scopeRefresh = inject<Ref<number>>('teacher-data-scope-refresh', ref(0))

const data = ref<DataCenterAdvanced>()
const error = ref('')
const cursorNotice = ref('')
const loading = ref(false)
const studentSearch = ref('')
const showSuggestions = ref(false)
const pendingStudent = ref<ResearchStudent | null | undefined>(undefined)
const remoteStudents = ref<ResearchStudent[]>([])
const studentSearchLoading = ref(false)
const studentSearchError = ref('')
let latestRequest = 0
let latestStudentRequest = 0
let dataController: AbortController | undefined
let studentController: AbortController | undefined
let studentTimer: ReturnType<typeof setTimeout> | undefined
let pollTimer: ReturnType<typeof setTimeout> | undefined
let disposed = false

const POLL_DELAYS = [5, 15, 30]
let pollAttempts = 0
let pollStartedAt: number | undefined
let pollingStopped = false
let loadedScope = ''
let loadedVersion: string | null | undefined
let dataInflight: { key: string; request: number; promise: Promise<void> } | undefined
let statusInflight: { key: string; request: number; promise: Promise<void> } | undefined
let statusController: AbortController | undefined
let latestStatusRequest = 0
const pages = new Map<string, DataCenterAdvanced>()
const preparationSnapshots = new Map<string, ResearchPreparation>()
let intent: { key: string; generation: number; query: DataQuery; courseId: number; reloaded: boolean } | undefined
let generation = 0

function queryKey(query: DataQuery) {
  return JSON.stringify(Object.entries(query).map(([key, raw]) => [key, Array.isArray(raw) ? raw[0] : raw])
    .filter(([, value]) => value != null && value !== '').map(([key, value]) => [String(key), String(value)] as const)
    .sort(([a], [b]) => a.localeCompare(b)))
}

function currentIntent(captured: NonNullable<typeof intent>) {
  return !disposed && captured.generation === intent?.generation
    && captured.key === `${getAuthenticationGeneration()}:${courseId.value}:` + queryKey(routeQuery(route.query))
}

function clearPages() {
  pages.clear()
  preparationSnapshots.clear()
}

function cancelStudentSearch() {
  latestStudentRequest += 1
  if (studentTimer) clearTimeout(studentTimer)
  studentTimer = undefined
  studentController?.abort()
  studentSearchLoading.value = false
  studentSearchError.value = ''
  remoteStudents.value = []
}

function enterIntent(query: DataQuery) {
  const namespace = `${getAuthenticationGeneration()}:${courseId.value}:`
  const key = namespace + queryKey(query)
  if (intent?.key === key) return intent
  latestRequest += 1
  latestStatusRequest += 1
  dataController?.abort()
  statusController?.abort()
  dataInflight = undefined
  statusInflight = undefined
  cancelStudentSearch()
  if (pollTimer) clearTimeout(pollTimer)
  pollTimer = undefined
  pollAttempts = 0
  pollStartedAt = undefined
  pollingStopped = false
  const scope = namespace + scopeKey(query)
  if (loadedScope !== scope) {
    loadedScope = scope
    loadedVersion = undefined
    clearPages()
    data.value = undefined
    pendingStudent.value = undefined
    studentSearch.value = ''
  }
  loading.value = false
  error.value = ''
  intent = { key, query: { ...query }, generation: ++generation, courseId: courseId.value, reloaded: false }
  return intent
}

function scopeKey(query: DataQuery) {
  return queryKey(Object.fromEntries(Object.entries(query).filter(([key]) => !['page', 'cursor', 'table'].includes(key))))
}

function stopPolling() {
  pollingStopped = true
  if (pollTimer) clearTimeout(pollTimer)
  pollTimer = undefined
}

function visibilityChanged() {
  if (document.visibilityState !== 'visible') {
    stopPolling()
    latestStatusRequest += 1
    statusController?.abort()
  }
}

function schedulePoll() {
  if (pollTimer) clearTimeout(pollTimer)
  pollTimer = undefined
  const preparation = data.value?.preparation
  if (disposed || pollingStopped || document.visibilityState !== 'visible' || error.value) return
  if (preparation?.state !== 'preparing' || preparation.ready_count !== 0 || pollAttempts >= POLL_DELAYS.length) return
  pollStartedAt ??= Date.now()
  const delay = Math.max(0, POLL_DELAYS[pollAttempts]! * 1000 - (Date.now() - pollStartedAt))
  pollTimer = setTimeout(() => { pollAttempts += 1; void checkStatus() }, delay)
}

document.addEventListener('visibilitychange', visibilityChanged)

const cardCopy: Record<string, { label: string; description: string; icon: string }> = {
  sessions: { label: '会话', description: '一名学生参加实验的一次会话', icon: 'session' },
  questions: { label: '题目', description: '课程题目固定库，详情页展示当前范围相关题目', icon: 'question' },
  knowledge_points: { label: '知识点', description: '课程知识点库，详情页展示当前范围相关知识点', icon: 'knowledge' },
  question_kc_mapping: { label: '题目和知识点映射', description: '题目与知识点的明确对应关系', icon: 'mapping' },
  responses: { label: '逐题作答', description: '某会话中学生对一道题的最终作答事实', icon: 'response' },
  behavior_events: { label: '行为过程', description: '一次已记录的研究行为或状态变化', icon: 'behavior' },
}

const view = computed(() => data.value?.view || route.query.view || 'overview')
const workspace = computed(() => data.value?.workspace)
const routeStudentId = computed(() => Number(route.query.student_id || 0))
const selectedStudent = computed(() =>
  pendingStudent.value !== undefined
    ? pendingStudent.value
    : workspace.value?.student || workspace.value?.students.find(student => student.id === routeStudentId.value) || null,
)
const selectedSession = computed(() => {
  const ref = typeof route.query.session_ref === 'string' ? route.query.session_ref : ''
  return workspace.value?.sessions.find(session => session.session_ref === ref) || workspace.value?.session || workspace.value?.sessions[0] || null
})
const selectedQuestion = computed(() => {
  const ref = typeof route.query.question_ref === 'string' ? route.query.question_ref : ''
  return workspace.value?.question || workspace.value?.responses.find(item => item.question_ref === ref) || workspace.value?.responses[0] || null
})
const selectedVisit = computed(() => {
  const key = typeof route.query.visit === 'string' ? route.query.visit : workspace.value?.selected_visit
  return workspace.value?.visits.find(item => item.key === key) || workspace.value?.visits[0] || null
})
const suggestions = computed(() => {
  const search = studentSearch.value.trim().toLowerCase()
  if (!search || !workspace.value) return []
  const candidates = remoteStudents.value.length ? remoteStudents.value : workspace.value.students
  return candidates.filter(student => {
    const label = studentLabel(student).toLowerCase()
    return label.includes(search) && label !== search
  }).slice(0, 20)
})
const activeCardKey = computed(() => {
  if (view.value === 'question' || view.value === 'timeline') return 'behavior_events'
  if (view.value === 'session' || view.value === 'student') return 'responses'
  return data.value?.selected_table.key || 'sessions'
})

function studentLabel(student: ResearchStudent) {
  return student.name + ' / ' + student.student_number
}

function displayValue(value: unknown) {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'boolean') return value ? '是' : '否'
  return String(value)
}

function isLongText(value: unknown) {
  const text = displayValue(value)
  return text.length > 48 || text.includes('\n')
}

function compactText(value: unknown) {
  const text = displayValue(value).replace(/\s+/g, ' ')
  return text.length > 36 ? text.slice(0, 36) + '…' : text
}

function formatTime(value: string) {
  if (!value) return '未记录'
  const compact = value.match(/^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})(\.\d+)?$/)
  if (compact) return compact[1] + '-' + compact[2] + '-' + compact[3] + ' ' + compact[4] + ':' + compact[5] + ':' + compact[6] + (compact[7] || '')
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  }).format(date).replaceAll('/', '-')
}

function formatDuration(value: number | null) {
  if (value === null) return '未记录'
  const minutes = Math.floor(value / 60)
  const seconds = Math.round(value % 60)
  return minutes ? minutes + '分' + seconds + '秒' : seconds + '秒'
}

function cleanQuery(overrides: Record<string, string | number | undefined>) {
  return { ...route.query, ...overrides }
}

async function go(overrides: Record<string, string | number | undefined>) {
  await router.push({ query: cleanQuery(overrides) })
}

function applyData(result: DataCenterAdvanced) {
  data.value = result
  loadedVersion = result.preparation?.result_version
  if (pendingStudent.value !== undefined) studentSearch.value = pendingStudent.value ? studentLabel(pendingStudent.value) : studentSearch.value
  else {
    const id = Number(route.query.student_id || 0)
    const student = result.workspace.student || result.workspace.students.find(item => item.id === id)
    studentSearch.value = student ? studentLabel(student) : ''
  }
}

function load(query = routeQuery(route.query), force = false): Promise<void> {
  const captured = enterIntent(query)
  const key = captured.key
  if (dataInflight?.key === key) return dataInflight.promise
  if (pollTimer) clearTimeout(pollTimer)
  pollTimer = undefined
  const cached = pages.get(key)
  if (!force && cached && (query.cursor ? !!cached.preview?.cursor?.result_version : cached.preparation?.result_version === loadedVersion)) {
    pages.delete(key)
    pages.set(key, cached)
    applyData(preparationSnapshots.has(key) ? { ...cached, preparation: preparationSnapshots.get(key) } : cached)
    loading.value = false
    schedulePoll()
    return Promise.resolve()
  }
  const request = ++latestRequest
  dataController?.abort()
  dataController = new AbortController()
  const signal = dataController.signal
  error.value = ''
  loading.value = true
  const promise = (async () => {
    try {
      const result = await api.advanced(captured.courseId, captured.query, signal)
      if (!currentIntent(captured) || request !== latestRequest) return
      if (loadedVersion && result.preparation?.result_version !== loadedVersion && Number(query.page || 1) > 1 && !query.cursor) {
        clearPages()
        loadedVersion = result.preparation?.result_version
        await go({ page: undefined, cursor: undefined })
        return
      }
      if (loadedVersion !== result.preparation?.result_version && !query.cursor) clearPages()
      preparationSnapshots.delete(key)
      applyData(result)
      pages.set(key, result)
      if (pages.size > 8) {
        const oldest = pages.keys().next().value!
        pages.delete(oldest)
        preparationSnapshots.delete(oldest)
      }
    } catch (caught) {
      if (currentIntent(captured) && request === latestRequest && query.cursor
          && caught instanceof ApiError && caught.code === 'BEHAVIOR_CURSOR_VERSION_CHANGED') {
        cursorNotice.value = '行为分页版本已更新，已返回第一页，请重新选择分页。'
        await go({ cursor: undefined, page: undefined })
        return
      }
      if (currentIntent(captured) && request === latestRequest && !isAbortError(caught)) error.value = caught instanceof Error ? caught.message : '研究数据预览加载失败。'
    } finally {
      if (currentIntent(captured) && request === latestRequest) {
        loading.value = false
        schedulePoll()
      }
      if (dataInflight?.request === request) dataInflight = undefined
    }
  })()
  dataInflight = { key, request, promise }
  return promise
}

function checkStatus(manual = false): Promise<void> {
  const query = routeQuery(route.query)
  const captured = enterIntent(query)
  const key = captured.key
  if (manual && ['questions', 'knowledge_points', 'question_kc_mapping'].includes(String(query.table || data.value?.selected_table.key))) {
    for (const [cacheKey, page] of pages) {
      if (page.course.id === captured.courseId && ['questions', 'knowledge_points', 'question_kc_mapping'].includes(page.selected_table.key)) {
        pages.delete(cacheKey)
        preparationSnapshots.delete(cacheKey)
      }
    }
    return load(query, true)
  }
  if (statusInflight?.key === key) return statusInflight.promise
  if (!data.value?.preparation) return manual ? load(query, true) : Promise.resolve()
  const request = ++latestStatusRequest
  statusController?.abort()
  statusController = new AbortController()
  const signal = statusController.signal
  error.value = ''
  const promise = (async () => {
    try {
      const result = await api.advancedStatus(captured.courseId, captured.query, signal)
      if (!currentIntent(captured) || request !== latestStatusRequest || (!manual && pollingStopped)) return
      const hadReadable = !!loadedVersion && (data.value?.preparation?.ready_count || 0) > 0
      preparationSnapshots.set(key, result.preparation)
      if (data.value) data.value = { ...data.value, preparation: result.preparation }
      const firstReadable = !hadReadable && result.preparation.ready_count > 0 && !!result.preparation.result_version && !captured.reloaded
      if ((manual && result.preparation.result_version !== loadedVersion) || (!manual && firstReadable)) {
        captured.reloaded = true
        stopPolling()
        clearPages()
        if (Number(query.page || 1) > 1 || query.cursor) {
          loadedVersion = result.preparation.result_version
          await go({ page: undefined, cursor: undefined })
        } else await load(query, true)
      }
      if (result.preparation.state === 'failed' || result.preparation.ready_count > 0) stopPolling()
    } catch (caught) {
      if (currentIntent(captured) && request === latestStatusRequest && !isAbortError(caught)) {
        error.value = caught instanceof Error ? caught.message : '研究数据状态查询失败。'
        stopPolling()
      }
    } finally {
      if (statusInflight?.request === request) statusInflight = undefined
      if (currentIntent(captured) && request === latestStatusRequest && !manual) schedulePoll()
    }
  })()
  statusInflight = { key, request, promise }
  return promise
}

function isAbortError(caught: unknown) {
  return caught instanceof Error && caught.name === 'AbortError'
}

async function searchStudents(search: string) {
  const captured = intent
  if (!captured) return
  const request = ++latestStudentRequest
  studentController?.abort()
  studentController = new AbortController()
  studentSearchError.value = ''
  studentSearchLoading.value = true
  try {
    const result = await api.advancedStudents(
      captured.courseId,
      { ...captured.query, q: search },
      studentController.signal,
    )
    if (currentIntent(captured) && request === latestStudentRequest) remoteStudents.value = result.items
  } catch (caught) {
    if (currentIntent(captured) && request === latestStudentRequest && !isAbortError(caught)) {
      remoteStudents.value = []
      studentSearchError.value = caught instanceof Error ? caught.message : '学生搜索失败。'
    }
  } finally {
    if (currentIntent(captured) && request === latestStudentRequest) studentSearchLoading.value = false
  }
}

function scheduleStudentSearch() {
  latestStudentRequest += 1
  if (studentTimer) clearTimeout(studentTimer)
  studentController?.abort()
  remoteStudents.value = []
  studentSearchError.value = ''
  studentSearchLoading.value = false
  const search = studentSearch.value.trim()
  if (!search) return
  studentTimer = setTimeout(() => { void searchStudents(search) }, 250)
}

async function selectStudent(student: ResearchStudent) {
  cancelStudentSearch()
  pendingStudent.value = student
  studentSearch.value = studentLabel(student)
  showSuggestions.value = false
}

function clearStudent() {
  pendingStudent.value = null
  studentSearch.value = ''
  showSuggestions.value = true
  scheduleStudentSearch()
}

function studentInput() {
  pendingStudent.value = null
  showSuggestions.value = true
  scheduleStudentSearch()
}

async function submitFilters() {
  if (!selectedStudent.value) return
  const sessionRef = typeof route.query.session_ref === 'string' ? route.query.session_ref : undefined
  await go({ view: 'student', student_id: String(selectedStudent.value.id), session_ref: sessionRef, question_ref: undefined, visit: undefined, page: undefined, cursor: undefined })
}

async function selectTable(key: string) {
  if (view.value !== 'overview' && key === 'behavior_events') {
    await go({ view: 'timeline', question_ref: selectedQuestion.value?.question_ref, visit: selectedVisit.value?.key, page: undefined, cursor: undefined })
    return
  }
  if (view.value === 'overview') await go({ table: key, page: undefined, cursor: undefined })
}

async function selectStudentSession(session: ResearchSession) {
  await go({ session_ref: session.session_ref, question_ref: undefined, visit: undefined, page: undefined, cursor: undefined })
}

async function openSession() {
  if (selectedSession.value) await go({ view: 'session', session_ref: selectedSession.value.session_ref, question_ref: undefined, visit: undefined, page: undefined, cursor: undefined })
}

async function openQuestion(questionRef: string) {
  await go({ view: 'question', question_ref: questionRef, visit: undefined, page: undefined, cursor: undefined })
}

async function selectQuestion(questionRef: string) {
  await go({ question_ref: questionRef || undefined, visit: undefined, page: undefined, cursor: undefined })
}

async function selectVisit(key: string) {
  const visit = workspace.value?.visits.find(item => item.key === key)
  await go({ visit: key || undefined, question_ref: visit?.question_ref || selectedQuestion.value?.question_ref, page: undefined, cursor: undefined })
}

async function navigate(page: number) {
  await go({ page: page === 1 ? undefined : String(page), cursor: undefined })
}

async function navigateBehavior(cursor: string | null | undefined) {
  if (cursor) await go({ cursor, page: undefined })
}

watch(
  () => [route.query.student_id, route.query.view],
  () => { pendingStudent.value = undefined },
)

watch(
  () => [route.params.courseId, getAuthenticationGeneration(), route.query] as const,
  () => { void load(routeQuery(route.query)) },
  { deep: true, immediate: true },
)

watch(scopeRefresh, () => { void checkStatus(true) })

onBeforeUnmount(() => {
  disposed = true
  latestRequest += 1
  latestStudentRequest += 1
  document.removeEventListener('visibilitychange', visibilityChanged)
  latestStatusRequest += 1
  statusController?.abort()
  if (pollTimer) clearTimeout(pollTimer)
  dataController?.abort()
  studentController?.abort()
  if (studentTimer) clearTimeout(studentTimer)
})
</script>

<template>
  <Alert v-if="cursorNotice" tone="info">{{ cursorNotice }}</Alert>
  <Alert v-if="error" tone="danger">{{ error }}</Alert>
  <p v-if="loading && !data" class="kt-research-loading" role="status">正在加载研究数据…</p>
  <p v-else-if="loading" class="kt-research-loading" role="status" aria-busy="true">正在刷新研究数据…</p>
  <template v-else-if="data">
    <button type="button" class="kt-button" data-testid="refresh-research-data" @click="checkStatus(true)">刷新结果</button>
    <p v-if="data.preparation" data-testid="preparation-counts" role="status">
      已结束会话 {{ data.preparation.total_count }} · 最新且材料完整 {{ data.preparation.latest_complete_count }} ·
      材料不完整或待核验 {{ data.preparation.input_incomplete_count }}（完整性未核验 {{ data.preparation.input_unverified_count }}）·
      首次整理 {{ data.preparation.pending_count }} · 更新中 {{ data.preparation.updating_count }} ·
      整理失败 {{ data.preparation.failed_count }}。以上状态可能重叠；评分状态另行展示。
    </p>
    <Alert
      v-if="data.preparation && data.preparation.state !== 'ready'"
      :tone="data.preparation.state === 'failed' ? 'danger' : data.preparation.state === 'updating' ? 'info' : 'warning'"
      data-testid="preparation-state"
    >
      <strong>研究数据状态：</strong>{{ data.preparation.message }}
    </Alert>
    <p v-if="data.preparation?.status_checked_at" data-testid="preparation-sampled-at">截至 {{ formatTime(data.preparation.status_checked_at) }} 的状态快照。</p>
    <header v-if="view === 'overview'" class="kt-research-heading">
      <div>
        <p class="kt-research-kicker">实验数据</p>
        <h1>研究数据预览</h1>
        <p>按实验、班级、学生和作答会话查看可直接用于研究的六张数据表。</p>
      </div>
      <aside v-if="selectedStudent" class="kt-step-note">
        <ResearchDataIcon name="info" />
        <div><strong>步骤 1/5：筛选单一学生</strong><span>通过选择学生，查看该学生的研究数据预览。</span></div>
      </aside>
    </header>

    <EmptyState v-if="!data.scope.is_complete" title="请先选择实验和班级">
      <p>研究数据预览需要明确的实验和班级范围。</p>
    </EmptyState>

    <div v-else class="kt-research-workspace" :class="{ 'kt-research-workspace--timeline': view === 'timeline' }">
      <template v-if="view === 'overview'">
        <form class="kt-research-filter-panel" role="search" @submit.prevent="submitFilters">
          <label class="kt-student-search">学生
            <span class="kt-input-wrap">
              <ResearchDataIcon name="search" />
              <input
                v-model="studentSearch"
                type="search"
                aria-label="按学生姓名或学号搜索"
                placeholder="输入姓名或学号搜索"
                autocomplete="off"
                @focus="showSuggestions = true"
                @input="studentInput"
              >
              <button v-if="selectedStudent" type="button" aria-label="清除学生" @click="clearStudent">×</button>
            </span>
            <span v-if="showSuggestions && suggestions.length" class="kt-student-suggestions" role="listbox">
              <button v-for="student in suggestions" :key="student.id" type="button" role="option" data-testid="student-suggestion" @click="selectStudent(student)">
                <strong>{{ student.name }}</strong><span>{{ student.student_number }}</span>
              </button>
            </span>
            <small v-if="studentSearchLoading" role="status">正在搜索学生…</small>
            <small v-else-if="studentSearchError" role="alert">{{ studentSearchError }}</small>
          </label>
          <label>作答会话
            <select
              :value="typeof route.query.session_ref === 'string' ? route.query.session_ref : ''"
              aria-label="选择作答会话"
              @change="go({ session_ref: ($event.target as HTMLSelectElement).value || undefined, page: undefined, cursor: undefined })"
            >
              <option value="">全部作答会话</option>
              <option v-for="option in data.session_options" :key="option.value" :value="option.value">{{ option.label }}</option>
            </select>
          </label>
          <button class="kt-research-primary" type="submit" :disabled="!selectedStudent">应用筛选</button>
        </form>

        <ResearchCards :tables="data.tables" :active-key="activeCardKey" @select="selectTable" />

        <section class="kt-research-panel kt-research-preview">
          <div class="kt-research-section-heading">
            <div><p class="kt-research-kicker">当前预览表</p><h2>{{ cardCopy[data.selected_table.key]?.label || data.selected_table.label }}</h2><p>{{ cardCopy[data.selected_table.key]?.description || data.selected_table.description }}</p></div>
            <div class="kt-research-panel-meta"><strong>共 {{ data.preview?.page.total_items || 0 }} 条</strong><span>数据生成于 {{ formatTime(data.generated_at) }}</span></div>
          </div>
          <template v-if="data.preview">
            <div v-if="data.preview.rows.length" class="kt-research-table-scroll" tabindex="0" aria-label="研究数据表，可水平滚动">
              <table class="kt-research-table">
                <thead><tr><th v-for="column in data.preview.columns" :key="column.name">{{ column.description }}</th></tr></thead>
                <tbody>
                  <tr v-for="(row, rowIndex) in data.preview.rows" :key="rowIndex">
                    <td v-for="(column, columnIndex) in data.preview.columns" :key="column.name">
                      <details v-if="isLongText(row[columnIndex])" data-testid="long-cell"><summary>{{ compactText(row[columnIndex]) }}</summary><p>{{ displayValue(row[columnIndex]) }}</p></details>
                      <span v-else>{{ displayValue(row[columnIndex]) }}</span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <EmptyState v-else title="当前范围暂无记录" />
            <nav v-if="data.preview.cursor" class="kt-research-cursor" aria-label="行为记录翻页">
              <button type="button" :disabled="!data.preview.cursor.previous" @click="navigateBehavior(data.preview.cursor.previous)">上一页</button>
              <button type="button" :disabled="!data.preview.cursor.next" @click="navigateBehavior(data.preview.cursor.next)">下一页</button>
            </nav>
            <Paginator v-else :page="data.preview.page.number" :total-pages="data.preview.page.total_pages" @navigate="navigate" />
          </template>
        </section>
      </template>

      <template v-else-if="view === 'student' && workspace?.student">
        <header class="kt-research-heading">
          <div><p class="kt-research-kicker">学生数据</p><h1>学生数据总览</h1><p>查看学生的实验参与情况，选择具体会话可查看该次实验的详细研究数据。</p></div>
          <strong class="kt-step-inline">步骤 2/5：选择学生会话</strong>
        </header>
        <section class="kt-research-panel kt-student-summary">
          <span class="kt-avatar"><ResearchDataIcon name="student" /></span>
          <strong>{{ workspace.student.name }}</strong>
          <dl>
            <div><dt>学号</dt><dd>{{ workspace.student.student_number }}</dd></div>
            <div><dt>班级</dt><dd>{{ workspace.student.class_name }}</dd></div>
            <div><dt>课程</dt><dd>{{ workspace.student.course_name }}</dd></div>
            <div><dt>已参与实验</dt><dd>{{ workspace.student.experiment_count }} 次</dd></div>
          </dl>
        </section>
        <section class="kt-research-panel">
          <div class="kt-research-section-heading"><div><h2>该学生的实验会话</h2><p>共 {{ workspace.sessions.length }} 条会话记录，按时间倒序排列</p></div></div>
          <div class="kt-research-table-scroll">
            <table class="kt-research-table kt-clickable-table">
              <thead><tr><th>sessionID</th><th>实验名称</th><th>完成状态</th><th>题目数</th><th>行为记录数</th><th>提交时间</th></tr></thead>
              <tbody>
                <tr v-for="session in workspace.sessions" :key="session.session_ref" :data-session-ref="session.session_ref" :aria-selected="selectedSession?.session_ref === session.session_ref" tabindex="0" @click="selectStudentSession(session)" @keydown.enter="selectStudentSession(session)">
                  <td>{{ session.session_ref }}</td><td>{{ session.experiment_name }}</td><td><span class="kt-status-dot" />{{ session.status_label }}</td><td>{{ session.question_count }}</td><td>{{ session.behavior_count }}</td><td>{{ formatTime(session.submitted_at) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>
        <section v-if="selectedSession" class="kt-research-panel">
          <div class="kt-research-section-heading">
            <div><p class="kt-research-kicker">当前选中会话预览</p><h2>{{ selectedSession.experiment_name }}（{{ selectedSession.session_ref }}）</h2><p>按实验、班级、学生和作答会话查看可直接用于研究的六张数据表。</p></div>
            <button class="kt-research-link" type="button" data-testid="open-session" @click="openSession">▶ 查看完整数据</button>
          </div>
          <ResearchCards :tables="data.tables" active-key="responses" compact />
        </section>
      </template>

      <template v-else-if="view === 'session' && selectedSession">
        <ResearchPageHeading title="会话详情" description="查看单次实验会话的详细数据，包括逐题作答、行为过程等信息。" step="步骤 3/5：查看逐题作答" @back="go({ view: 'student', question_ref: undefined, visit: undefined })" />
        <ResearchSessionSummary :session="selectedSession" />
        <ResearchCards :tables="data.tables" :active-key="activeCardKey" @select="selectTable" />
        <section class="kt-research-panel">
          <div class="kt-research-section-heading"><div><h2>逐题作答</h2><p>查看该会话中学生对每道题的作答情况。</p></div><span class="kt-step-pill">步骤 3/5：查看逐题作答</span></div>
          <ResearchResponsesTable :responses="workspace?.responses || []" @select="openQuestion" />
        </section>
      </template>

      <template v-else-if="view === 'question' && selectedSession && selectedQuestion">
        <ResearchPageHeading title="题目行为过程" description="查看单个题目在一次或多次访问中的详细行为日志，仅展示该题目内的行为数据。" step="步骤 4/5：进入单题行为过程" @back="go({ view: 'session', visit: undefined })" />
        <ResearchQuestionSummary :session="selectedSession" :question="selectedQuestion" />
        <ResearchCards :tables="data.tables" active-key="behavior_events" />
        <section class="kt-research-filter-panel kt-behavior-filters">
          <label>选择题目
            <select :value="selectedQuestion.question_ref" aria-label="选择题目" @change="selectQuestion(($event.target as HTMLSelectElement).value)">
              <option v-for="response in workspace?.responses || []" :key="response.question_ref" :value="response.question_ref">{{ response.question_ref }}（{{ response.question_type }}）</option>
            </select>
          </label>
          <label>选择访问
            <select :value="typeof route.query.visit === 'string' ? route.query.visit : ''" aria-label="选择访问" @change="selectVisit(($event.target as HTMLSelectElement).value)">
              <option value="">全部访问</option>
              <option v-for="visitItem in workspace?.visits || []" :key="visitItem.key" :value="visitItem.key">第 {{ visitItem.visit_index }} 次访问</option>
            </select>
          </label>
          <p><ResearchDataIcon name="info" />仅展示该题目内的行为数据，不包含其他题目的行为。</p>
        </section>
        <ResearchBehaviorTable :behavior="workspace?.behavior || []" :generated-at="data.generated_at" />
        <nav v-if="data.preview?.cursor" class="kt-research-cursor" aria-label="行为记录翻页">
          <button type="button" :disabled="!data.preview.cursor.previous" @click="navigateBehavior(data.preview.cursor.previous)">上一页</button>
          <button type="button" :disabled="!data.preview.cursor.next" @click="navigateBehavior(data.preview.cursor.next)">下一页</button>
        </nav>
      </template>

      <template v-else-if="view === 'timeline' && selectedSession">
        <ResearchPageHeading title="单一学生全过程" description="查看某一学生在实验中的完整答题过程、行为轨迹与最终结果。" step="步骤 5/5：查看单一学生全过程" @back="go({ view: 'session', visit: undefined })" />
        <ResearchSessionSummary :session="selectedSession" />
        <section class="kt-timeline-grid">
          <article class="kt-research-panel kt-visit-timeline">
            <div class="kt-icon-heading"><ResearchDataIcon name="behavior" /><div><h2>题目访问轨迹</h2><p>该学生在实验中的题目访问顺序与时间轨迹。</p></div></div>
            <button v-for="(visitItem, index) in workspace?.visits || []" :key="visitItem.key" type="button" :data-visit-key="visitItem.key" :aria-current="selectedVisit?.key === visitItem.key ? 'step' : undefined" @click="selectVisit(visitItem.key)">
              <span>{{ index + 1 }}</span><div><strong>{{ visitItem.question_ref }}<small v-if="visitItem.visit_index > 1">（第 {{ visitItem.visit_index }} 次）</small></strong><p>点击题目时间：{{ formatTime(visitItem.clicked_at) }}<br>进入题目时间：{{ formatTime(visitItem.entered_at) }}<br>离开题目时间：{{ formatTime(visitItem.left_at) }}</p></div><b>停留时长：{{ formatDuration(visitItem.duration_seconds) }}</b>
            </button>
          </article>
          <article class="kt-research-panel kt-visit-detail">
            <div class="kt-research-section-heading">
              <div class="kt-icon-heading"><ResearchDataIcon name="question" /><div><h2>当前题目内部过程</h2><p>展示选中题目的详细行为序列。</p></div></div>
              <select :value="selectedVisit?.key" aria-label="选择当前访问" @change="selectVisit(($event.target as HTMLSelectElement).value)">
                <option v-for="visitItem in workspace?.visits || []" :key="visitItem.key" :value="visitItem.key">{{ visitItem.question_ref }}（第 {{ visitItem.visit_index }} 次访问）</option>
              </select>
            </div>
            <div v-if="selectedVisit" class="kt-visit-highlight"><strong>题目：{{ selectedVisit.question_ref }}（第 {{ selectedVisit.visit_index }} 次访问）</strong><span>进入时间：{{ formatTime(selectedVisit.entered_at) }}<br>离开时间：{{ formatTime(selectedVisit.left_at) }}<br>停留时长：{{ formatDuration(selectedVisit.duration_seconds) }}</span></div>
            <ResearchBehaviorCompactTable :behavior="workspace?.behavior || []" />
            <nav v-if="data.preview?.cursor" class="kt-research-cursor" aria-label="行为记录翻页">
              <button type="button" :disabled="!data.preview.cursor.previous" @click="navigateBehavior(data.preview.cursor.previous)">上一页</button>
              <button type="button" :disabled="!data.preview.cursor.next" @click="navigateBehavior(data.preview.cursor.next)">下一页</button>
            </nav>
          </article>
        </section>
        <section class="kt-research-panel kt-timeline-results">
          <div class="kt-icon-heading"><ResearchDataIcon name="response" /><div><h2>最终作答结果</h2><p>该会话所有题目的最终提交结果。</p></div></div>
          <ResearchResponsesResultTable :responses="workspace?.responses || []" />
        </section>
      </template>

      <EmptyState v-else title="当前研究数据状态不可用"><p>请返回研究数据预览重新选择学生和会话。</p></EmptyState>
    </div>
  </template>
</template>
