<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import type { StudentLearningSummary } from '../student-courses/api'
import { getStudentCourseApi } from '../student-courses/api'

const route = useRoute()
const courseId = Number(route.params.courseId)
const summary = ref<StudentLearningSummary>()
const loading = ref(true)
const error = ref('')

function statusClass(experiment: StudentLearningSummary['experiments'][number]) {
  if (experiment.completed || experiment.status_label === '已完成') return 'kt-status--success'
  if (experiment.participated) return 'kt-status--warning'
  return 'kt-status--neutral'
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
  <section>
    <div class="kt-page-header">
      <div class="kt-page-header__copy">
        <p class="kt-kicker">Learning record</p>
        <h1>我的学习</h1>
        <p>这里仅汇总本课程内你的正式实验记录；反馈可见范围以各实验政策为准。</p>
      </div>
    </div>
    <p v-if="error" class="kt-alert kt-alert--danger" role="alert">{{ error }}</p>
    <p v-if="loading" role="status">正在加载学习记录…</p>
    <template v-else-if="summary">
      <section class="kt-card kt-learning-overview" aria-labelledby="learning-overview-title">
        <div><p class="kt-kicker">Course progress</p><h2 id="learning-overview-title">实验完成</h2></div>
        <strong>{{ summary.completion.completed ?? 0 }} / {{ summary.completion.eligible ?? 0 }}</strong>
        <span v-if="summary.completion.rate?.percent_display">{{ summary.completion.rate.percent_display }}</span>
      </section>
      <section class="kt-section" aria-labelledby="learning-experiments-title">
        <div class="kt-section-heading"><h2 id="learning-experiments-title">实验记录</h2></div>
        <div v-if="summary.experiments.length" class="kt-learning-card-grid">
          <article v-for="experiment in summary.experiments" :key="experiment.experiment_id" class="kt-card kt-learning-card">
            <div>
              <span class="kt-status" :class="statusClass(experiment)">{{ experiment.status_label }}</span>
              <h3>{{ experiment.name }}</h3>
              <p v-if="experiment.correct_rate?.percent_display">正确率 {{ experiment.correct_rate.percent_display }}</p>
              <p v-else class="kt-muted">当前没有可显示的成绩指标</p>
            </div>
            <RouterLink v-if="experiment.results_available" class="kt-button kt-button--secondary" :to="`/student/courses/${courseId}/learning/experiments/${experiment.experiment_id}/`">查看实验记录</RouterLink>
          </article>
        </div>
        <div v-else class="kt-card kt-empty-state"><p>暂无实验学习记录。</p></div>
      </section>
    </template>
  </section>
</template>
