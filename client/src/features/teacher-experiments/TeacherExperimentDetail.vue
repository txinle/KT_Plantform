<script setup lang="ts">
import { useExperimentPageUrl } from './experiment-page-url'
const pageUrl = useExperimentPageUrl()
import { computed, inject, onMounted, reactive, ref, watch, type Ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import type { components } from '@/api/generated/schema'
import ExperimentClassTaskTable from './ExperimentClassTaskTable.vue'
import { classTaskStatusLabel } from './class-task-labels'
import Alert from '@/design-system/components/Alert.vue'
import Button from '@/design-system/components/Button.vue'
import EmptyState from '@/design-system/components/EmptyState.vue'
import MobileCardList from '@/design-system/components/MobileCardList.vue'
import Paginator from '@/design-system/components/Paginator.vue'
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
import type { ResearchTools } from './research-tools'

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
const readinessLoading = ref(false)
const readinessError = ref('')
const tasksLoading = ref(false)
const tasksError = ref('')
const progressLoading = ref(false)
const progressError = ref('')
const working = ref(false)
const error = ref('')
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
let detailRequestId = 0

const courseName = computed(() => course?.value?.name || '课程')
const isPublished = computed(() => experiment.value?.status !== 'draft')
const researchTools = computed(() => experiment.value?.research_tools as Partial<ResearchTools> | undefined)
const enabledTools = computed(() => [researchTools.value?.doubt_enabled && '疑问标记', researchTools.value?.check_enabled && '答案检查', researchTools.value?.kc_visible && '知识点展示'].filter(Boolean).join('、'))
const overviewQuestionCount = computed(() => readiness.value?.requested_count ?? experiment.value?.sampling_total_count)
const questionTypeLabels: Record<string, string> = { single_choice: '单选题', multiple_choice: '多选题', true_false: '判断题', fill_blank: '填空题', short_answer: '简答题', numeric: '数值题', reading: '阅读理解', words: '选词填空', cloze: '短文填空', translation: '英汉互译', essay: '作文' }
const sharedClassTotalLimit = computed(() => {
  const limits = tasks.value.map(task => task.total_time_limit_minutes)
  return limits.length && limits[0] != null && limits.every(limit => limit === limits[0]) ? limits[0] : null
})
const revisedTotalLimit = computed(() =>
  isPublished.value && sharedClassTotalLimit.value != null
  && sharedClassTotalLimit.value !== experiment.value?.total_time_limit_minutes,
)

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

async function selectCollectionCount(task: ClassTask, status: string) {
  collectionClassId.value = String(task.class_id)
  collectionStatus.value = status
  collectionQuery.value = ''
  await applyCollectionFilters()
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
  const query = tab === 'tasks' ? { tab: 'tasks' } : {}
  replacingCollectionRoute = collectionRouteKey(query)
  await router.replace({ query })
  if (replacingCollectionRoute === collectionRouteKey(query)) replacingCollectionRoute = ''
}

async function loadReadiness(requestId: number) {
  try {
    const result = await api.readiness(courseId, experimentId)
    if (requestId === detailRequestId) readiness.value = result
  } catch (caught) {
    if (requestId === detailRequestId) readinessError.value = caught instanceof Error ? caught.message : '发布前检查加载失败。'
  } finally {
    if (requestId === detailRequestId) readinessLoading.value = false
  }
}

async function loadTasks(requestId: number) {
  try {
    const taskCollection = await api.classTasks(experimentId)
    if (requestId !== detailRequestId) return
    tasks.value = taskCollection.items
    tasksLoading.value = false
    progressLoading.value = tasks.value.length > 0
    await Promise.all(tasks.value.map(async (task) => {
      try {
        const row = await api.progress(task.id)
        if (requestId === detailRequestId) progress[task.id] = row
      } catch (caught) {
        if (requestId === detailRequestId) {
          progressError.value += `${task.class_name}：${caught instanceof Error ? caught.message : '班级进度加载失败。'} `
        }
      }
    }))
  } catch (caught) {
    if (requestId === detailRequestId) tasksError.value = caught instanceof Error ? caught.message : '班级任务加载失败。'
  } finally {
    if (requestId === detailRequestId) {
      tasksLoading.value = false
      progressLoading.value = false
    }
  }
}

async function load() {
  const requestId = ++detailRequestId
  loading.value = true
  detailReady.value = false
  error.value = ''
  readiness.value = undefined
  readinessError.value = ''
  readinessLoading.value = true
  tasksError.value = ''
  tasksLoading.value = true
  progressError.value = ''
  progressLoading.value = false
  for (const taskId of Object.keys(progress)) delete progress[Number(taskId)]
  void loadReadiness(requestId)
  void loadTasks(requestId)
  try {
    const definition = await api.detail(courseId, experimentId)
    if (requestId !== detailRequestId) return
    experiment.value = definition
    const requestedTab = queryText(route.query.tab)
    activeTab.value = requestedTab === 'tasks' || requestedTab === 'collection' ? requestedTab : 'definition'
    detailReady.value = true
    loading.value = false
    if (activeTab.value === 'collection') await loadCollection(collectionPage.value)
  } catch (caught) {
    if (requestId === detailRequestId) error.value = caught instanceof Error ? caught.message : '实验详情加载失败。'
  } finally {
    if (requestId === detailRequestId) loading.value = false
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
      : 'definition'
  },
  { deep: true },
)

async function startPublish() {
  working.value = true
  error.value = ''
  try {
    const plan = await api.startPlan(courseId, experimentId)
    await router.push(pageUrl(`/teacher/courses/${courseId}/experiments/${experimentId}/publish-plan/${plan.id}/step/${plan.current_step || 1}/`)!)
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '无法进入发布流程。'
  } finally {
    working.value = false
  }
}

async function afterDeletion() {
  deletionTarget.value = undefined
  await router.push(pageUrl(`/teacher/courses/${courseId}/experiments/`)!)
}

onMounted(load)
</script>

<template>
  <p v-if="loading" role="status">正在加载实验详情…</p>
  <template v-else-if="experiment">
    <header class="kt-experiment-page-header">
      <div class="kt-experiment-page-header__copy">
        <nav class="kt-experiment-breadcrumb" aria-label="层级目录">
          <a :href="pageUrl('/teacher/courses/')">我的课程</a><span aria-hidden="true">/</span>
          <a :href="pageUrl(`/teacher/courses/${courseId}/`)">{{ courseName }}</a><span aria-hidden="true">/</span>
          <a :href="pageUrl(`/teacher/courses/${courseId}/experiments/`)">实验</a><span aria-hidden="true">/</span>
          <span aria-current="page">{{ experiment.name }}</span>
        </nav>
        <div class="kt-experiment-title-row">
          <h1>{{ experiment.name }}</h1>
        </div>
        <p data-experiment-meta>{{ experiment.status_label }} · {{ overviewQuestionCount != null ? `${overviewQuestionCount} 道题` : '题量待确认' }} · {{ experiment.total_time_limit_minutes != null ? `实验 ${experiment.total_time_limit_minutes} 分钟` : '不限时' }} · {{ tasksLoading ? '正在加载班级' : `${tasks.length} 个班级` }}</p>
      </div>
      <div class="kt-experiment-page-header__actions">
        <a class="kt-button kt-button--secondary" :href="pageUrl(`/teacher/courses/${courseId}/experiments/`)">返回实验</a>
        <a v-if="experiment.can_edit" class="kt-button kt-button--secondary" :href="pageUrl(`/teacher/courses/${courseId}/experiments/${experimentId}/edit/`)">编辑实验</a>
        <Button v-if="experiment.can_publish && experiment.time_mode !== 'per_question'" aria-label="发布操作" :disabled="working" @click="startPublish">{{ isPublished ? '继续发布' : '发布到班级' }}</Button>
        <button v-if="experiment.can_manage" class="kt-text-button kt-text-button--danger" data-delete-experiment type="button" @click="deletionTarget = { kind: 'experiment', experimentId }">删除实验</button>
      </div>
    </header>

    <nav class="kt-experiment-tabs kt-sitev2-tabs" role="tablist" aria-label="实验详情导航" aria-orientation="horizontal">
      <button id="experiment-tab-definition" type="button" role="tab" :aria-selected="activeTab === 'definition'" aria-controls="experiment-panel-definition" :class="{ 'is-active': activeTab === 'definition' }" @click="selectTab('definition')">概况</button>
      <button id="experiment-tab-tasks" type="button" role="tab" :aria-selected="activeTab === 'tasks'" aria-controls="experiment-panel-tasks" :class="{ 'is-active': activeTab === 'tasks' }" @click="selectTab('tasks')">班级与排期</button>
      <button id="experiment-tab-collection" type="button" role="tab" :aria-selected="activeTab === 'collection'" aria-controls="experiment-panel-collection" :class="{ 'is-active': activeTab === 'collection' }" @click="selectTab('collection')">学生作答</button>
      <a v-if="experiment.activity?.grading_url" :href="pageUrl(experiment.activity.grading_url)">人工批改</a>
    </nav>

    <Alert v-if="error" tone="danger">{{ error }}</Alert>

    <section v-if="activeTab === 'definition'" id="experiment-panel-definition" class="kt-experiment-overview" role="tabpanel" aria-labelledby="experiment-tab-definition">
      <div class="kt-experiment-draft-grid">
        <div class="kt-definition-summary">
          <section class="kt-card" aria-labelledby="definition-summary">
            <div class="kt-section-heading"><h2 id="definition-summary">实验内容</h2></div>
            <dl class="kt-description-list kt-definition-summary__list kt-definition-content-facts">
              <div><span class="kt-summary-icon" data-summary-icon><Icon name="bank" :size="17" /></span><dt>题库</dt><dd>{{ experiment.question_banks?.map((bank) => bank.name).join('、') || experiment.question_bank?.name || '未选择题库' }}</dd></div>
              <div><span class="kt-summary-icon"><Icon name="definition" :size="17" /></span><dt>题型构成</dt><dd>{{ experiment.question_quotas?.filter(quota => quota.amount > 0).map(quota => `${questionTypeLabels[quota.question_type] || quota.question_type} ${quota.amount} 道`).join('、') || '未指定题型数量' }}</dd></div>
            </dl>
          </section>
          <section class="kt-card" aria-labelledby="definition-settings">
            <div class="kt-section-heading"><h2 id="definition-settings">作答、反馈与研究设置</h2></div>
            <dl class="kt-description-list kt-definition-summary__list kt-definition-settings-facts">
              <div><dt>抽题</dt><dd>{{ experiment.sampling_mode_label || experiment.selection_mode_label }}<template v-if="experiment.sampling?.pinned_question_ids?.length"> · 指定 {{ experiment.sampling.pinned_question_ids.length }} 道</template><template v-if="overviewQuestionCount != null"> · 每人 {{ overviewQuestionCount }} 道</template></dd></div>
              <div><span class="kt-summary-icon" data-summary-icon><Icon name="summary" :size="17" /></span><dt>题目顺序</dt><dd>{{ experiment.ordering_mode_label }}</dd></div>
              <div><span class="kt-summary-icon" data-summary-icon><Icon name="status" :size="17" /></span><dt>{{ revisedTotalLimit ? '当前各班总限时' : '总限时' }}</dt><dd>{{ displayLimit(revisedTotalLimit ? sharedClassTotalLimit : experiment.total_time_limit_minutes, '分钟') }}<small v-if="revisedTotalLimit">（发布时 {{ displayLimit(experiment.total_time_limit_minutes, '分钟') }}）</small><small v-else-if="isPublished && experiment.total_time_limit_minutes != null && tasks.length > 1 && sharedClassTotalLimit == null">（各班当前限时见“班级发布”）</small></dd></div>
              <div><span class="kt-summary-icon" data-summary-icon><Icon name="data" :size="17" /></span><dt>中断续答</dt><dd>{{ experiment.resume_mode_label }}</dd></div>
              <div><span class="kt-summary-icon" data-summary-icon><Icon name="summary" :size="17" /></span><dt>跨实验题目</dt><dd>{{ experiment.cross_experiment_item_policy === 'mixed_review' ? `混合复测 · ${experiment.review_count} 题` : (experiment.cross_experiment_item_policy === 'exclude_previous' ? '排除已提交或已分配题目' : '不限制历史题目') }}</dd></div>
              <div><span class="kt-summary-icon" data-summary-icon><Icon name="status" :size="17" /></span><dt>反馈</dt><dd>{{ experiment.feedback_mode_label }}</dd></div>
              <div><span class="kt-summary-icon" data-summary-icon><Icon name="status" :size="17" /></span><dt>反馈发布</dt><dd>{{ experiment.feedback_release_policy_label || '未设置' }}</dd></div>
              <div><span class="kt-summary-icon" data-summary-icon><Icon name="data" :size="17" /></span><dt>作答信心</dt><dd>{{ experiment.confidence_mode_label }}</dd></div>
              <div v-if="experiment.time_mode === 'per_question'"><span class="kt-summary-icon" data-summary-icon><Icon name="status" :size="17" /></span><dt>历史单题限时</dt><dd>{{ displayLimit(experiment.per_question_time_limit_seconds, '秒') }}</dd></div>
              <div v-if="experiment.confidence_sampling_unit && experiment.confidence_mode !== 'off'"><span class="kt-summary-icon"><Icon name="data" :size="17" /></span><dt>信心采样</dt><dd>{{ experiment.confidence_sampling_unit_label || '未设置' }}<template v-if="experiment.confidence_probability != null"> · {{ Number(experiment.confidence_probability) * 100 }}%</template></dd></div>
            </dl>
            <details v-if="experiment.sampling_kcs?.length || experiment.description" class="kt-definition-extra">
              <summary>知识点范围与实验说明</summary>
              <p v-if="experiment.description">{{ experiment.description }}</p>
              <dl class="kt-description-list kt-definition-summary__list">
                <div v-if="experiment.sampling_kcs?.length" class="kt-definition-summary__wide"><span class="kt-summary-icon"><Icon name="bank" :size="17" /></span><dt>知识点范围</dt><dd><span v-for="node in experiment.sampling_kcs" :key="node.kc_code" class="kt-cell-note">{{ node.kc_name || node.kc_code }} · {{ node.match_scope === 'any_level' ? '匹配任意层级' : '仅匹配主知识点' }}<template v-if="node.quota != null"> · {{ node.quota }} 道</template></span></dd></div>
                <div><dt>指定题目</dt><dd>{{ experiment.sampling?.pinned_question_ids?.length || 0 }} 道，其余按抽题配置补足</dd></div>
              </dl>
            </details>
            <div v-if="experiment.can_edit" class="kt-definition-summary__action">
              <a class="kt-button kt-button--secondary" :href="pageUrl(`/teacher/courses/${courseId}/experiments/${experimentId}/edit/`)">编辑实验定义</a>
            </div>
          </section>
          <Alert class="kt-definition-notice" data-definition-notice tone="info">{{ isPublished ? '实验定义已经冻结；如需调整内容，请按现有流程创建新的实验定义。' : '首次正式发布后，实验定义将冻结；后续可继续发布到其他班级。' }}</Alert>
        </div>
        <aside class="kt-experiment-side-stack">
          <section class="kt-card kt-overview-side-card" aria-labelledby="definition-schedule-title">
            <h2 id="definition-schedule-title" class="kt-overview-section-header">时间与发布</h2>
            <div class="kt-overview-side-body">
              <p v-if="tasksLoading" role="status">正在加载班级排期…</p><Alert v-else-if="tasksError" tone="danger">{{ tasksError }}</Alert>
              <dl v-else class="kt-overview-side-facts"><div><dt>开始时间</dt><dd>{{ tasks.length ? (tasks.every(task => task.opens_at === tasks[0]?.opens_at) ? (tasks[0]?.opens_at ? displayDate(tasks[0].opens_at) : '立即开放') : '各班分别安排') : '未安排' }}</dd></div><div><dt>截止时间</dt><dd>{{ tasks.length ? (tasks.every(task => task.closes_at === tasks[0]?.closes_at) ? (tasks[0]?.closes_at ? displayDate(tasks[0].closes_at) : '无截止') : '各班分别安排') : '未安排' }}</dd></div><div><dt>参与范围</dt><dd>{{ tasks.length }} 个班级 · {{ tasks.reduce((total, task) => total + task.participant_count, 0) }} 名学生</dd></div></dl>
              <button type="button" class="kt-button kt-button--secondary" @click="selectTab('tasks')">管理班级与排期</button>
            </div>
          </section>
          <section class="kt-card kt-overview-side-card kt-overview-research">
            <h2 class="kt-overview-section-header">研究工具与数据</h2>
            <dl class="kt-overview-side-facts kt-overview-side-body"><div><dt>已启用工具</dt><dd>{{ enabledTools || (researchTools && Object.keys(researchTools).length ? '未启用' : '未配置') }}</dd></div><div><dt>提示与规则</dt><dd>{{ researchTools?.hints?.length || 0 }} 条提示 · {{ researchTools?.rules?.length || 0 }} 条规则</dd></div></dl>
            <div class="kt-overview-links"><a v-if="experiment.activity?.grading_url" :href="pageUrl(experiment.activity.grading_url)">待批改：{{ experiment.activity.pending_grading_count }}</a><span v-else>待批改：{{ experiment.activity?.pending_grading_count ?? 0 }}</span><a :href="pageUrl(`/teacher/courses/${courseId}/data/`)">进入数据中心</a></div>
          </section>
          <section class="kt-card kt-overview-side-card kt-publish-ready-card" aria-labelledby="publish-ready-title">
            <h2 id="publish-ready-title" class="kt-overview-section-header">{{ isPublished ? '继续发布' : '发布准备' }}</h2>
            <div class="kt-overview-side-body">
              <div class="kt-readiness-card">
                <p v-if="readinessLoading" role="status">正在加载发布前检查…</p><Alert v-else-if="readinessError" tone="danger">{{ readinessError }}</Alert>
                <p v-if="readiness">当前候选 {{ readiness.candidate_count }} 道可用题目；发布前将检查每位学生的可用题量。</p>
                <Alert v-if="readiness && !readiness.ready" tone="warning"><ul><li v-for="issue in readiness.issues" :key="issue">{{ issue }}</li></ul></Alert>
                <details v-if="readiness" class="kt-readiness-details"><summary>发布前检查 · {{ readiness.ready ? '定义就绪' : '需要处理' }}</summary><ul class="kt-readiness-checklist"><li>{{ readiness.ready ? '实验定义已通过基础检查' : '实验定义仍需完成基础检查' }}</li><li>题库全局候选：{{ readiness.candidate_count }} 道可用题目，尚未按学生历史过滤</li><li>已关联参与者：{{ readiness.participant_count }}</li></ul></details>
              </div>
              <Alert v-if="experiment.time_mode === 'per_question'" tone="warning">历史单题计时实验不能继续发布；请复制为新实验并选择无时限或实验总时限。</Alert><Button v-else-if="experiment.can_publish" data-action="publish-from-definition" :disabled="working" @click="startPublish">{{ working ? '正在进入…' : (isPublished ? '继续发布' : '发布到班级') }}</Button>
            </div>
          </section>
        </aside>
      </div>
    </section>

    <section v-else-if="activeTab === 'tasks'" id="experiment-panel-tasks" role="tabpanel" aria-labelledby="experiment-tab-tasks">
      <Alert v-if="isPublished && !tasksLoading && !tasksError" tone="success">已发布 {{ tasks.length }} 个班级任务。任务状态和可用操作由服务端返回。</Alert>
      <div class="kt-published-layout">
        <section class="kt-card kt-published-task-table" aria-labelledby="class-task-title">
          <div class="kt-section-heading"><div><p class="kt-kicker">班级发布</p><h2 id="class-task-title">已发布班级任务</h2></div><span v-if="!tasksLoading && !tasksError">{{ tasks.length }} 个</span></div>
          <p v-if="progressLoading" role="status">正在加载班级进度…</p>
          <Alert v-if="progressError" tone="danger">{{ progressError }}</Alert>
          <p v-if="tasksLoading" role="status">正在加载班级任务…</p>
          <Alert v-else-if="tasksError" tone="danger">{{ tasksError }}</Alert>
          <EmptyState v-else-if="!tasks.length" title="尚未发布到班级"><p>完成发布检查并提交后，这里会显示每个班级的任务状态。</p></EmptyState>
          <ExperimentClassTaskTable v-if="tasks.length && !tasksLoading && !tasksError" :course-id="courseId" :experiment-id="experimentId" :tasks="tasks" :progress="progress" @changed="load" />
        </section>
        <aside class="kt-experiment-side-stack">
          <section class="kt-card kt-publish-ready-card"><h2>继续发布</h2><p>使用当前实验定义发布到尚未覆盖的班级。</p><Button v-if="experiment.can_publish" data-action="continue-publish" :disabled="working" @click="startPublish">继续发布</Button></section>
          <section class="kt-card kt-publish-ready-card"><h2>数据中心</h2><p>查看和导出班级采集数据与相关指标。</p><a class="kt-button kt-button--secondary" :href="pageUrl(`/teacher/courses/${courseId}/data/`)">查看采集数据</a></section>
        </aside>
      </div>
    </section>

    <section v-else id="experiment-panel-collection" role="tabpanel" aria-labelledby="experiment-tab-collection">
      <section class="kt-card kt-published-task-table" aria-labelledby="collection-status-title" data-collection-summary>
        <div class="kt-section-heading"><div><p class="kt-kicker">采集状态</p><h2 id="collection-status-title">采集总览</h2><p class="kt-cell-note">全班参与者统计；点击人数筛选下方记录。</p></div><span v-if="!tasksLoading && !tasksError">{{ tasks.length }} 个</span></div>
        <p v-if="progressLoading" role="status">正在加载班级进度…</p>
        <Alert v-if="progressError" tone="danger">{{ progressError }}</Alert>
        <p v-if="tasksLoading" role="status">正在加载班级任务…</p>
        <Alert v-else-if="tasksError" tone="danger">{{ tasksError }}</Alert>
        <EmptyState v-else-if="!tasks.length" title="暂无采集任务"><p>发布到班级后，服务端会在这里返回各任务的采集进度。</p></EmptyState>
        <div v-else class="kt-table-scroll"><table class="kt-table"><thead><tr><th>班级</th><th>状态</th><th>参与者</th><th>未开始</th><th>进行中</th><th>正常完成</th><th>已超时</th><th>已作废</th></tr></thead><tbody><tr v-for="task in tasks" :key="task.id"><td>{{ task.class_name }}</td><td>{{ classTaskStatusLabel(task.business_status) }}</td><td>{{ progress[task.id]?.total_participants ?? '—' }}</td><td><button type="button" class="kt-collection-count" :data-collection-count="`${task.id}:not_started`" :aria-label="`${task.class_name} · 未开始 · ${progress[task.id]?.not_started ?? 0} 人`" :aria-pressed="collectionClassId === String(task.class_id) && collectionStatus === 'not_started'" :disabled="!progress[task.id] || collectionLoading" @click="selectCollectionCount(task, 'not_started')">{{ progress[task.id]?.not_started ?? '—' }}</button></td><td><button type="button" class="kt-collection-count" :data-collection-count="`${task.id}:in_progress`" :aria-label="`${task.class_name} · 进行中 · ${progress[task.id]?.in_progress ?? 0} 人`" :aria-pressed="collectionClassId === String(task.class_id) && collectionStatus === 'in_progress'" :disabled="!progress[task.id] || collectionLoading" @click="selectCollectionCount(task, 'in_progress')">{{ progress[task.id]?.in_progress ?? '—' }}</button></td><td><button type="button" class="kt-collection-count" :data-collection-count="`${task.id}:completed`" :aria-label="`${task.class_name} · 正常完成 · ${progress[task.id]?.completed ?? 0} 人`" :aria-pressed="collectionClassId === String(task.class_id) && collectionStatus === 'completed'" :disabled="!progress[task.id] || collectionLoading" @click="selectCollectionCount(task, 'completed')">{{ progress[task.id]?.completed ?? '—' }}</button></td><td><button type="button" class="kt-collection-count" :data-collection-count="`${task.id}:expired`" :aria-label="`${task.class_name} · 已超时 · ${progress[task.id]?.expired ?? 0} 人`" :aria-pressed="collectionClassId === String(task.class_id) && collectionStatus === 'expired'" :disabled="!progress[task.id] || collectionLoading" @click="selectCollectionCount(task, 'expired')">{{ progress[task.id]?.expired ?? '—' }}</button></td><td><button type="button" class="kt-collection-count" :data-collection-count="`${task.id}:invalidated`" :aria-label="`${task.class_name} · 已作废 · ${progress[task.id]?.invalidated ?? 0} 人`" :aria-pressed="collectionClassId === String(task.class_id) && collectionStatus === 'invalidated'" :disabled="!progress[task.id] || collectionLoading" @click="selectCollectionCount(task, 'invalidated')">{{ progress[task.id]?.invalidated ?? '—' }}</button></td></tr></tbody></table></div>
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
    <DeletionDialog v-if="deletionTarget" :page-url="pageUrl" :course-id="courseId" :target="deletionTarget" @cancelled="deletionTarget = undefined" @completed="afterDeletion" />
  </template>
  <Alert v-else tone="danger">{{ error || '实验不存在或无法访问。' }}</Alert>
</template>
