<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import Icon from '@/design-system/Icon.vue'

import type { StudentLearningQuestion } from '../student-courses/api'
import { getStudentCourseApi } from '../student-courses/api'

const route = useRoute()
const courseId = Number(route.params.courseId)
const experimentId = Number(route.params.experimentId)
const questionId = Number(route.params.questionId)
const question = ref<StudentLearningQuestion>()
const loading = ref(true)
const error = ref('')
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
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '题目反馈加载失败。'
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <section class="kt-feedback-page kt-question-feedback">
    <nav class="kt-feedback-breadcrumbs" aria-label="面包屑">
      <RouterLink :to="`/student/courses/${courseId}/`">{{ question?.experiment_name || '实验' }}</RouterLink>
      <Icon name="chevron-right" :size="16" />
      <RouterLink :to="`/student/courses/${courseId}/learning/`">我的学习</RouterLink>
      <Icon name="chevron-right" :size="16" />
      <RouterLink :to="`/student/courses/${courseId}/learning/experiments/${experimentId}/`">实验结果</RouterLink>
      <Icon name="chevron-right" :size="16" />
      <span aria-current="page">{{ question?.code || '题目反馈' }}</span>
    </nav>

    <div class="kt-feedback-hero kt-question-feedback__hero">
      <div>
        <h1>{{ question?.code || '题目反馈' }}</h1>
        <p>{{ question?.experiment_name }}</p>
      </div>
      <RouterLink class="kt-feedback-back" :to="`/student/courses/${courseId}/learning/experiments/${experimentId}/`">返回实验结果</RouterLink>
    </div>

    <nav class="kt-feedback-tabs" aria-label="学习模块导航">
      <RouterLink :to="`/student/courses/${courseId}/`">实验任务</RouterLink>
      <RouterLink class="is-active" :to="`/student/courses/${courseId}/learning/`" aria-current="page">我的学习</RouterLink>
    </nav>

    <p v-if="error" class="kt-alert kt-alert--danger kt-feedback-alert" role="alert">{{ error }}</p>
    <p v-if="loading" class="kt-feedback-loading" role="status">正在加载题目反馈…</p>

    <template v-else-if="question">
      <p class="kt-feedback-generated">统计生成时间：{{ formatGeneratedAt(question.generated_at) }}</p>

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

      <article class="kt-question-feedback-card">
        <h2>题目反馈</h2>
        <ul v-if="question.kcs.length" class="kt-kc-tags" aria-label="知识点">
          <li class="kt-kc-tags__prefix"><span>KC</span>{{ question.kcs[0] }}</li>
          <li v-for="kc in question.kcs.slice(1)" :key="kc">{{ kc }}</li>
        </ul>

        <div class="kt-question-prompt">
          <span>题目</span>
          <h3>{{ question.text }}</h3>
        </div>

        <div v-if="question.options.length" class="kt-feedback-options">
          <div
            v-for="option in question.options"
            :key="option.value"
            class="kt-feedback-option"
            :class="{
              'kt-feedback-option--student': option.selected_by_student,
              'kt-feedback-option--correct': option.is_correct === true,
            }"
          >
            <span class="kt-feedback-option__label">{{ option.label }}</span>
            <span class="kt-feedback-option__text">{{ option.text }}</span>
            <span class="kt-feedback-option__badges">
              <span v-if="option.selected_by_student" class="kt-option-badge kt-option-badge--student"><Icon name="user" :size="17" /> 我的作答</span>
              <span v-if="option.is_correct === true" class="kt-option-badge kt-option-badge--correct"><Icon name="check" :size="17" /> 正确答案</span>
            </span>
          </div>
        </div>

        <div class="kt-feedback-answers" :class="{ 'has-full-feedback': hasFullFeedback }">
          <section class="kt-feedback-answer-panel kt-feedback-answer-panel--student">
            <h3><span><Icon name="user" :size="18" /></span>学生作答</h3>
            <p>{{ question.final_answer || '未作答' }}</p>
          </section>
          <template v-if="hasFullFeedback">
            <section class="kt-feedback-answer-panel kt-feedback-answer-panel--correct">
              <h3><span><Icon name="check" :size="18" /></span>正确答案</h3>
              <p>{{ question.correct_answer || '—' }}</p>
            </section>
            <section class="kt-feedback-answer-panel kt-feedback-answer-panel--analysis">
              <h3><span><Icon name="analysis" :size="18" /></span>答案解析</h3>
              <p>{{ question.explanation || '教师未填写解析' }}</p>
            </section>
          </template>
        </div>
      </article>
    </template>
  </section>
</template>
