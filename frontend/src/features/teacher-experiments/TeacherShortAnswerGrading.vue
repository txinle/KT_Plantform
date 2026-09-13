<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'

import { ApiError, MutationOutcomeUnknownError } from '@/api/client'

import {
  getTeacherExperimentApi,
  type GradingStatusFilter,
  type ManualGradingOverview,
  type ManualGradingQuestionDetail,
  type ManualGradingSubmission,
} from './api'

const route = useRoute()
const api = getTeacherExperimentApi()
const courseId = Number(route.params.courseId)
const experimentId = Number(route.params.experimentId)
const taskId = Number(route.params.taskId)

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
  selectedQuestionId.value = snapshotId
  page.value = nextPage
  detail.value = await api.gradingQuestion(taskId, snapshotId, statusFilter.value, nextPage)
  for (const row of detail.value.submissions.items) {
    comments[row.submission_id] = ''
  }
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    overview.value = await api.grading(taskId)
    const preferred = overview.value.questions.find(
      question => question.question_snapshot_id === selectedQuestionId.value,
    ) || overview.value.questions[0]
    if (preferred) await loadQuestion(preferred.question_snapshot_id)
    else detail.value = undefined
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '批改任务加载失败。'
  } finally {
    loading.value = false
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
      taskId,
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

onMounted(load)
</script>

<template>
  <header class="kt-experiment-page-header kt-grading-header">
    <div class="kt-experiment-page-header__copy">
      <nav class="kt-experiment-breadcrumb" aria-label="层级目录">
        <a :href="`/teacher/courses/${courseId}/experiments/${experimentId}/`">实验详情</a>
        <span aria-hidden="true">/</span><span aria-current="page">简答题批改</span>
      </nav>
      <h1>简答题批改</h1>
      <p v-if="overview">{{ overview.task.experiment_name }} · {{ overview.task.class_name }}</p>
    </div>
    <a class="kt-button kt-button--secondary" :href="`/teacher/courses/${courseId}/experiments/${experimentId}/`">返回实验</a>
  </header>

  <p v-if="loading" role="status">正在加载批改任务…</p>
  <div v-else class="kt-grading-page">
    <p v-if="error" class="kt-alert kt-alert--danger" role="alert">{{ error }}</p>
    <p v-if="notice" class="kt-alert kt-alert--success" role="status">{{ notice }}</p>

    <div v-if="overview" class="kt-grading-summary" aria-label="批改进度">
      <div><span>简答题</span><strong>{{ overview.totals.short_answer_questions }}</strong></div>
      <div><span>已提交</span><strong>{{ overview.totals.submitted }}</strong></div>
      <div><span>待批改</span><strong>{{ overview.totals.pending }}</strong></div>
      <div><span>已批改</span><strong>{{ overview.totals.graded }}</strong></div>
    </div>

    <div v-if="overview?.questions.length" class="kt-grading-layout">
      <aside class="kt-card kt-grading-questions" aria-label="简答题列表">
        <h2>题目</h2>
        <button
          v-for="question in overview.questions"
          :key="question.question_snapshot_id"
          type="button"
          :class="{ 'is-active': selectedQuestionId === question.question_snapshot_id }"
          @click="loadQuestion(question.question_snapshot_id)"
        >
          <strong>{{ question.code }}</strong>
          <span>{{ question.text_summary }}</span>
          <small>待批改 {{ question.pending_count }} · 已批改 {{ question.graded_count }}</small>
        </button>
      </aside>

      <main v-if="detail" class="kt-grading-workspace">
        <section class="kt-card kt-grading-reference">
          <p class="kt-kicker">{{ detail.question.code }}</p>
          <h2>冻结题目与参考信息</h2>
          <h3>题干</h3><p>{{ detail.question.text }}</p>
          <h3>参考答案</h3><pre>{{ answerText(detail.question.correct_answer) }}</pre>
          <template v-if="detail.question.explanation">
            <h3>解析</h3><p>{{ detail.question.explanation }}</p>
          </template>
        </section>

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

        <p v-if="!rows.length" class="kt-card kt-grading-empty">当前筛选下没有正式提交的简答题答案。</p>
        <article v-for="row in rows" :key="row.submission_id" class="kt-card kt-grading-row" data-grading-row>
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

        <nav v-if="detail.submissions.page.total_pages > 1" class="kt-grading-pagination" aria-label="批改分页">
          <button type="button" :disabled="page <= 1" @click="loadQuestion(selectedQuestionId as number, page - 1)">上一页</button>
          <span>第 {{ page }} / {{ detail.submissions.page.total_pages }} 页</span>
          <button type="button" :disabled="page >= detail.submissions.page.total_pages" @click="loadQuestion(selectedQuestionId as number, page + 1)">下一页</button>
        </nav>
      </main>
    </div>
    <p v-else-if="overview" class="kt-card kt-grading-empty">当前任务还没有可批改的正式简答题提交。</p>
  </div>
</template>
