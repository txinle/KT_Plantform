<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import Alert from '@/design-system/components/Alert.vue'
import EmptyState from '@/design-system/components/EmptyState.vue'

import { getTeacherAnalysisApi, type AttemptProcessResponse } from './api'

const route = useRoute()
const api = getTeacherAnalysisApi()
const courseId = Number(route.params.courseId)
const classId = Number(route.params.classId)
const experimentId = Number(route.params.experimentId)
const questionId = Number(route.params.questionId)
const studentId = Number(route.params.studentId)
const page = ref<AttemptProcessResponse>()
const loading = ref(true)
const error = ref('')
const detail = computed(() => page.value?.detail)

function displayDate(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString('zh-CN', { hour12: false })
}

function displayTime(value: string | null) {
  return value ? displayDate(value) : '未记录时间'
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    page.value = await api.attemptProcess(courseId, classId, experimentId, questionId, studentId)
  } catch {
    error.value = '作答过程暂时无法加载，请刷新页面后重试。'
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <Alert v-if="error" tone="danger">{{ error }}</Alert>
  <p v-if="loading" role="status">正在加载作答过程事实…</p>

  <template v-else-if="detail">
    <div class="kt-process-notice"><span aria-hidden="true">ⓘ</span><div><strong>{{ detail.display_name }} · {{ detail.question_code }}</strong><p>{{ detail.question_text }}</p><details><summary>查看完整题目与参考答案</summary><p v-for="option in detail.question_options" :key="option">{{ option }}</p><p>参考答案：{{ detail.correct_answer || '未设置' }}</p></details></div></div>
    <div class="kt-process-summary" aria-label="作答过程概览"><span>最终结果：<strong>{{ detail.result_label }}</strong></span><span>得分：<strong>{{ detail.score_display }}</strong></span><span>信心：<strong>{{ detail.confidence_label }}</strong></span><span>{{ detail.process.completeness_label }}</span><span>{{ detail.coverage_status_title }} · {{ detail.coverage_time_title }}</span></div>

    <Alert v-if="detail.coverage_reason_labels.length" class="kt-process-quality" tone="info"><strong>记录质量说明：</strong>{{ detail.coverage_reason_labels.join('；') }}。缺失事件不会被解释为未发生行为。</Alert>
    <Alert v-if="detail.has_cross_instance_order_uncertainty" class="kt-process-quality" tone="warning"><strong>跨页面顺序依据同步估计，精确先后不可完全确定。</strong></Alert>
    <Alert v-if="detail.process.completeness === 'incomplete'" class="kt-process-quality" tone="warning"><strong>{{ detail.process.completeness_label }}。</strong>平台只显示能够验证的最终事实，不补写缺失步骤，也不推断学生状态。</Alert>

    <section class="kt-card kt-analysis-section" aria-labelledby="process-timeline-title">
      <div class="kt-section-heading"><div><h1 id="process-timeline-title"><span class="kt-process-icon" aria-hidden="true">☷</span>作答过程</h1><p>共 {{ detail.process.steps.length }} 个可验证步骤，按本次正式答案的变化顺序展示。</p></div><div class="kt-process-heading-meta"><span>总用时：{{ detail.answer_time_display }}</span><a v-if="page" class="kt-button kt-button--secondary" :href="page.actions.back_url">返回题目分析</a></div></div>
      <EmptyState v-if="!detail.process.steps.length" title="没有可验证的中间步骤"><p>最终提交与评分事实仍保留在上方。</p></EmptyState>
      <ol v-else class="kt-process-timeline">
        <li v-for="(step, index) in detail.process.steps" :key="`${step.label}-${index}`">
          <span class="kt-process-node" aria-hidden="true">{{ index + 1 }}</span>
          <div class="kt-process-step">
            <div class="kt-process-copy">
              <strong>{{ step.label }}</strong>
              <span v-if="detail.process.question_type === 'short_answer' && step.length_after !== null">字数 {{ step.length_before }} → {{ step.length_after }}</span>
              <span v-else-if="step.value">{{ step.value_before !== null && !['short_answer', 'fill_blank', 'numeric'].includes(detail.process.question_type) ? `${step.value_before} → ${step.value}` : step.value.length > 100 ? `${step.value.slice(0, 100)}…` : step.value }}</span>
              <details v-if="step.value_before !== null && step.value_after !== null && ['short_answer', 'fill_blank', 'numeric'].includes(detail.process.question_type)" class="kt-process-values">
                <summary>查看完整修改</summary>
                <p><b>修改前：</b>{{ step.value_before }}</p>
                <p><b>修改后：</b>{{ step.value_after }}</p>
              </details>
            </div>
            <div class="kt-process-meta">
              <time class="kt-process-time">{{ displayTime(step.occurred_at) }}</time>
              <span v-if="step.interval_ms !== null" class="kt-process-duration">+ {{ step.interval_ms }} ms</span>
              <small v-if="!step.occurred_at">{{ step.time_source }}</small>
            </div>
          </div>
        </li>
      </ol>
    </section>

    <section v-if="detail.process.final_value" class="kt-card kt-analysis-section" aria-labelledby="process-final-answer-title">
      <div class="kt-section-heading"><h2 id="process-final-answer-title"><span class="kt-process-icon" aria-hidden="true">▥</span>最终答案</h2></div>
      <div class="kt-analysis-final-answer">{{ detail.process.final_value }}</div>
    </section>
  </template>
</template>

<style scoped>
.kt-process-notice { align-items: start; background: #eaf0ff; border-radius: .6rem; color: #174fa9; display: flex; gap: .65rem; padding: .85rem 1rem; }
.kt-process-notice p { margin: 0; overflow-wrap: anywhere; }
.kt-process-summary { background: #f2f4f8; border-radius: .6rem; color: #4d607d; display: flex; flex-wrap: wrap; gap: .6rem 1.5rem; margin: .45rem 0; padding: .85rem 1rem; }
.kt-process-icon { background: #ebe7ff; border-radius: .35rem; color: #684aff; display: inline-flex; justify-content: center; margin-right: .65rem; width: 1.6rem; }
.kt-process-quality { background: #fff8e8; border-color: #ffe0a0; color: #976100; }
.kt-analysis-section { border-radius: 1rem; margin-top: 1.15rem; padding: 1.2rem 1.5rem; box-shadow: 0 1px 4px #17294b08; }
.kt-analysis-section h1 { font-size: 1.2rem; margin: 0; }
.kt-analysis-section .kt-section-heading { align-items: start; margin-bottom: .35rem; }
.kt-analysis-section .kt-section-heading p { font-size: .85rem; margin: .35rem 0 .2rem 2.25rem; }
.kt-analysis-section p { color: var(--kt-text-muted); margin: .25rem 0 1rem; }
.kt-process-timeline { list-style: none; margin: .7rem 0 0; padding: 0; }
.kt-process-timeline li { align-items: center; display: grid; grid-template-columns: 2.25rem minmax(0, 1fr); gap: .85rem; position: relative; padding-bottom: .55rem; }
.kt-process-timeline li::before { background: #cbd5e1; content: ''; left: 1.08rem; position: absolute; top: 0; bottom: 0; width: 2px; }
.kt-process-timeline li:first-child::before { top: 50%; }
.kt-process-timeline li:last-child::before { bottom: 50%; }
.kt-process-heading-meta { align-items: center; color: var(--kt-text-muted); display: flex; flex-wrap: wrap; gap: 1rem; font-size: .85rem; }
.kt-process-node { align-items: center; background: #60a7fc; border-radius: 50%; color: #fff; display: flex; font-size: .9rem; font-weight: 700; height: 2.1rem; justify-content: center; position: relative; width: 2.1rem; z-index: 1; }
.kt-process-timeline li:first-child .kt-process-node { background: #0964ef; }
.kt-process-timeline li:last-child .kt-process-node { background: #794aff; }
.kt-process-step { align-items: center; background: #f6f7f9; border-radius: .6rem; display: grid; gap: 1rem; grid-template-columns: minmax(0, 1fr) minmax(16rem, auto); min-height: 3.4rem; padding: .45rem 1rem; }
.kt-process-copy { display: grid; gap: .28rem; min-width: 0; }
.kt-process-copy > span { overflow-wrap: anywhere; white-space: pre-wrap; }
.kt-process-values summary { color: #2563eb; cursor: pointer; font-size: .82rem; }
.kt-process-values p { color: var(--kt-color-text); margin: .45rem 0 0; overflow-wrap: anywhere; white-space: pre-wrap; }
.kt-process-meta { align-items: center; display: flex; gap: 1rem; justify-content: end; text-align: right; }
.kt-process-time, .kt-process-meta small { color: var(--kt-text-muted); font-size: .82rem; }
.kt-process-duration { background: #eaf3ff; border-radius: 999px; color: #0062ef; font-size: .78rem; min-width: 3.5rem; padding: .3rem .65rem; text-align: center; white-space: nowrap; }
.kt-analysis-final-answer { background: #eaf4ff; border: 1px solid #cfe3fb; border-radius: .65rem; color: #153a62; padding: 1rem 1.1rem; white-space: pre-wrap; overflow-wrap: anywhere; }
@media (max-width: 42rem) {
  .kt-analysis-section { padding: 1rem; }
  .kt-analysis-section .kt-section-heading { flex-wrap: wrap; gap: .65rem; }
  .kt-process-timeline li { gap: .65rem; grid-template-columns: 2rem minmax(0, 1fr); }
  .kt-process-node { height: 2rem; width: 2rem; }
  .kt-process-timeline li::before { left: .95rem; }
  .kt-process-step { grid-template-columns: 1fr; }
  .kt-process-meta { flex-wrap: wrap; justify-content: start; text-align: left; }
}
</style>
