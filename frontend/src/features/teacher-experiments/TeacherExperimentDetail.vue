<script setup lang="ts">
import { computed, inject, onMounted, reactive, ref, watch, type Ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import type { components } from '@/api/generated/schema'
import Alert from '@/design-system/components/Alert.vue'
import Button from '@/design-system/components/Button.vue'
import EmptyState from '@/design-system/components/EmptyState.vue'
import MobileCardList from '@/design-system/components/MobileCardList.vue'
import Paginator from '@/design-system/components/Paginator.vue'
import SidebarSection from '@/design-system/components/SidebarSection.vue'
import StatusBadge from '@/design-system/components/StatusBadge.vue'
import Icon from '@/design-system/Icon.vue'
import DeletionDialog from '@/features/teacher-deletion/DeletionDialog.vue'
import type { DeletionTarget } from '@/features/teacher-deletion/api'

import {
  getTeacherExperimentApi,
  type ClassTask,
  type ClassTaskProgress,
  type CollectionParticipantCollection,
  type TeacherExperiment,
  type TeacherExperimentReadiness,
} from './api'

const route = useRoute()
const router = useRouter()
const api = getTeacherExperimentApi()
const courseId = Number(route.params.courseId)
const experimentId = Number(route.params.experimentId)
const course = inject<Ref<components['schemas']['CourseContext'] | undefined>>(
  'teacher-experiments-course',
  ref<components['schemas']['CourseContext']>(),
)
const experiment = ref<TeacherExperiment>()
const readiness = ref<TeacherExperimentReadiness>()
const tasks = ref<ClassTask[]>([])
const progress = reactive<Record<number, ClassTaskProgress>>({})
const loading = ref(true)
const working = ref(false)
const error = ref('')
const scheduleTaskId = ref<number>()
const selectedScheduleTask = computed(() => tasks.value.find(task => task.id === scheduleTaskId.value))
const scheduleForm = reactive({
  opens_at: '', closes_at: '', reason: '', total_minutes: '', item_seconds: '',
  total_extension_minutes: '', item_extension_seconds: '',
})
const confirmation = reactive({ taskId: 0, action: '' as '' | 'cancel' | 'manual_close', reason: '' })
const activeTab = ref<'definition' | 'tasks' | 'collection'>('definition')
const deletionTarget = ref<DeletionTarget>()
const collection = ref<CollectionParticipantCollection>()
const collectionLoading = ref(false)
const collectionError = ref('')
const detailReady = ref(false)
const collectionClassId = ref(queryText(route.query.class_id))
const collectionStatus = ref(queryText(route.query.status))
const collectionQuery = ref(queryText(route.query.q))
const collectionPage = ref(queryPage(route.query.page))
let replacingCollectionRoute = ''
let collectionRequestId = 0

const readinessTone = computed(() => readiness.value?.ready ? 'success' : 'warning')
const courseName = computed(() => course?.value?.name || '课程')
const isPublished = computed(() => experiment.value?.status !== 'draft')

function displayDate(value: string | null | undefined) {
  return value ? new Date(value).toLocaleString('zh-CN') : '未设置'
}

function displayLimit(value: number | null | undefined, unit: string) {
  return value === null || value === undefined ? '未设置' : `${value} ${unit}`
}

function queryText(value: unknown) {
  return typeof value === 'string' ? value : ''
}

function queryPage(value: unknown) {
  const page = Number(queryText(value))
  return Number.isInteger(page) && page > 0 ? page : 1
}

function collectionDate(value: string | null) {
  return value ? new Date(value).toLocaleString('zh-CN') : '—'
}

function collectionTone(status: string) {
  if (status === 'completed') return 'success'
  if (status === 'in_progress') return 'warning'
  if (status === 'expired') return 'danger'
  return 'neutral'
}

function collectionFilters(page = 1) {
  const filters: { classId?: number; status?: string; query?: string; page: number } = { page }
  const classId = collectionClassId.value.trim()
  const status = collectionStatus.value.trim()
  const query = collectionQuery.value.trim()
  if (classId) filters.classId = Number(classId)
  if (status) filters.status = status
  if (query) filters.query = query
  return filters
}

function collectionRouteQuery(page = 1) {
  const query: Record<string, string> = { tab: 'collection' }
  const classId = collectionClassId.value.trim()
  const status = collectionStatus.value.trim()
  const search = collectionQuery.value.trim()
  if (classId) query.class_id = classId
  if (status) query.status = status
  if (search) query.q = search
  if (page > 1) query.page = String(page)
  return query
}

function collectionRouteKey(query: Record<string, unknown>) {
  return JSON.stringify({
    tab: queryText(query.tab),
    class_id: queryText(query.class_id).trim(),
    status: queryText(query.status).trim(),
    q: queryText(query.q).trim(),
    page: queryPage(query.page),
  })
}

function syncCollectionRoute(query: Record<string, unknown>) {
  collectionClassId.value = queryText(query.class_id)
  collectionStatus.value = queryText(query.status)
  collectionQuery.value = queryText(query.q)
  collectionPage.value = queryPage(query.page)
}

async function loadCollection(page = 1) {
  const requestId = ++collectionRequestId
  collectionLoading.value = true
  collectionError.value = ''
  collection.value = undefined
  collectionPage.value = page
  try {
    const result = await api.collectionParticipants(experimentId, collectionFilters(page))
    if (requestId === collectionRequestId) collection.value = result
  } catch (caught) {
    if (requestId === collectionRequestId) {
      collectionError.value = caught instanceof Error ? caught.message : '学生采集状态加载失败。'
    }
  } finally {
    if (requestId === collectionRequestId) collectionLoading.value = false
  }
}

async function replaceCollectionRoute(page = 1) {
  const query = collectionRouteQuery(page)
  replacingCollectionRoute = collectionRouteKey(query)
  await router.replace({ query })
  if (replacingCollectionRoute === collectionRouteKey(query)) replacingCollectionRoute = ''
}

async function applyCollectionFilters() {
  collectionPage.value = 1
  await replaceCollectionRoute(1)
  await loadCollection(1)
}

async function clearCollectionFilters() {
  collectionClassId.value = ''
  collectionStatus.value = ''
  collectionQuery.value = ''
  collectionPage.value = 1
  await replaceCollectionRoute(1)
  await loadCollection(1)
}

async function navigateCollection(page: number) {
  collectionPage.value = page
  await replaceCollectionRoute(page)
  await loadCollection(page)
}

async function selectTab(tab: 'definition' | 'tasks' | 'collection') {
  activeTab.value = tab
  if (tab === 'collection') {
    await replaceCollectionRoute(collectionPage.value)
    await loadCollection(collectionPage.value)
    return
  }
  replacingCollectionRoute = collectionRouteKey({})
  await router.replace({ query: {} })
  if (replacingCollectionRoute === collectionRouteKey({})) replacingCollectionRoute = ''
}

function dateTimeLocal(value: string | null) {
  if (!value) return ''
  const date = new Date(value)
  const offset = date.getTimezoneOffset() * 60_000
  return new Date(date.getTime() - offset).toISOString().slice(0, 16)
}

function isoOrNull(value: string) {
  return value ? new Date(value).toISOString() : null
}

async function load() {
  loading.value = true
  detailReady.value = false
  error.value = ''
  try {
    const [definition, ready, taskCollection] = await Promise.all([
      api.detail(courseId, experimentId),
      api.readiness(courseId, experimentId),
      api.classTasks(experimentId),
    ])
    experiment.value = definition
    const defaultTab = definition.status === 'draft' ? 'definition' : 'tasks'
    activeTab.value = queryText(route.query.tab) === 'collection' ? 'collection' : defaultTab
    readiness.value = ready
    tasks.value = taskCollection.items
    const rows = await Promise.all(tasks.value.map(async (task) => [task.id, await api.progress(task.id)] as const))
    for (const [taskId, row] of rows) progress[taskId] = row
    detailReady.value = true
    loading.value = false
    if (activeTab.value === 'collection') await loadCollection(collectionPage.value)
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '实验详情加载失败。'
  } finally {
    loading.value = false
  }
}

watch(
  () => route.query,
  async (query) => {
    const key = collectionRouteKey(query)
    if (key === replacingCollectionRoute) {
      replacingCollectionRoute = ''
      return
    }
    if (queryText(query.tab) === 'collection') {
      syncCollectionRoute(query)
      activeTab.value = 'collection'
      if (detailReady.value) await loadCollection(collectionPage.value)
      return
    }
    if (!experiment.value) return
    activeTab.value = queryText(query.tab) === 'definition' || queryText(query.tab) === 'tasks'
      ? queryText(query.tab) as 'definition' | 'tasks'
      : experiment.value.status === 'draft' ? 'definition' : 'tasks'
  },
  { deep: true },
)

async function startPublish() {
  working.value = true
  error.value = ''
  try {
    const plan = await api.startPlan(courseId, experimentId)
    await router.push(`/teacher/courses/${courseId}/experiments/${experimentId}/publish-plan/${plan.id}/step/${plan.current_step || 1}/`)
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '无法进入发布流程。'
  } finally {
    working.value = false
  }
}

function editSchedule(task: ClassTask) {
  scheduleTaskId.value = task.id
  scheduleForm.opens_at = dateTimeLocal(task.opens_at)
  scheduleForm.closes_at = dateTimeLocal(task.closes_at)
  scheduleForm.reason = ''
  scheduleForm.total_minutes = task.total_time_limit_minutes == null ? '' : String(task.total_time_limit_minutes)
  scheduleForm.item_seconds = task.per_question_time_limit_seconds == null ? '' : String(task.per_question_time_limit_seconds)
  scheduleForm.total_extension_minutes = ''
  scheduleForm.item_extension_seconds = ''
}

function timingInput(task: ClassTask) {
  const beforeStart = task.available_actions.includes('revise_schedule')
  return {
    expected_revision: task.schedule_revision,
    reopen: task.business_status === 'manual_closed' || task.business_status === 'closed',
    reason: scheduleForm.reason.trim(),
    ...(task.total_time_limit_minutes == null ? {} : beforeStart
      ? { total_time_limit_minutes: Number(scheduleForm.total_minutes) }
      : scheduleForm.total_extension_minutes
        ? { total_time_limit_minutes: task.total_time_limit_minutes + Number(scheduleForm.total_extension_minutes) }
        : {}),
    ...(task.per_question_time_limit_seconds == null ? {} : beforeStart
      ? { per_question_time_limit_seconds: Number(scheduleForm.item_seconds) }
      : scheduleForm.item_extension_seconds
        ? { per_question_time_limit_seconds: task.per_question_time_limit_seconds + Number(scheduleForm.item_extension_seconds) }
        : {}),
  }
}

async function reviseSchedule(task: ClassTask) {
  const beforeStart = task.available_actions.includes('revise_schedule')
  const opensChanged = beforeStart && scheduleForm.opens_at !== dateTimeLocal(task.opens_at)
  const cutoffChanged = scheduleForm.closes_at !== dateTimeLocal(task.closes_at)
  const closesAt = cutoffChanged ? isoOrNull(scheduleForm.closes_at) : undefined
  working.value = true
  error.value = ''
  try {
    await api.reviseSchedule(task.id, {
      ...timingInput(task),
      ...(opensChanged ? { opens_at: isoOrNull(scheduleForm.opens_at) } : {}),
      ...(cutoffChanged ? { closes_at: closesAt } : {}),
    })
    scheduleTaskId.value = undefined
    await load()
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '排期修订失败。'
  } finally {
    working.value = false
  }
}

function requestTerminalAction(taskId: number, action: 'cancel' | 'manual_close') {
  confirmation.taskId = taskId
  confirmation.action = action
  confirmation.reason = ''
}

async function applyTerminalAction() {
  working.value = true
  try {
    if (confirmation.action === 'cancel') await api.cancel(confirmation.taskId, confirmation.reason)
    if (confirmation.action === 'manual_close') await api.manualClose(confirmation.taskId, confirmation.reason)
    confirmation.action = ''
    await load()
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '任务状态更新失败。'
  } finally {
    working.value = false
  }
}

async function releaseFeedback(taskId: number) {
  working.value = true
  error.value = ''
  try {
    await api.releaseFeedback(taskId)
    await load()
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '反馈发布失败。'
  } finally {
    working.value = false
  }
}

async function afterDeletion() {
  deletionTarget.value = undefined
  await router.push(`/teacher/courses/${courseId}/experiments/`)
}

onMounted(load)
</script>

<template>
  <p v-if="loading" role="status">正在加载实验详情…</p>
  <template v-else-if="experiment">
    <header class="kt-experiment-page-header">
      <div class="kt-experiment-page-header__copy">
        <nav class="kt-experiment-breadcrumb" aria-label="层级目录">
          <a href="/teacher/courses/">我的课程</a><span aria-hidden="true">/</span>
          <a :href="`/teacher/courses/${courseId}/`">{{ courseName }}</a><span aria-hidden="true">/</span>
          <a :href="`/teacher/courses/${courseId}/experiments/`">实验</a><span aria-hidden="true">/</span>
          <span aria-current="page">{{ experiment.name }}</span>
        </nav>
        <div class="kt-experiment-title-row">
          <h1>{{ experiment.name }}</h1>
          <StatusBadge :label="experiment.status_label" :tone="experiment.status === 'draft' ? 'warning' : 'success'" />
        </div>
        <p>{{ experiment.description || '未填写实验说明。' }}</p>
      </div>
      <div v-if="experiment.can_manage" class="kt-experiment-page-header__actions">
        <button class="kt-button kt-button--danger" data-delete-experiment type="button" @click="deletionTarget = { kind: 'experiment', experimentId }">删除实验</button>
      </div>
    </header>

    <SidebarSection>
      <nav class="kt-experiment-tabs kt-sidebar-secondary-nav" role="tablist" aria-label="实验详情导航" aria-orientation="vertical">
        <button id="experiment-tab-definition" type="button" role="tab" :aria-selected="activeTab === 'definition'" aria-controls="experiment-panel-definition" :class="{ 'is-active': activeTab === 'definition' }" @click="selectTab('definition')">实验定义</button>
        <button id="experiment-tab-tasks" type="button" role="tab" :aria-selected="activeTab === 'tasks'" aria-controls="experiment-panel-tasks" :class="{ 'is-active': activeTab === 'tasks' }" @click="selectTab('tasks')">班级发布</button>
        <button id="experiment-tab-collection" type="button" role="tab" :aria-selected="activeTab === 'collection'" aria-controls="experiment-panel-collection" :class="{ 'is-active': activeTab === 'collection' }" @click="selectTab('collection')">采集状态</button>
      </nav>
    </SidebarSection>

    <Alert v-if="error" tone="danger">{{ error }}</Alert>

    <section v-if="activeTab === 'definition'" id="experiment-panel-definition" role="tabpanel" aria-labelledby="experiment-tab-definition">
      <div class="kt-experiment-draft-grid">
        <section class="kt-card kt-definition-summary" aria-labelledby="definition-summary">
          <div class="kt-section-heading"><h2 id="definition-summary">实验设置摘要</h2></div>
          <dl class="kt-description-list kt-definition-summary__list">
            <div><span class="kt-summary-icon" data-summary-icon><Icon name="bank" :size="17" /></span><dt>题库</dt><dd>{{ experiment.question_bank?.name || '未选择题库' }}</dd></div>
            <div><span class="kt-summary-icon" data-summary-icon><Icon name="definition" :size="17" /></span><dt>选题</dt><dd>{{ experiment.sampling_mode_label || experiment.selection_mode_label }}<span v-if="experiment.sampling_mode === 'total_count'"> · {{ experiment.sampling_total_count }} 题</span></dd></div>
            <div><span class="kt-summary-icon" data-summary-icon><Icon name="summary" :size="17" /></span><dt>跨实验题目</dt><dd>{{ experiment.cross_experiment_item_policy === 'mixed_review' ? `混合复测 · ${experiment.review_count} 题` : (experiment.cross_experiment_item_policy === 'exclude_previous' ? '排除已提交或已分配题目' : '不限制历史题目') }}</dd></div>
            <div><span class="kt-summary-icon" data-summary-icon><Icon name="summary" :size="17" /></span><dt>题目顺序</dt><dd>{{ experiment.ordering_mode_label }}</dd></div>
            <div><span class="kt-summary-icon" data-summary-icon><Icon name="status" :size="17" /></span><dt>反馈</dt><dd>{{ experiment.feedback_mode_label }}</dd></div>
            <div><span class="kt-summary-icon" data-summary-icon><Icon name="status" :size="17" /></span><dt>反馈发布</dt><dd>{{ experiment.feedback_release_policy_label || '未设置' }}</dd></div>
            <div><span class="kt-summary-icon" data-summary-icon><Icon name="status" :size="17" /></span><dt>总限时</dt><dd>{{ displayLimit(experiment.total_time_limit_minutes, '分钟') }}</dd></div>
            <div><span class="kt-summary-icon" data-summary-icon><Icon name="status" :size="17" /></span><dt>单题限时</dt><dd>{{ displayLimit(experiment.per_question_time_limit_seconds, '秒') }}</dd></div>
            <div><span class="kt-summary-icon" data-summary-icon><Icon name="data" :size="17" /></span><dt>中断续答</dt><dd>{{ experiment.resume_mode_label }}</dd></div>
            <div><span class="kt-summary-icon" data-summary-icon><Icon name="data" :size="17" /></span><dt>作答信心</dt><dd>{{ experiment.confidence_mode_label }}</dd></div>
          </dl>
          <div v-if="experiment.can_edit" class="kt-definition-summary__action">
            <a class="kt-button kt-button--secondary" :href="`/teacher/courses/${courseId}/experiments/${experimentId}/edit/`">编辑实验定义</a>
          </div>
        </section>
        <aside class="kt-experiment-side-stack">
          <section class="kt-card kt-publish-ready-card" aria-labelledby="publish-ready-title">
            <div v-if="!isPublished" class="kt-publish-empty-illustration" aria-hidden="true"><Icon name="publish" :size="31" /><Icon name="classes" :size="22" /><Icon name="check" :size="17" /></div>
            <h2 id="publish-ready-title">发布到班级</h2>
            <p v-if="isPublished">当前实验已有 {{ tasks.length }} 个班级任务，可继续将同一实验定义发布到其他班级。</p>
            <p v-else>首次发布后实验定义将冻结；班级、参与者和开放时段将在下一步确认。</p>
            <Button data-action="publish-from-definition" :disabled="working" @click="startPublish">{{ working ? '正在进入…' : (isPublished ? '继续发布' : '发布到班级') }}</Button>
          </section>
          <section class="kt-card kt-readiness-card" aria-labelledby="readiness-summary">
            <div class="kt-section-heading"><h2 id="readiness-summary">发布前检查</h2><StatusBadge :label="readiness?.ready ? '定义就绪' : '需要处理'" :tone="readinessTone" /></div>
            <dl class="kt-readiness-facts">
              <div><dt>题库全局候选</dt><dd>{{ readiness?.candidate_count ?? 0 }} 道可用题目</dd></div>
              <div><dt>已关联参与者</dt><dd>{{ readiness?.participant_count ?? 0 }}</dd></div>
            </dl>
            <ul class="kt-readiness-checklist">
              <li><Icon :name="readiness?.ready ? 'check' : 'warning'" :size="17" /><span>{{ readiness?.ready ? '实验定义已通过基础检查' : '实验定义仍需完成基础检查' }}</span></li>
              <li><Icon name="bank" :size="17" /><span>题库全局候选：{{ readiness?.candidate_count ?? 0 }} 道，尚未按学生历史过滤</span></li>
              <li><Icon name="classes" :size="17" /><span>已关联参与者：{{ readiness?.participant_count ?? 0 }}</span></li>
              <li v-for="issue in readiness?.issues || []" :key="issue"><Icon name="warning" :size="17" /><span>{{ issue }}</span></li>
            </ul>
          </section>
        </aside>
      </div>
      <Alert tone="info">{{ isPublished ? '实验定义已经冻结；如需调整内容，请按现有流程创建新的实验定义。' : '首次正式发布后，实验定义将冻结；后续可继续发布到其他班级。' }}</Alert>
    </section>

    <section v-else-if="activeTab === 'tasks'" id="experiment-panel-tasks" role="tabpanel" aria-labelledby="experiment-tab-tasks">
      <Alert v-if="isPublished" tone="success">已发布 {{ tasks.length }} 个班级任务。任务状态和可用操作由服务端返回。</Alert>
      <div class="kt-published-layout">
        <section class="kt-card kt-published-task-table" aria-labelledby="class-task-title">
          <div class="kt-section-heading"><div><p class="kt-kicker">班级发布</p><h2 id="class-task-title">已发布班级任务</h2></div><span>{{ tasks.length }} 个</span></div>
          <EmptyState v-if="!tasks.length" title="尚未发布到班级"><p>完成发布检查并提交后，这里会显示每个班级的任务状态。</p></EmptyState>
          <div v-else class="kt-table-scroll">
            <table class="kt-table">
              <thead><tr><th>班级</th><th>状态</th><th>参与者</th><th>开放时段</th><th>未开始</th><th>进行中</th><th>已完成</th><th>操作</th></tr></thead>
              <tbody>
                <template v-for="task in tasks" :key="task.id">
                  <tr>
                    <td><strong>{{ task.class_name }}</strong></td><td><StatusBadge :label="task.business_status" tone="neutral" /></td>
                    <td>{{ progress[task.id]?.total_participants ?? task.participant_count }}</td><td>{{ displayDate(task.opens_at) }} — {{ displayDate(task.closes_at) }}<span v-if="task.total_time_limit_minutes != null"> · 总时限 {{ task.total_time_limit_minutes }} 分钟</span><span v-if="task.per_question_time_limit_seconds != null"> · 单题 {{ task.per_question_time_limit_seconds }} 秒</span><span v-if="task.recoverable_count"> · 可恢复 {{ task.recoverable_count }} 人</span></td>
                    <td>{{ progress[task.id]?.not_started ?? '—' }}</td><td>{{ progress[task.id]?.in_progress ?? '—' }}</td><td>{{ progress[task.id]?.completed ?? '—' }}</td>
                    <td>
                      <div class="kt-table-actions">
                        <a
                          v-if="task.available_actions.includes('grade_short_answers')"
                          data-action="grade-short-answers"
                          class="kt-text-button"
                          :href="`/teacher/courses/${courseId}/experiments/${experimentId}/class-tasks/${task.id}/grading/`"
                        >批改简答题 · 待批改 {{ task.grading?.pending ?? 0 }}</a>
                        <button v-if="task.available_actions.includes('revise_schedule')" data-action="revise-schedule" class="kt-text-button" type="button" @click="editSchedule(task)">修订排期</button>
                        <button v-else-if="task.available_actions.includes('adjust_time')" data-action="adjust-time" class="kt-text-button" type="button" @click="editSchedule(task)">调整时间</button>
                        <button v-if="task.available_actions.includes('cancel')" data-action="cancel-task" class="kt-text-button kt-text-button--danger" type="button" @click="requestTerminalAction(task.id, 'cancel')">取消任务</button>
                        <button v-if="task.available_actions.includes('manual_close')" data-action="manual-close" class="kt-text-button kt-text-button--danger" type="button" @click="requestTerminalAction(task.id, 'manual_close')">提前结束</button>
                        <button v-if="task.available_actions.includes('release_feedback')" data-action="release-feedback" class="kt-text-button" type="button" :disabled="working" @click="releaseFeedback(task.id)">发布反馈</button>
                      </div>
                    </td>
                  </tr>
                  <tr v-if="confirmation.taskId === task.id && confirmation.action"><td colspan="8"><form class="kt-inline-panel kt-inline-panel--danger" @submit.prevent="applyTerminalAction"><h3>{{ confirmation.action === 'cancel' ? '确认取消该班任务' : '确认提前结束该班任务' }}</h3><p>此操作会立即改变学生端可用状态，请填写可审计原因后确认。</p><label>原因<input v-model="confirmation.reason" maxlength="500" required></label><div class="kt-inline-actions"><button class="kt-button kt-button--danger" type="submit" :disabled="working">确认执行</button><button type="button" class="kt-text-button" @click="confirmation.action = ''">返回</button></div></form></td></tr>
                </template>
              </tbody>
            </table>
          </div>
          <form v-if="selectedScheduleTask" class="kt-inline-panel kt-time-adjust-panel" @submit.prevent="reviseSchedule(selectedScheduleTask)">
            <h3>调整该班时间：{{ selectedScheduleTask.class_name }} · 第 {{ selectedScheduleTask.schedule_revision }} 版</h3>
            <p v-if="selectedScheduleTask.business_status === 'manual_closed' || selectedScheduleTask.business_status === 'closed'">保存后重新开放；已提交答案不会解锁。</p>
            <div class="kt-inline-form-grid">
              <label v-if="selectedScheduleTask.available_actions.includes('revise_schedule')">开放时间<input v-model="scheduleForm.opens_at" type="datetime-local"></label>
              <label>{{ selectedScheduleTask.available_actions.includes('revise_schedule') ? '本班实验截止时间（调整后）' : selectedScheduleTask.closes_at ? `延长本班实验截止时间（当前：${displayDate(selectedScheduleTask.closes_at)}）` : '设置本班实验截止时间' }}<input v-model="scheduleForm.closes_at" data-action="class-cutoff" type="datetime-local" :required="selectedScheduleTask.business_status === 'closed' || selectedScheduleTask.business_status === 'manual_closed' || (selectedScheduleTask.total_time_limit_minutes == null && selectedScheduleTask.per_question_time_limit_seconds == null)"></label>
              <label v-if="selectedScheduleTask.total_time_limit_minutes != null && selectedScheduleTask.available_actions.includes('revise_schedule')">学生作答总时长（调整后，当前：{{ selectedScheduleTask.total_time_limit_minutes }} 分钟）<input v-model="scheduleForm.total_minutes" data-action="total-time-limit" type="number" min="1" step="1" required></label>
              <label v-else-if="selectedScheduleTask.total_time_limit_minutes != null">学生作答时间延长（当前时长：{{ selectedScheduleTask.total_time_limit_minutes }} 分钟）<input v-model="scheduleForm.total_extension_minutes" data-action="total-time-extension" type="number" min="1" step="1" placeholder="填写增加的分钟数"><small v-if="scheduleForm.total_extension_minutes">调整后：{{ selectedScheduleTask.total_time_limit_minutes + Number(scheduleForm.total_extension_minutes) }} 分钟</small></label>
              <label v-if="selectedScheduleTask.per_question_time_limit_seconds != null && selectedScheduleTask.available_actions.includes('revise_schedule')">每题作答时长（调整后，当前：{{ selectedScheduleTask.per_question_time_limit_seconds }} 秒）<input v-model="scheduleForm.item_seconds" data-action="item-time-limit" type="number" min="1" step="1" required></label>
              <label v-else-if="selectedScheduleTask.per_question_time_limit_seconds != null">每题作答时间延长（当前时长：{{ selectedScheduleTask.per_question_time_limit_seconds }} 秒）<input v-model="scheduleForm.item_extension_seconds" data-action="item-time-extension" type="number" min="1" step="1" placeholder="填写增加的秒数"><small v-if="scheduleForm.item_extension_seconds">调整后：{{ selectedScheduleTask.per_question_time_limit_seconds + Number(scheduleForm.item_extension_seconds) }} 秒</small></label>
              <label>调整原因<input v-model="scheduleForm.reason" maxlength="500" required></label>
            </div>
            <p v-if="selectedScheduleTask.total_time_limit_minutes != null || selectedScheduleTask.per_question_time_limit_seconds != null">实际可答至个人时限与本班截止时间中较早者。</p>
            <div class="kt-inline-actions"><Button type="submit" :disabled="working">保存调整</Button><button type="button" class="kt-text-button" @click="scheduleTaskId = undefined">取消</button></div>
          </form>
        </section>
        <aside class="kt-experiment-side-stack">
          <section class="kt-card kt-publish-ready-card"><h2>继续发布</h2><p>使用当前实验定义发布到尚未覆盖的班级。</p><Button data-action="continue-publish" :disabled="working" @click="startPublish">继续发布</Button></section>
          <section class="kt-card kt-publish-ready-card"><h2>数据中心</h2><p>查看和导出班级采集数据与相关指标。</p><a class="kt-button kt-button--secondary" :href="`/teacher/courses/${courseId}/data/`">查看采集数据</a></section>
        </aside>
      </div>
    </section>

    <section v-else id="experiment-panel-collection" role="tabpanel" aria-labelledby="experiment-tab-collection">
      <section class="kt-card kt-published-task-table" aria-labelledby="collection-status-title" data-collection-summary>
        <div class="kt-section-heading"><div><p class="kt-kicker">采集状态</p><h2 id="collection-status-title">班级任务进度</h2></div><span>{{ tasks.length }} 个</span></div>
        <EmptyState v-if="!tasks.length" title="暂无采集任务"><p>发布到班级后，服务端会在这里返回各任务的采集进度。</p></EmptyState>
        <div v-else class="kt-table-scroll"><table class="kt-table"><thead><tr><th>班级</th><th>状态</th><th>参与者</th><th>未开始</th><th>进行中</th><th>正常完成</th><th>已超时</th><th>已作废</th></tr></thead><tbody><tr v-for="task in tasks" :key="task.id"><td>{{ task.class_name }}</td><td>{{ task.business_status }}</td><td>{{ progress[task.id]?.total_participants ?? '—' }}</td><td>{{ progress[task.id]?.not_started ?? '—' }}</td><td>{{ progress[task.id]?.in_progress ?? '—' }}</td><td>{{ progress[task.id]?.completed ?? '—' }}</td><td>{{ progress[task.id]?.expired ?? '—' }}</td><td>{{ progress[task.id]?.invalidated ?? '—' }}</td></tr></tbody></table></div>
      </section>

      <section class="kt-card kt-published-task-table" aria-labelledby="collection-participants-title" data-collection-participants>
        <div class="kt-section-heading"><div><p class="kt-kicker">学生状态明细</p><h2 id="collection-participants-title">学生采集状态</h2></div><span>{{ collection?.page.total_items ?? '—' }} 人</span></div>
        <form class="kt-inline-form-grid" data-collection-filter @submit.prevent="applyCollectionFilters">
          <label for="collection-filter-class"><span>班级</span><select id="collection-filter-class" v-model="collectionClassId"><option value="">全部班级</option><option v-for="choice in collection?.class_choices || []" :key="choice.id" :value="String(choice.id)">{{ choice.name }}</option></select></label>
          <label for="collection-filter-query"><span>学生</span><input id="collection-filter-query" v-model="collectionQuery" type="search" placeholder="姓名或学号"></label>
          <label for="collection-filter-status"><span>状态</span><select id="collection-filter-status" v-model="collectionStatus"><option value="">全部状态</option><option v-for="choice in collection?.status_choices || []" :key="choice.value" :value="choice.value">{{ choice.label }}</option></select></label>
          <div class="kt-inline-actions"><Button type="submit" :disabled="collectionLoading">应用</Button><button class="kt-button kt-button--secondary" data-collection-clear type="button" :disabled="collectionLoading" @click="clearCollectionFilters">清除</button></div>
        </form>

        <Alert v-if="collectionError" tone="danger">{{ collectionError }}</Alert>
        <p v-else-if="collectionLoading" role="status">正在加载学生采集状态…</p>
        <EmptyState v-else-if="collection && !collection.items.length" title="当前筛选条件下没有学生"><button class="kt-button kt-button--secondary" data-collection-clear type="button" @click="clearCollectionFilters">清除筛选</button></EmptyState>
        <template v-else-if="collection">
          <div class="kt-table-scroll kt-desktop-only">
            <table class="kt-table">
              <thead><tr><th>学生</th><th>班级</th><th>状态</th><th>开始</th><th>结束</th><th>提交题数</th></tr></thead>
              <tbody><tr v-for="participant in collection.items" :key="participant.participant_id"><td><strong>{{ participant.display_name }}</strong><span class="kt-cell-note kt-mono">{{ participant.student_number }}</span></td><td>{{ participant.class_name }}</td><td><StatusBadge :label="participant.status_label" :tone="collectionTone(participant.status)" /></td><td data-collection-start>{{ collectionDate(participant.started_at) }}</td><td data-collection-end>{{ collectionDate(participant.ended_at) }}</td><td>提交 {{ participant.submission_count }} 题</td></tr></tbody>
            </table>
          </div>
          <MobileCardList class="kt-mobile-only">
            <li v-for="participant in collection.items" :key="participant.participant_id">
              <div class="kt-section-heading"><div><strong>{{ participant.display_name }}</strong><span class="kt-cell-note kt-mono">{{ participant.student_number }}</span></div><StatusBadge :label="participant.status_label" :tone="collectionTone(participant.status)" /></div>
              <dl class="kt-description-list"><div><dt>班级</dt><dd>{{ participant.class_name }}</dd></div><div><dt>开始</dt><dd data-collection-start>{{ collectionDate(participant.started_at) }}</dd></div><div><dt>结束</dt><dd data-collection-end>{{ collectionDate(participant.ended_at) }}</dd></div><div><dt>提交题数</dt><dd>提交 {{ participant.submission_count }} 题</dd></div></dl>
            </li>
          </MobileCardList>
          <Paginator :page="collection.page.number" :total-pages="collection.page.total_pages" @navigate="navigateCollection" />
        </template>
      </section>
    </section>
    <DeletionDialog v-if="deletionTarget" :course-id="courseId" :target="deletionTarget" @cancelled="deletionTarget = undefined" @completed="afterDeletion" />
  </template>
  <Alert v-else tone="danger">{{ error || '实验不存在或无法访问。' }}</Alert>
</template>
