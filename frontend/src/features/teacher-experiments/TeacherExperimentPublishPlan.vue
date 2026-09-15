<script setup lang="ts">
import { computed, inject, onMounted, reactive, ref, type Ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { ApiError } from '@/api/client'
import type { components } from '@/api/generated/schema'
import Alert from '@/design-system/components/Alert.vue'
import Button from '@/design-system/components/Button.vue'
import StatusBadge from '@/design-system/components/StatusBadge.vue'
import Icon from '@/design-system/Icon.vue'

import {
  getTeacherExperimentApi,
  type PublishOperationJob,
  type PublishParticipantCollection,
  type PublishPlan,
  type PublishPlanReview,
  type PublishPlanSetupInput,
  type TeacherExperiment,
} from './api'

type Participant = PublishParticipantCollection['items'][number] & { excluded?: boolean }
type ClassOption = { id: number; name: string; academic_year: string; student_count: number }

const route = useRoute()
const router = useRouter()
const api = getTeacherExperimentApi()
const courseId = Number(route.params.courseId)
const experimentId = Number(route.params.experimentId)
const planId = Number(route.params.planId)
const course = inject<Ref<components['schemas']['CourseContext'] | undefined>>(
  'teacher-experiments-course',
  ref<components['schemas']['CourseContext']>(),
)
const step = computed(() => Number(route.params.step) === 2 ? 2 : 1)
const plan = ref<PublishPlan>()
const review = ref<PublishPlanReview>()
const experiment = ref<TeacherExperiment>()
const classes = ref<ClassOption[]>([])
const participants = ref<Participant[]>([])
const selected = reactive<Record<number, boolean>>({})
const scheduleMode = reactive<Record<number, 'default' | 'custom'>>({})
const classOpensAt = reactive<Record<number, string>>({})
const classClosesAt = reactive<Record<number, string>>({})
const defaultSchedule = reactive({ opens_at: '', closes_at: '' })
const loading = ref(true)
const working = ref(false)
const error = ref('')
const notice = ref('')
const confirmCommit = ref(false)
const scheduleEditClassId = ref<number>()
const rosterExpanded = ref(false)
const activeJob = ref<PublishOperationJob>()
const courseName = computed(() => course?.value?.name || '课程')
const configuredScheduleCount = computed(() => review.value?.plan.classes.length ?? 0)
const jobStorageKey = `kt-publish-operation:${planId}`
const jobStageLabels: Record<string, string> = {
  queued: '等待后台处理',
  reading_history: '正在读取名单与历史记录',
  selecting_questions: '正在计算参与者题目',
  writing_preview: '正在写入发布检查结果',
  validating_preview: '正在核对发布检查结果',
  creating_release: '正在创建正式发布快照',
  completed: '处理完成',
  failed: '处理失败',
}

function newIdempotencyKey(operation: 'preview' | 'commit') {
  const suffix = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`
  return `${operation}-${planId}-${suffix}`
}

function rememberJob(job: PublishOperationJob | undefined) {
  activeJob.value = job
  if (job) sessionStorage.setItem(jobStorageKey, JSON.stringify({ job_id: job.job_id, operation: job.operation }))
  else sessionStorage.removeItem(jobStorageKey)
}

function wait(milliseconds: number) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds))
}

async function waitForJob(initial: PublishOperationJob) {
  let current = initial
  let interval = 1000
  while (current.status === 'queued' || current.status === 'running') {
    try {
      current = await api.operationJob(current.job_id)
      activeJob.value = current
      notice.value = ''
    } catch {
      notice.value = '后台任务仍在运行，正在重新连接…'
      await wait(interval)
      interval = Math.min(interval + 500, 3000)
      continue
    }
    if (current.status === 'queued' || current.status === 'running') {
      await wait(interval)
      interval = Math.min(interval + 500, 3000)
    }
  }
  if (current.status === 'succeeded') {
    rememberJob(undefined)
    return current
  }
  rememberJob(undefined)
  if (current.status === 'stale') {
    await load()
    throw new Error(current.error_message || '发布数据已变化，请重新运行发布前检查。')
  }
  throw new Error(current.error_message || '后台发布任务失败。')
}

async function resumeStoredJob() {
  const stored = sessionStorage.getItem(jobStorageKey)
  if (!stored) return
  try {
    const reference = JSON.parse(stored) as { job_id?: string; operation?: 'preview' | 'commit' }
    if (!reference.job_id || !reference.operation) {
      rememberJob(undefined)
      return
    }
    working.value = true
    const current = await api.operationJob(reference.job_id)
    const completed = await waitForJob(current)
    if (completed.operation === 'preview') {
      review.value = await api.review(planId)
      hydratePlan(review.value.plan)
      await loadParticipants()
      await router.push(`/teacher/courses/${courseId}/experiments/${experimentId}/publish-plan/${planId}/step/2/`)
    } else {
      await router.push(`/teacher/courses/${courseId}/experiments/${experimentId}/`)
    }
  } catch (caught) {
    await recover(caught, '后台发布任务状态恢复失败。')
  } finally {
    working.value = false
  }
}

function dateTimeLocal(value: string | null | undefined) {
  if (!value) return ''
  const date = new Date(value)
  const offset = date.getTimezoneOffset() * 60_000
  return new Date(date.getTime() - offset).toISOString().slice(0, 16)
}

function isoOrNull(value: string) {
  return value ? new Date(value).toISOString() : null
}

function hydratePlan(nextPlan: PublishPlan) {
  plan.value = nextPlan
  defaultSchedule.opens_at = dateTimeLocal(nextPlan.default_opens_at)
  defaultSchedule.closes_at = dateTimeLocal(nextPlan.default_closes_at)
  for (const option of classes.value) {
    selected[option.id] = false
    scheduleMode[option.id] = 'default'
    classOpensAt[option.id] = ''
    classClosesAt[option.id] = ''
  }
  for (const row of nextPlan.classes) {
    selected[row.class_id] = true
    scheduleMode[row.class_id] = row.schedule_mode === 'custom' ? 'custom' : 'default'
    classOpensAt[row.class_id] = dateTimeLocal(row.opens_at)
    classClosesAt[row.class_id] = dateTimeLocal(row.closes_at)
  }
}

async function loadParticipants() {
  const first = await api.participants(planId, 1)
  const rows = [...first.items] as Participant[]
  for (let pageNumber = 2; pageNumber <= first.page.total_pages; pageNumber += 1) {
    rows.push(...(await api.participants(planId, pageNumber)).items)
  }
  participants.value = rows
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [nextPlan, collection, definition] = await Promise.all([
      api.plan(planId),
      api.list(courseId, 1),
      api.detail(courseId, experimentId),
    ])
    classes.value = collection.summary.classes
    experiment.value = definition
    hydratePlan(nextPlan)
    if (step.value === 2) review.value = await api.review(planId)
    await loadParticipants()
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '发布计划加载失败。'
  } finally {
    loading.value = false
  }
}

function exclusionsFor(classId: number) {
  return participants.value
    .filter((participant) => participant.class_id === classId && participant.excluded)
    .map((participant) => participant.id)
}

function setupInput(): PublishPlanSetupInput {
  const selectedClasses = classes.value.filter((option) => selected[option.id])
  return {
    expected_revision: plan.value?.plan_revision || 1,
    classes: selectedClasses.map((option) => ({
      class_id: option.id,
      excluded_student_ids: exclusionsFor(option.id),
    })),
    default_schedule: {
      opens_at: isoOrNull(defaultSchedule.opens_at),
      closes_at: isoOrNull(defaultSchedule.closes_at),
    },
    schedules: selectedClasses
      .filter((option) => scheduleMode[option.id] === 'custom')
      .map((option) => scheduleMode[option.id] === 'custom'
        ? {
            class_id: option.id,
            mode: 'custom' as const,
            opens_at: isoOrNull(classOpensAt[option.id] || ''),
            closes_at: isoOrNull(classClosesAt[option.id] || ''),
          }
        : { class_id: option.id, mode: 'default' as const }),
  }
}

async function recover(caught: unknown, fallback: string) {
  if (caught instanceof ApiError && caught.code === 'PUBLISH_PLAN_STALE') {
    notice.value = '计划已在其他页面更新，已重新载入最新版本。'
    await load()
    return
  }
  if (caught instanceof ApiError) {
    const details = Object.values(caught.fields)
      .flatMap((value) => Array.isArray(value) ? value : [value])
      .map((value) => (
        value && typeof value === 'object' && 'message' in value
          ? String(value.message)
          : String(value)
      ))
      .filter(Boolean)
    error.value = details.join('；') || caught.message || fallback
    return
  }
  error.value = caught instanceof Error ? caught.message : fallback
}

async function saveDraft(showNotice = true) {
  working.value = true
  error.value = ''
  notice.value = ''
  try {
    const saved = await api.saveSetup(planId, setupInput())
    hydratePlan(saved)
    await loadParticipants()
    if (showNotice) notice.value = '草稿已保存。你可以退出后继续。'
    return saved
  } catch (caught) {
    await recover(caught, '发布设置保存失败。')
    return undefined
  } finally {
    working.value = false
  }
}

async function runPreview() {
  const saved = await saveDraft(false)
  if (!saved) return
  if (saved.blocking_issues.length) {
    error.value = saved.blocking_issues.map((issue) => issue.message).join('；')
    return
  }
  working.value = true
  try {
    const job = await api.preview(
      planId,
      saved.plan_revision,
      newIdempotencyKey('preview'),
    )
    rememberJob(job)
    await waitForJob(job)
    review.value = await api.review(planId)
    hydratePlan(review.value.plan)
    await loadParticipants()
    await router.push(`/teacher/courses/${courseId}/experiments/${experimentId}/publish-plan/${planId}/step/2/`)
  } catch (caught) {
    await recover(caught, '发布检查失败。')
  } finally {
    working.value = false
  }
}

function setExcluded(participant: Participant, excluded: boolean) {
  participant.excluded = excluded
}

function planClass(classId: number) {
  return plan.value?.classes.find((item) => item.class_id === classId)
}

function participantCount(option: ClassOption) {
  return planClass(option.id)?.participant_count ?? option.student_count
}

function exclusionCount(option: ClassOption) {
  return planClass(option.id)?.excluded_count
    ?? participants.value.filter((participant) => participant.class_id === option.id && participant.excluded).length
}

function scheduleLabel(option: ClassOption) {
  if (!selected[option.id]) return '未选择'
  return scheduleMode[option.id] === 'custom' ? '自定义排期' : '沿用批量排期'
}

function classScheduleTime(option: ClassOption, boundary: 'opens_at' | 'closes_at') {
  if (!selected[option.id]) return null
  if (scheduleMode[option.id] === 'custom') {
    return boundary === 'opens_at' ? classOpensAt[option.id] || null : classClosesAt[option.id] || null
  }
  return boundary === 'opens_at'
    ? defaultSchedule.opens_at || planClass(option.id)?.opens_at || null
    : defaultSchedule.closes_at || planClass(option.id)?.closes_at || null
}

function applyDefaultScheduleToSelected() {
  for (const option of classes.value) {
    if (!selected[option.id]) continue
    scheduleMode[option.id] = 'default'
  }
  scheduleEditClassId.value = undefined
}

function openScheduleEditor(classId: number) {
  scheduleMode[classId] ||= 'default'
  scheduleEditClassId.value = classId
}

function displayDate(value: string | null | undefined) {
  return value ? new Date(value).toLocaleString('zh-CN') : '未设置'
}

function displayLimit(value: number | null | undefined, unit: string) {
  return value === null || value === undefined ? '未设置' : `${value} ${unit}`
}

async function commit() {
  if (!plan.value || working.value) return
  working.value = true
  error.value = ''
  try {
    const job = await api.commit(
      planId,
      plan.value.plan_revision,
      newIdempotencyKey('commit'),
    )
    rememberJob(job)
    await waitForJob(job)
    await router.push(`/teacher/courses/${courseId}/experiments/${experimentId}/`)
  } catch (caught) {
    await recover(caught, '正式发布失败。')
  } finally {
    working.value = false
  }
}

onMounted(async () => {
  await load()
  await resumeStoredJob()
})
</script>

<template>
  <header class="kt-experiment-page-header">
    <div class="kt-experiment-page-header__copy">
      <nav class="kt-experiment-breadcrumb" aria-label="层级目录">
        <a href="/teacher/courses/">我的课程</a><span aria-hidden="true">/</span>
        <a :href="`/teacher/courses/${courseId}/`">{{ courseName }}</a><span aria-hidden="true">/</span>
        <a :href="`/teacher/courses/${courseId}/experiments/`">实验</a><span aria-hidden="true">/</span>
        <a :href="`/teacher/courses/${courseId}/experiments/${experimentId}/`">{{ experiment?.name || '实验' }}</a><span aria-hidden="true">/</span>
        <span aria-current="page">{{ step === 1 ? '发布到班级' : '发布确认' }}</span>
      </nav>
      <div class="kt-experiment-title-row"><h1>{{ step === 1 ? '发布到班级' : (confirmCommit ? '发布确认' : '发布前检查') }}</h1></div>
      <p>{{ step === 1 ? '先选择参与班级并设置开放时段，保存草稿不会运行发布检查。' : '检查当前发布计划，再确认创建正式班级任务。' }}</p>
    </div>
  </header>

  <ol class="kt-publish-stepper" aria-label="发布进度">
    <li :aria-current="step === 1 ? 'step' : undefined" :class="{ 'is-current': step === 1, 'is-complete': step === 2 }"><span>{{ step === 2 ? '✓' : '01' }}</span><strong>班级与排期</strong></li>
    <li :aria-current="step === 2 ? 'step' : undefined" :class="{ 'is-current': step === 2 }"><span>02</span><strong>发布确认</strong></li>
  </ol>

  <p v-if="loading" role="status">正在加载发布计划…</p>
  <Alert v-if="error" tone="danger">{{ error }}</Alert>
  <Alert v-if="notice" tone="success">{{ notice }}</Alert>
  <Alert v-if="activeJob" tone="info" role="status">
    {{ jobStageLabels[activeJob.stage] || '后台处理中' }}：
    {{ activeJob.processed }}/{{ activeJob.total }}
  </Alert>

  <template v-if="!loading && plan && step === 1">
    <Alert v-if="plan.blocking_issues.length" tone="danger">{{ plan.blocking_issues.map((issue) => issue.message).join('；') }}</Alert>
    <Alert v-else :tone="plan.setup_status === 'complete' ? 'success' : 'warning'">{{ plan.setup_status === 'complete' ? '设置完整，可以执行发布检查。' : '当前草稿尚不完整，仍可保存并稍后继续。' }}<span v-if="plan.missing_requirements.length">缺少：{{ plan.missing_requirements.join('、') }}</span></Alert>

    <section class="kt-card kt-publish-plan-panel" aria-labelledby="publish-classes">
      <div class="kt-publish-batch-schedule">
        <label for="publish-default-opens-at">批量设置开始<input id="publish-default-opens-at" v-model="defaultSchedule.opens_at" type="datetime-local"></label>
        <label for="publish-default-closes-at">批量设置截止<input id="publish-default-closes-at" v-model="defaultSchedule.closes_at" type="datetime-local"></label>
        <button data-action="apply-selected-schedule" class="kt-button kt-button--secondary" type="button" :disabled="!classes.some((option) => selected[option.id])" @click="applyDefaultScheduleToSelected">应用到已选班级</button>
        <p>留空开始表示立即开放；留空截止表示不设置截止。</p>
      </div>
      <div class="kt-section-heading"><div><h2 id="publish-classes">班级与排期</h2><p>计划版本 {{ plan.plan_revision }}</p></div><span>{{ classes.length }} 个可选班级</span></div>
      <div class="kt-table-scroll"><table class="kt-table kt-publish-class-table"><thead><tr><th>选择</th><th>班级</th><th>当前成员</th><th>默认参与</th><th>排除</th><th>开始</th><th>截止</th><th>排期状态</th><th>操作</th></tr></thead><tbody><tr v-for="option in classes" :key="option.id"><td><input :id="`publish-class-${option.id}`" v-model="selected[option.id]" type="checkbox" :aria-label="`选择 ${option.name}`"></td><td><label :for="`publish-class-${option.id}`"><strong>{{ option.academic_year }} {{ option.name }}</strong></label></td><td>{{ option.student_count }}</td><td>{{ participantCount(option) }}</td><td>{{ exclusionCount(option) }}</td><td>{{ displayDate(classScheduleTime(option, 'opens_at')) }}</td><td>{{ displayDate(classScheduleTime(option, 'closes_at')) }}</td><td><span class="kt-schedule-status">{{ scheduleLabel(option) }}</span></td><td><div class="kt-table-actions"><button v-if="participants.length" data-action="manage-exclusions" class="kt-text-button" type="button" @click="rosterExpanded = true">管理排除</button><span v-else class="kt-muted">保存后管理排除</span><button v-if="selected[option.id]" class="kt-text-button" type="button" @click="openScheduleEditor(option.id)">修改时间</button></div></td></tr></tbody></table></div>

      <div v-for="option in classes.filter((item) => selected[item.id] && scheduleEditClassId === item.id)" :key="option.id" class="kt-publish-schedule-editor">
        <div><strong>{{ option.academic_year }} {{ option.name }}</strong><span>{{ scheduleLabel(option) }}</span></div>
        <select v-model="scheduleMode[option.id]" :aria-label="`${option.name}排期方式`"><option value="default">沿用批量排期</option><option value="custom">自定义排期</option></select><div v-if="scheduleMode[option.id] === 'custom'" class="kt-form-grid"><label>开放时间<input v-model="classOpensAt[option.id]" type="datetime-local"></label><label>截止时间<input v-model="classClosesAt[option.id]" type="datetime-local"></label></div>
      </div>

      <section id="publish-roster" class="kt-publish-roster" aria-labelledby="publish-roster-title">
        <div class="kt-section-heading"><div><h3 id="publish-roster-title">参与者与排除名单</h3><p>名册由服务端按当前计划返回；保存草稿后可调整是否纳入。</p></div><button data-action="toggle-roster" class="kt-button kt-button--secondary" type="button" @click="rosterExpanded = !rosterExpanded">{{ rosterExpanded ? '收起名单' : `查看名单（${participants.length}）` }}</button></div>
        <template v-if="rosterExpanded">
          <p v-if="!participants.length" class="kt-publish-empty-row">选择班级并保存草稿后，服务端会返回该班当前有效名册。</p>
          <div v-else class="kt-table-scroll"><table class="kt-table"><thead><tr><th>学号</th><th>姓名</th><th>班级</th><th>参与</th></tr></thead><tbody><tr v-for="participant in participants" :key="`${participant.class_id}-${participant.id}`"><td>{{ participant.student_number }}</td><td>{{ participant.display_name }}</td><td>{{ participant.class_name }}</td><td><label class="kt-check-inline"><input type="checkbox" :checked="!participant.excluded" @change="setExcluded(participant, !($event.target as HTMLInputElement).checked)">纳入</label></td></tr></tbody></table></div>
        </template>
      </section>
    </section>

    <div class="kt-publish-actions"><a class="kt-button kt-button--secondary" :href="`/teacher/courses/${courseId}/experiments/${experimentId}/`">返回实验详情</a><div><button data-action="save-draft" class="kt-button kt-button--secondary" type="button" :disabled="working" @click="saveDraft()">保存草稿</button><Button data-action="run-preview" :disabled="working" @click="runPreview">运行发布前检查</Button></div></div>
  </template>

  <template v-if="!loading && plan && step === 2 && review">
    <section v-if="!confirmCommit" class="kt-publish-precheck" aria-labelledby="precheck-title">
      <div class="kt-publish-precheck__metrics"><div><span data-precheck-metric-icon class="kt-precheck-metric-icon"><Icon name="classes" :size="19" /></span><strong>{{ review.preview.class_count }}</strong><span>参与班级</span></div><div><span data-precheck-metric-icon class="kt-precheck-metric-icon"><Icon name="data" :size="19" /></span><strong>{{ review.preview.participant_count }}</strong><span>参与者</span></div><div><span data-precheck-metric-icon class="kt-precheck-metric-icon"><Icon name="definition" :size="19" /></span><strong>{{ review.preview.item_count }}</strong><span>分配题目</span></div><div><span data-precheck-metric-icon class="kt-precheck-metric-icon"><Icon name="publish" :size="19" /></span><strong>{{ configuredScheduleCount }}</strong><span>已配置排期</span></div><div><span data-precheck-metric-icon class="kt-precheck-metric-icon"><Icon name="status" :size="19" /></span><strong>{{ review.preview.randomization_validation }}</strong><span>随机化校验</span></div></div>
      <h2 id="precheck-title">发布前检查结果</h2>
      <section class="kt-card kt-precheck-results"><div class="kt-section-heading"><h3>系统检查结果</h3><StatusBadge label="检查完成" tone="success" /></div><ul class="kt-precheck-effects"><li v-for="effect in review.effects" :key="effect">{{ effect }}</li><li v-if="!review.effects.length">{{ review.preview.randomization_validation }}</li></ul><details class="kt-randomization-details"><summary>查看随机化详情</summary><p>{{ review.preview.randomization_validation }}</p><p>预览版本：{{ review.preview.plan_revision }}</p></details></section>
      <section class="kt-card kt-precheck-participants"><div class="kt-section-heading"><div><h3>参与者检查结果</h3><p>{{ experiment?.cross_experiment_item_policy === 'mixed_review' ? `混合复测：每人复测 ${experiment.review_count} 题` : (experiment?.cross_experiment_item_policy === 'exclude_previous' ? '排除已正式作答题目及其他有效任务题单' : '不限制历史题目') }}</p></div><a :href="api.participantExportUrl(planId)">导出 CSV</a></div><div class="kt-table-scroll"><table class="kt-table"><thead><tr><th>班级</th><th>学号</th><th>姓名</th><th>题数</th><th>复测题</th><th>新题</th><th>校验</th></tr></thead><tbody><tr v-for="participant in participants" :key="participant.id"><td>{{ participant.class_name }}</td><td>{{ participant.student_number }}</td><td>{{ participant.display_name }}</td><td>{{ participant.item_count }}</td><td>{{ participant.review_item_count }}</td><td>{{ participant.new_item_count }}</td><td>{{ participant.is_valid ? '有效' : '需处理' }}</td></tr></tbody></table></div></section>
      <Alert tone="info">本次预览使用服务端返回的发布计划和检查结果；正式提交会使用同一计划版本。</Alert>
      <div class="kt-publish-actions kt-publish-actions--center"><a class="kt-button kt-button--secondary" :href="`/teacher/courses/${courseId}/experiments/${experimentId}/publish-plan/${planId}/step/1/`">返回班级与排期</a><Button data-action="enter-confirmation" @click="confirmCommit = true">进入发布确认</Button></div>
    </section>

    <section v-else class="kt-publish-confirmation" aria-labelledby="confirmation-title">
      <h2 id="confirmation-title">发布确认</h2>
      <p>请确认本次将创建的班级任务；提交后将跳转回实验详情。</p>
      <div class="kt-publish-confirmation-grid">
        <section class="kt-card"><h3>实验定义</h3><dl v-if="experiment" class="kt-description-list kt-definition-summary__list"><div><dt>题库</dt><dd>{{ experiment.question_bank?.name || '未选择题库' }}</dd></div><div><dt>选题</dt><dd>{{ experiment.sampling_mode_label || experiment.selection_mode_label }}<span v-if="experiment.sampling_mode === 'total_count'"> · {{ experiment.sampling_total_count }} 题</span></dd></div><div><dt>跨实验题目</dt><dd>{{ experiment.cross_experiment_item_policy === 'mixed_review' ? `混合复测 · ${experiment.review_count} 题` : (experiment.cross_experiment_item_policy === 'exclude_previous' ? '排除已正式作答题目' : '不限制历史题目') }}</dd></div><div><dt>题目顺序</dt><dd>{{ experiment.ordering_mode_label }}</dd></div><div><dt>反馈</dt><dd>{{ experiment.feedback_mode_label }}</dd></div><div><dt>总限时</dt><dd>{{ displayLimit(experiment.total_time_limit_minutes, '分钟') }}</dd></div><div><dt>单题限时</dt><dd>{{ displayLimit(experiment.per_question_time_limit_seconds, '秒') }}</dd></div><div><dt>中断续答</dt><dd>{{ experiment.resume_mode_label }}</dd></div><div><dt>作答信心</dt><dd>{{ experiment.confidence_mode_label }}</dd></div></dl></section>
        <section class="kt-card"><h3>本次班级任务</h3><table class="kt-confirm-task-table"><thead><tr><th>班级</th><th>参与者</th><th>开放时段</th></tr></thead><tbody><tr v-for="row in review.plan.classes" :key="row.class_id"><td>{{ row.academic_year }} {{ row.class_name }}</td><td>{{ row.participant_count }}</td><td>{{ displayDate(row.opens_at) }} — {{ displayDate(row.closes_at) }}</td></tr></tbody></table></section>
      </div>
      <Alert tone="info">确认后将按本次检查过的发布计划创建正式班级任务和参与者快照。</Alert>
      <div class="kt-publish-actions kt-publish-actions--center"><button class="kt-button kt-button--secondary" type="button" @click="confirmCommit = false">返回检查</button><button data-action="commit-publish" class="kt-button" type="button" :disabled="working" @click="commit">{{ working ? '正在发布…' : '确认发布' }}</button></div>
    </section>
  </template>
</template>
