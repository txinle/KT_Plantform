<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import type { StudentResult, StudentTask } from '../student-courses/api'
import { getStudentCourseApi } from '../student-courses/api'

const route = useRoute()
const courseId = Number(route.params.courseId)
const experimentId = Number(route.params.experimentId)
const task = ref<StudentTask>()
const result = ref<StudentResult>()
const loading = ref(true)
const error = ref('')
const completedLabel = computed(() => result.value?.completion_status === 'completed' ? '已完成' : result.value?.completion_status || '')
type FeedbackResult = Exclude<StudentResult, { feedback_mode: 'none' }>
type FullResult = Extract<StudentResult, { feedback_mode: 'full' }>
type FullResultItem = NonNullable<FullResult['items']>[number]
const feedbackResult = computed<FeedbackResult | undefined>(() => result.value?.feedback_mode === 'none' ? undefined : result.value)
const fullResult = computed<FullResult | undefined>(() => result.value?.feedback_mode === 'full' ? result.value : undefined)

function comparisonMessage(comparison: FeedbackResult['comparison']) {
  if (!comparison) return ''
  if (comparison.class_average?.status === 'unavailable' || comparison.rank?.status === 'unavailable') return '为保护同学隐私，当前样本量不足，暂不显示班级比较。'
  return ''
}

function fullItem(questionId: number): FullResultItem | undefined {
  return fullResult.value?.items?.find((item) => item.question_id === questionId)
}

onMounted(async () => {
  try {
    const tasks = await getStudentCourseApi().tasks(courseId)
    task.value = tasks.items.find((item) => item.experiment_id === experimentId)
    if (!task.value) throw new Error('未找到与你当前身份匹配的正式实验任务。')
    result.value = await getStudentCourseApi().result(task.value.task_id)
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '实验结果加载失败。'
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <section>
    <div class="kt-page-header">
      <div class="kt-page-header__copy">
        <p class="kt-kicker">Experiment record</p>
        <h1>{{ task?.experiment_name || '实验记录' }}</h1>
        <p v-if="result">{{ completedLabel }}<template v-if="result.completed_at"> · {{ new Date(result.completed_at).toLocaleString('zh-CN') }}</template></p>
      </div>
      <div class="kt-page-header__actions"><RouterLink :to="`/student/courses/${courseId}/learning/`" class="kt-button kt-button--secondary">返回我的学习</RouterLink></div>
    </div>
    <p v-if="error" class="kt-alert kt-alert--danger" role="alert">{{ error }}</p>
    <p v-if="loading" role="status">正在加载实验记录…</p>
    <template v-else-if="result">
      <section v-if="result.feedback_mode === 'none'" class="kt-card kt-result-notice">
        <h2>{{ completedLabel }}</h2><p>本实验未开放作答反馈</p>
      </section>
      <template v-else>
        <section v-if="feedbackResult?.score" class="kt-card kt-result-score" aria-labelledby="result-score-title">
          <div><p class="kt-kicker">Graded result</p><h2 id="result-score-title">得分</h2></div>
          <strong>{{ feedbackResult.score.earned }} / {{ feedbackResult.score.maximum }}</strong><span>{{ feedbackResult.score.percent }}%</span>
        </section>
        <p v-if="comparisonMessage(feedbackResult?.comparison)" class="kt-alert kt-alert--info">{{ comparisonMessage(feedbackResult?.comparison) }}</p>
        <section v-if="feedbackResult?.items?.length" class="kt-section kt-result-items" aria-labelledby="result-items-title">
          <div class="kt-section-heading"><h2 id="result-items-title">作答明细</h2></div>
          <article v-for="item in feedbackResult.items" :key="item.question_id" class="kt-card kt-result-item">
            <div class="kt-result-item__heading"><h3>{{ item.question_code }}</h3><span v-if="item.is_correct !== undefined && item.is_correct !== null" class="kt-status" :class="item.is_correct ? 'kt-status--success' : 'kt-status--danger'">{{ item.is_correct ? '正确' : '错误' }}</span></div>
            <dl class="kt-result-details">
              <div><dt>你的作答</dt><dd>{{ item.submitted_answer || '未作答' }}</dd></div>
              <div v-if="item.score !== undefined"><dt>本题得分</dt><dd>{{ item.score }}<template v-if="item.max_score !== undefined"> / {{ item.max_score }}</template></dd></div>
              <div v-if="fullItem(item.question_id)?.correct_answer !== undefined"><dt>正确答案</dt><dd>{{ fullItem(item.question_id)?.correct_answer || '—' }}</dd></div>
              <div v-if="fullItem(item.question_id)?.explanation"><dt>解析</dt><dd>{{ fullItem(item.question_id)?.explanation }}</dd></div>
            </dl>
          </article>
        </section>
      </template>
    </template>
  </section>
</template>
