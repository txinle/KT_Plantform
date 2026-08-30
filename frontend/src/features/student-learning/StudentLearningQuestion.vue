<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import type { StudentLearningQuestion } from '../student-courses/api'
import { getStudentCourseApi } from '../student-courses/api'

const route = useRoute()
const courseId = Number(route.params.courseId)
const experimentId = Number(route.params.experimentId)
const questionId = Number(route.params.questionId)
const question = ref<StudentLearningQuestion>()
const loading = ref(true)
const error = ref('')

onMounted(async () => {
  try {
    question.value = await getStudentCourseApi().learningQuestion(courseId, experimentId, questionId)
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '题目记录加载失败。'
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <section>
    <div class="kt-page-header">
      <div class="kt-page-header__copy"><p class="kt-kicker">Question record</p><h1>{{ question?.code || '题目记录' }}</h1><p>{{ question?.experiment_name }}</p></div>
      <div class="kt-page-header__actions"><RouterLink class="kt-button kt-button--secondary" :to="`/student/courses/${courseId}/learning/experiments/${experimentId}/`">返回实验记录</RouterLink></div>
    </div>
    <p v-if="error" class="kt-alert kt-alert--danger" role="alert">{{ error }}</p>
    <p v-if="loading" role="status">正在加载题目记录…</p>
    <article v-else-if="question" class="kt-card kt-question-record">
      <h2>{{ question.text }}</h2>
      <dl class="kt-result-details">
        <div><dt>作答结果</dt><dd>{{ question.result_label }}</dd></div>
        <div><dt>你的作答</dt><dd>{{ question.final_answer || '未作答' }}</dd></div>
        <div v-if="question.correct_answer !== undefined"><dt>正确答案</dt><dd>{{ question.correct_answer || '—' }}</dd></div>
        <div v-if="question.explanation"><dt>解析</dt><dd>{{ question.explanation }}</dd></div>
      </dl>
    </article>
  </section>
</template>
