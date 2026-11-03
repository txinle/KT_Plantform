<script setup lang="ts">
import { useExperimentPageUrl } from './experiment-page-url'
const pageUrl = useExperimentPageUrl()
import { computed, nextTick, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { ApiError, MutationOutcomeUnknownError } from '@/api/client'

import {
  getTeacherExperimentApi,
  type ClassTask,
  type GradingStatusFilter,
  type ManualGradingOverview,
  type ManualGradingQuestionDetail,
  type ManualGradingSubmission,
} from './api'

const route = useRoute()
const router = useRouter()
const api = getTeacherExperimentApi()
const courseId = Number(route.params.courseId)
const experimentId = Number(route.params.experimentId)
const taskId = computed(() => Number(route.params.taskId))
const classTasks = ref<ClassTask[]>([])
const gradingClasses = computed(() => classTasks.value.filter(task => task.available_actions.includes('grade_short_answers')))
let loadVersion = 0
let questionVersion = 0

const overview = ref<ManualGradingOverview>()
const detail = ref<ManualGradingQuestionDetail>()
const selectedQuestionId = ref<number>()
const statusFilter = ref<GradingStatusFilter>('pending')
const page = ref(1)
const loading = ref(true)
const savingSubmissionId = ref<number>()
const error = ref('')
const notice = ref('')
const comments = reactive<Record<number, string>>({})
const questionList = ref<HTMLElement>()
const referenceContent = ref<HTMLElement>()
const submissionList = ref<HTMLElement>()

const rows = computed(() => detail.value?.submissions.items || [])

function answerText(answer: unknown) {
  if (answer === null || answer === undefined || answer === '') return '（空答案）'
  if (typeof answer === 'string') return answer
  return JSON.stringify(answer, null, 2)
}

function displayDate(value: string | null) {
  return value ? new Date(value).toLocaleString('zh-CN') : '—'
}

async function loadQuestion(snapshotId: number, nextPage = 1) {
  const requestVersion = ++questionVersion
  const currentTaskId = taskId.value
  const questionChanged = selectedQuestionId.value !== snapshotId
  selectedQuestionId.value = snapshotId
  page.value = nextPage
  const result = await api.gradingQuestion(currentTaskId, snapshotId, statusFilter.value, nextPage)
  if (requestVersion !== questionVersion || currentTaskId !== taskId.value) return
  detail.value = result
  for (const row of detail.value.submissions.items) {
    comments[row.submission_id] = ''
  }
  await nextTick()
  if (questionChanged && referenceContent.value) referenceContent.value.scrollTop = 0
  if (submissionList.value) submissionList.value.scrollTop = 0
}

async function load() {
  const version = ++loadVersion
  loading.value = true
  error.value = ''
  try {
    const [result, tasks] = await Promise.all([api.grading(taskId.value), api.classTasks(experimentId)])
    if (version !== loadVersion) return
    overview.value = result
    classTasks.value = tasks.items
    const preferred = overview.value.questions.find(
      question => question.question_snapshot_id === selectedQuestionId.value,
    ) || overview.value.questions[0]
    if (preferred) await loadQuestion(preferred.question_snapshot_id)
    else detail.value = undefined
  } catch (caught) {
    if (version === loadVersion) error.value = caught instanceof Error ? caught.message : '批改任务加载失败。'
  } finally {
    if (version === loadVersion) {
      loading.value = false
      await nextTick()
      const list = questionList.value
      const selected = list?.querySelector<HTMLElement>('[aria-pressed="true"]')
      if (list && selected) {
        const bounds = list.getBoundingClientRect()
        const item = selected.getBoundingClientRect()
        if (item.top < bounds.top || item.bottom > bounds.bottom) {
          list.scrollTop += item.top - bounds.top - (list.clientHeight - item.height) / 2
        }
      }
    }
  }
}

async function changeFilter(value: GradingStatusFilter) {
  statusFilter.value = value
  if (!selectedQuestionId.value) return
  try {
    await loadQuestion(selectedQuestionId.value)
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '批改列表加载失败。'
  }
}

async function grade(row: ManualGradingSubmission, isCorrect: boolean) {
  const comment = comments[row.submission_id] || ''
  if (row.current_is_correct !== null && !comment.trim()) {
    error.value = '修改已生效判定时必须填写原因。'
    return
  }
  savingSubmissionId.value = row.submission_id
  error.value = ''
  notice.value = ''
  try {
    const result = await api.gradeSubmission(
      taskId.value,
      selectedQuestionId.value as number,
      row.submission_id,
      {
        expected_grade_version: row.latest_grade_version,
        is_correct: isCorrect,
        comment,
      },
    )
    if (overview.value) overview.value.totals = result.task_progress
    await load()
    notice.value = '判定已保存。'
    await nextTick()
    document.querySelector<HTMLElement>('[data-grading-row] button')?.focus()
  } catch (caught) {
    if (caught instanceof ApiError && caught.code === 'GRADE_VERSION_CONFLICT') {
      await load()
      error.value = '其他教师已经修改了这条记录，已加载服务器最新判定。'
    } else if (caught instanceof MutationOutcomeUnknownError) {
      await load()
      error.value = '网络响应中断，已重新读取服务器记录，请核对当前判定。'
    } else {
      error.value = caught instanceof Error ? caught.message : '判定保存失败。'
    }
  } finally {
    savingSubmissionId.value = undefined
  }
}

function defer(row: ManualGradingSubmission) {
  const index = rows.value.findIndex(item => item.submission_id === row.submission_id)
  const next = document.querySelectorAll<HTMLElement>('[data-grading-row]')[index + 1]
  next?.querySelector<HTMLElement>('button')?.focus()
}

function switchClass(event: Event) {
  const id = Number((event.target as HTMLSelectElement).value)
  if (id === taskId.value || !gradingClasses.value.some(task => task.id === id)) return
  void router.push(pageUrl(`/teacher/courses/${courseId}/experiments/${experimentId}/class-tasks/${id}/grading/`))
}

watch(taskId, () => {
  ++questionVersion
  overview.value = undefined
  detail.value = undefined
  selectedQuestionId.value = undefined
  statusFilter.value = 'pending'
  page.value = 1
  notice.value = ''
  for (const key of Object.keys(comments)) delete comments[Number(key)]
  void load()
}, { immediate: true })
</script>

<template>
  <div class="kt-grading-screen">
    <header class="kt-experiment-page-header kt-grading-header">
      <div class="kt-experiment-page-header__copy">
        <nav class="kt-experiment-breadcrumb" aria-label="层级目录">
          <a :href="pageUrl(`/teacher/courses/${courseId}/experiments/${experimentId}/`)">实验详情</a>
          <span aria-hidden="true">/</span><span aria-current="page">人工批改</span>
        </nav>
        <div class="kt-grading-heading">
          <h1>人工批改</h1>
          <div class="kt-grading-context">
            <span v-if="overview">{{ overview.task.experiment_name }}</span>
            <select v-if="gradingClasses.length" class="kt-grading-class-switch" :value="taskId" aria-label="切换班级" :disabled="loading || savingSubmissionId !== undefined" @change="switchClass">
              <option v-for="task in gradingClasses" :key="task.id" :value="task.id">{{ task.class_name }} · 待批改：{{ task.grading.pending ?? 0 }}</option>
            </select>
          </div>
        </div>
      </div>
      <a class="kt-button kt-button--secondary" :href="pageUrl(`/teacher/courses/${courseId}/experiments/${experimentId}/`)">返回实验</a>
    </header>

    <p v-if="loading" role="status">正在加载批改任务…</p>
    <div v-else class="kt-grading-page">
      <p v-if="error" class="kt-alert kt-alert--danger" role="alert">{{ error }}</p>
      <p v-if="notice" class="kt-alert kt-alert--success" role="status">{{ notice }}</p>

      <div v-if="overview" class="kt-grading-summary" aria-label="批改进度">
        <div><span>人工批改题目</span><strong>{{ overview.totals.short_answer_questions }}</strong></div>
        <div><span>已提交</span><strong>{{ overview.totals.submitted }}</strong></div>
        <div><span>待批改</span><strong>{{ overview.totals.pending }}</strong></div>
        <div><span>已批改</span><strong>{{ overview.totals.graded }}</strong></div>
      </div>

      <div v-if="overview?.questions.length" class="kt-grading-layout">
        <aside class="kt-card kt-grading-questions" aria-label="人工批改题目列表">
          <header class="kt-grading-panel-heading">
            <h2>题目</h2><small>{{ overview.questions.length }} 题</small>
          </header>
          <div ref="questionList" class="kt-grading-question-list">
            <button
              v-for="question in overview.questions"
              :key="question.question_snapshot_id"
              type="button"
              :class="{ 'is-active': selectedQuestionId === question.question_snapshot_id }"
              :aria-pressed="selectedQuestionId === question.question_snapshot_id"
              @click="loadQuestion(question.question_snapshot_id)"
            >
              <strong>{{ question.code }}</strong>
              <span :class="{ 'has-pending': question.pending_count > 0 }">待批 {{ question.pending_count }}</span>
            </button>
          </div>
        </aside>

        <section v-if="detail" class="kt-card kt-grading-reference-panel" aria-label="题目与参考">
          <header class="kt-grading-panel-heading">
            <h2>题目与参考</h2><small>{{ detail.question.code }}</small>
          </header>
          <div ref="referenceContent" class="kt-grading-reference">
            <p class="kt-kicker">{{ detail.question.code }}</p>
            <h3>题干</h3><p>{{ detail.question.text }}</p>
            <h3>参考答案</h3><pre>{{ answerText(detail.question.correct_answer) }}</pre>
            <template v-if="detail.question.explanation">
              <h3>解析</h3><p>{{ detail.question.explanation }}</p>
            </template>
          </div>
        </section>

        <section v-if="detail" class="kt-card kt-grading-workspace" aria-label="学生作答与批改">
          <div class="kt-grading-toolbar" aria-label="筛选作答">
            <button
              v-for="option in ([['all', '全部'], ['pending', '待批改'], ['completed', '已批改']] as const)"
              :key="option[0]"
              type="button"
              :aria-pressed="statusFilter === option[0]"
              @click="changeFilter(option[0])"
            >
              {{ option[1] }}
            </button>
          </div>

          <div ref="submissionList" class="kt-grading-submissions">
            <p v-if="!rows.length" class="kt-grading-empty">当前筛选下没有正式提交的答案。</p>
            <article v-for="row in rows" :key="row.submission_id" class="kt-grading-row" data-grading-row>
              <header>
                <div><strong>{{ row.display_name }}</strong><span>{{ row.student_number }}</span></div>
                <span v-if="row.current_is_correct !== null" :class="row.current_is_correct ? 'is-correct' : 'is-incorrect'">
                  {{ row.current_is_correct ? '当前判定：正确' : '当前判定：错误' }}
                </span>
                <span v-else>待教师批改</span>
              </header>
              <pre class="kt-grading-answer">{{ answerText(row.final_answer) }}</pre>
              <p class="kt-grading-meta">提交于 {{ displayDate(row.submitted_at) }}<template v-if="row.graded_at"> · {{ row.grader_name }}批改于 {{ displayDate(row.graded_at) }}</template></p>
              <p v-if="row.current_comment" class="kt-grading-meta">当前备注：{{ row.current_comment }}</p>
              <label v-if="row.current_is_correct !== null">
                修改原因 <span aria-hidden="true">*</span>
                <textarea v-model="comments[row.submission_id]" rows="2" maxlength="2000" required />
              </label>
              <label v-else>
                批改备注（可选）
                <textarea v-model="comments[row.submission_id]" rows="2" maxlength="2000" />
              </label>
              <div class="kt-grading-actions">
                <button type="button" class="kt-button kt-button--danger" data-grade="incorrect" :disabled="savingSubmissionId === row.submission_id" @click="grade(row, false)">判为错误</button>
                <button type="button" class="kt-button" data-grade="correct" :disabled="savingSubmissionId === row.submission_id" @click="grade(row, true)">判为正确</button>
                <button type="button" class="kt-text-button" @click="defer(row)">稍后处理</button>
              </div>
            </article>
          </div>

          <nav v-if="detail.submissions.page.total_pages > 1" class="kt-grading-pagination" aria-label="批改分页">
            <button type="button" :disabled="page <= 1" @click="loadQuestion(selectedQuestionId as number, page - 1)">上一页</button>
            <span>第 {{ page }} / {{ detail.submissions.page.total_pages }} 页</span>
            <button type="button" :disabled="page >= detail.submissions.page.total_pages" @click="loadQuestion(selectedQuestionId as number, page + 1)">下一页</button>
          </nav>
        </section>
      </div>
      <p v-else-if="overview" class="kt-card kt-grading-empty">当前任务还没有可人工批改的正式提交。</p>
    </div>
  </div>
</template>
