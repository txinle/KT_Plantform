<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import Icon from '@/design-system/Icon.vue'
import { createProcessRuntime, type StudentProcessRuntime } from '@/assessment-runtime/process-bridge'
import { getBootstrapContext } from '@/app/bootstrap'

import type { StudentLearningQuestion } from '../student-courses/api'
import { getStudentCourseApi } from '../student-courses/api'
import StudentQuestionDirectory from './StudentQuestionDirectory.vue'

const route = useRoute()
const courseId = Number(route.params.courseId)
const experimentId = Number(route.params.experimentId)
const questionId = Number(route.params.questionId)
const question = ref<StudentLearningQuestion>()
const loading = ref(true)
const error = ref('')
const answerExpanded = ref(true)
const explanationExpanded = ref(true)
let runtime: StudentProcessRuntime | undefined
let visitId: string | undefined
let left = false

async function exposure(stage: string) {
  const capture = question.value?.feedback_capture
  if (!capture || !runtime || !visitId) return
  await runtime.record('feedback_exposure', {
    capture_version: 'capture-v2', operation_id: visitId, feedback_mode: capture.mode, stage,
    answer_event_id: capture.answer_event_id, snapshot_id: capture.snapshot_id,
    included_correctness: capture.authorized.correctness,
    included_correct_answer: stage !== 'detail_entered' && answerExpanded.value && capture.authorized.correct_answer,
    included_explanation: stage !== 'detail_entered' && explanationExpanded.value && capture.authorized.explanation,
  })
}

async function expandAnswer() {
  answerExpanded.value = true
  await nextTick()
  try { await exposure('answer_presented') } catch { error.value = '反馈呈现记录未保存，请勿关闭页面。' }
}

async function expandExplanation() {
  explanationExpanded.value = true
  await nextTick()
  try { await exposure('explanation_presented') } catch { error.value = '解析呈现记录未保存，请勿关闭页面。' }
}

function leaveDetail() {
  if (left || !runtime) return
  left = true
  const current = runtime
  void exposure('detail_left').then(() => current.flushBounded(1500)).finally(() => current.dispose()).catch(() => undefined)
}
const hasFullFeedback = computed(() => {
  if (!question.value) return false
  return Object.prototype.hasOwnProperty.call(question.value, 'correct_answer')
    && Object.prototype.hasOwnProperty.call(question.value, 'explanation')
})

function formatGeneratedAt(value: string) {
  const parts = new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).formatToParts(new Date(value))
  const part = (type: Intl.DateTimeFormatPartTypes) => parts.find((item) => item.type === type)?.value || ''
  return `${part('year')}-${part('month')}-${part('day')} ${part('hour')}:${part('minute')}:${part('second')}`
}

onMounted(async () => {
  try {
    question.value = await getStudentCourseApi().learningQuestion(courseId, experimentId, questionId)
    if (question.value.feedback_capture) {
      answerExpanded.value = false
      explanationExpanded.value = false
      visitId = crypto.randomUUID()
      runtime = await createProcessRuntime(question.value.feedback_capture, 'submitted', getBootstrapContext().csrf_token)
      globalThis.addEventListener('pagehide', leaveDetail)
    }
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '题目反馈加载失败。'
  } finally {
    loading.value = false
    await nextTick()
    try { await exposure('detail_entered') } catch { error.value = '反馈进入记录未保存，请勿关闭页面。' }
  }
})
onBeforeUnmount(() => { leaveDetail(); globalThis.removeEventListener('pagehide', leaveDetail) })
</script>

