<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import type { MetricDisplay, StudentLearningComparison, StudentLearningSummary } from '../student-courses/api'
import { getStudentCourseApi } from '../student-courses/api'
import { useComparisonPolling } from '../student-learning/useComparisonPolling'

const route = useRoute()
const courseId = Number(route.params.courseId)
const summary = ref<StudentLearningSummary>()
const loading = ref(true)
const error = ref('')

const checks = useComparisonPolling(() => summary.value,
  () => Boolean(summary.value?.comparisons?.correct_rate?.class_average || summary.value?.comparisons?.score_rate?.class_average
    || summary.value?.kc_rows?.some((row) => row.class_average) || summary.value?.trend_rows?.some((row) => row.class_average)),
  () => getStudentCourseApi().learningStatus(courseId),
  async () => { summary.value = await getStudentCourseApi().learning(courseId) },
)
const refreshing = ref(false)
async function refresh() {
  refreshing.value = true
  try { await checks.refresh(); error.value = '' }
  catch (caught) { error.value = caught instanceof Error ? caught.message : '学习记录刷新失败。' }
  finally { refreshing.value = false }
}

function statusClass(experiment: StudentLearningSummary['experiments'][number]) {
  if (experiment.completed || experiment.status_label === '已完成') return 'kt-status--success'
  if (experiment.participated) return 'kt-status--warning'
  return 'kt-status--neutral'
}

function metricText(metric: MetricDisplay | undefined) {
  return metric?.percent_display || '—'
}

function classAverage(comparison: StudentLearningComparison | undefined) {
  return comparison?.class_average?.percent_display
}

onMounted(async () => {
  try {
    summary.value = await getStudentCourseApi().learning(courseId)
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '学习记录加载失败。'
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <section class="kt-student-learning-page">
    <header class="kt-student-page-heading kt-student-page-heading--split">
      <div>
        <p class="kt-student-page-heading__eyebrow">{{ summary?.course_code || '课程' }}</p>
        <h1>班级中心</h1>
        <p>{{ summary?.course_name || '正在读取课程学习记录。' }}</p>
      </div>
      <p v-if="summary" class="kt-student-page-heading__context">{{ summary.course_term }} · {{ summary.class_group_name }}</p>
      <button v-if="summary" type="button" class="kt-button kt-button--secondary" :disabled="refreshing" @click="refresh">{{ refreshing ? '正在检查…' : '刷新结果' }}</button>
    </header>
    <p v-if="error" class="kt-alert kt-alert--danger" role="alert">{{ error }}</p>
    <p v-if="loading" role="status">正在加载学习记录…</p>
    <template v-else-if="summary">
      <p v-if="summary.comparison_status === 'pending' || summary.comparison_status === 'updating'" role="status">班级比较正在生成，个人作答与评分已显示。</p>
      <p v-else-if="summary.comparison_status === 'failed'" role="status">班级比较暂时无法生成，个人作答与评分可正常查看。</p>
      <section class="kt-learning-summary-grid" aria-label="个人学习概览">
        <article class="kt-card kt-learning-summary-card">
          <span>实验完成</span>
          <strong>{{ summary.completion.completed ?? 0 }} / {{ summary.completion.eligible ?? 0 }}</strong>
          <small v-if="summary.completion.rate?.percent_display">完成率 {{ summary.completion.rate.percent_display }}</small>
        </article>
        <article v-if="summary.correct_rate" class="kt-card kt-learning-summary-card">
          <span>正确率</span>
          <strong>{{ metricText(summary.correct_rate) }}</strong>
          <small v-if="classAverage(summary.comparisons?.correct_rate)">班级平均 {{ classAverage(summary.comparisons?.correct_rate) }}</small>
        </article>
        <article v-if="summary.score_rate" class="kt-card kt-learning-summary-card">
          <span>得分率</span>
          <strong>{{ metricText(summary.score_rate) }}</strong>
          <small v-if="classAverage(summary.comparisons?.score_rate)">班级平均 {{ classAverage(summary.comparisons?.score_rate) }}</small>
        </article>
        <article v-if="summary.score_grading_coverage" class="kt-card kt-learning-summary-card">
          <span>评分覆盖</span>
          <strong>已评分 {{ summary.score_grading_coverage.graded ?? 0 }} / {{ summary.score_grading_coverage.submitted ?? 0 }} 题</strong>
          <small v-if="summary.score_grading_coverage.is_partial">仍有题目等待评分</small>
        </article>
      </section>

      <section v-if="summary.kc_rows?.length" class="kt-card kt-learning-panel" aria-labelledby="learning-kc-title">
        <div class="kt-learning-panel__heading">
          <div><h2 id="learning-kc-title">知识点作答表现</h2><p>仅显示已授权的个人与班级比较。</p></div>
        </div>
        <ul class="kt-learning-kc-list">
          <li v-for="row in summary.kc_rows" :key="row.name">
            <span>{{ row.name }}</span>
            <strong>{{ metricText(row.student_value) }}</strong>
            <small v-if="row.class_average?.percent_display">班级平均 {{ row.class_average.percent_display }}</small>
          </li>
        </ul>
      </section>

      <section v-if="summary.trend_rows?.length" class="kt-card kt-learning-panel" aria-labelledby="learning-trend-title">
        <div class="kt-learning-panel__heading">
          <div><h2 id="learning-trend-title">学习趋势</h2><p>按已开放结果的作业顺序显示。</p></div>
        </div>
        <ol class="kt-learning-trend-list">
          <li v-for="row in summary.trend_rows" :key="row.experiment_id">
            <span>{{ row.name }}</span>
            <strong>{{ metricText(row.student_value) }}</strong>
            <small v-if="row.class_average?.percent_display">班级平均 {{ row.class_average.percent_display }}</small>
          </li>
        </ol>
      </section>

      <section class="kt-learning-panel" aria-labelledby="learning-experiments-title">
        <div class="kt-learning-panel__heading"><div><h2 id="learning-experiments-title">作业记录</h2><p>结果反馈按教师设置的权限显示。</p></div></div>
        <div v-if="summary.experiments.length" class="kt-learning-experiment-list">
          <article v-for="experiment in summary.experiments" :key="experiment.experiment_id" class="kt-card kt-learning-experiment-row">
            <div class="kt-learning-experiment-row__main">
              <span class="kt-status" :class="statusClass(experiment)">{{ experiment.status_label }}</span>
              <h3>{{ experiment.name }}</h3>
              <p v-if="experiment.correct_rate?.percent_display">正确率 {{ experiment.correct_rate.percent_display }}</p>
              <p v-else class="kt-muted">当前没有可显示的成绩指标</p>
            </div>
            <RouterLink v-if="experiment.results_available" class="kt-button kt-button--secondary" :to="`/student/courses/${courseId}/learning/experiments/${experiment.experiment_id}/`">查看作答详情</RouterLink>
          </article>
        </div>
        <div v-else class="kt-card kt-empty-state"><p>暂无实验学习记录。</p></div>
      </section>
    </template>
  </section>
</template>