<template>
  <section class="kt-feedback-page kt-question-feedback">
    <nav class="kt-feedback-breadcrumbs" aria-label="面包屑">
      <RouterLink :to="`/student/courses/${courseId}/`">{{ question?.experiment_name || '实验' }}</RouterLink>
      <Icon name="chevron-right" :size="16" />
      <RouterLink :to="`/student/courses/${courseId}/learning/`">我的学习</RouterLink>
      <Icon name="chevron-right" :size="16" />
      <RouterLink :to="`/student/courses/${courseId}/learning/experiments/${experimentId}/`">作答详情</RouterLink>
      <Icon name="chevron-right" :size="16" />
      <span aria-current="page">{{ question?.code || '题目反馈' }}</span>
    </nav>

    <header class="kt-feedback-page-header kt-question-feedback__header">
      <div>
        <h1>{{ question?.code || '题目反馈' }}</h1>
        <p>{{ question?.experiment_name }}</p>
      </div>
      <RouterLink class="kt-feedback-back" :to="`/student/courses/${courseId}/learning/experiments/${experimentId}/`">返回作答详情</RouterLink>
    </header>

    <p v-if="error" class="kt-alert kt-alert--danger kt-feedback-alert" role="alert">{{ error }}</p>
    <p v-if="loading" class="kt-feedback-loading" role="status">正在加载题目反馈…</p>

    <template v-else-if="question">
      <div class="kt-question-detail-layout">
        <div class="kt-question-detail-layout__main">
          <p class="kt-feedback-generated">统计生成时间：{{ formatGeneratedAt(question.generated_at) }}</p>

          <article class="kt-question-feedback-card">
            <h2>题目</h2>
            <ul v-if="question.kcs.length" class="kt-kc-tags" aria-label="知识点">
              <li class="kt-kc-tags__prefix"><span>KC</span>{{ question.kcs[0] }}</li>
              <li v-for="kc in question.kcs.slice(1)" :key="kc">{{ kc }}</li>
            </ul>

            <div class="kt-question-prompt">
              <h3>{{ question.text }}</h3>
            </div>

            <div v-if="question.options.length" class="kt-feedback-options">
              <div
                v-for="option in question.options"
                :key="option.value"
                class="kt-feedback-option"
                :class="{
                  'kt-feedback-option--student': option.selected_by_student,
                  'kt-feedback-option--correct': answerExpanded && option.is_correct === true,
                }"
              >
                <span class="kt-feedback-option__label">{{ option.label }}</span>
                <span class="kt-feedback-option__text">{{ option.text }}</span>
                <span class="kt-feedback-option__badges">
                  <span v-if="option.selected_by_student" class="kt-option-badge kt-option-badge--student"><Icon name="user" :size="17" /> 我的作答</span>
                  <span v-if="answerExpanded && option.is_correct === true" class="kt-option-badge kt-option-badge--correct"><Icon name="check" :size="17" /> 正确答案</span>
                </span>
              </div>
            </div>

            <div class="kt-feedback-answer-pair" :class="{ 'has-full-feedback': hasFullFeedback }">
              <section class="kt-feedback-answer-panel kt-feedback-answer-panel--student">
                <h3><span><Icon name="user" :size="18" /></span>我的正式作答</h3>
                <p>{{ question.final_answer || '未作答' }}</p>
              </section>
              <template v-if="hasFullFeedback">
                <section class="kt-feedback-answer-panel kt-feedback-answer-panel--correct">
                  <h3><span><Icon name="check" :size="18" /></span>正确答案</h3>
                  <button v-if="!answerExpanded" class="kt-button kt-button--secondary" type="button" data-show-answer @click="expandAnswer">展开正确答案</button>
                  <p v-else>{{ question.correct_answer || '—' }}</p>
                </section>
              </template>
            </div>
            <section v-if="hasFullFeedback" class="kt-feedback-answer-panel kt-feedback-answer-panel--analysis">
              <h3><span><Icon name="analysis" :size="18" /></span>答案解析</h3>
              <button v-if="!explanationExpanded" class="kt-button kt-button--secondary" type="button" data-show-explanation :disabled="!answerExpanded" @click="expandExplanation">展开解析</button>
              <p v-else>{{ question.explanation || '教师未填写解析' }}</p>
            </section>
          </article>
          <section class="kt-question-metrics" aria-label="题目结果">
            <article class="kt-feedback-metric">
              <span>结果</span>
              <strong>{{ question.result_label }}</strong>
            </article>
            <article v-if="question.score" class="kt-feedback-metric">
              <span>得分</span>
              <strong>{{ question.score.value }} / {{ question.score.maximum }}</strong>
            </article>
          </section>
        </div>
        <StudentQuestionDirectory :course-id="courseId" :experiment-id="experimentId" :question-id="questionId" />
      </div>
    </template>
  </section>
</template>
