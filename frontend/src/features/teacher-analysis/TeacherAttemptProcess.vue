<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import Alert from '@/design-system/components/Alert.vue'
import EmptyState from '@/design-system/components/EmptyState.vue'
import PageHeader from '@/design-system/components/PageHeader.vue'
import StatusBadge from '@/design-system/components/StatusBadge.vue'

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
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '作答过程事实加载失败。'
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <PageHeader :title="detail ? `${detail.display_name} · ${detail.question_code} 作答过程` : '作答过程'">
    <template #kicker>{{ detail ? `${detail.student_number} · ${detail.experiment_name}` : '班级题目分析' }}</template>
    <p>{{ detail?.question_text || '正在加载可验证的作答事实。' }}</p>
    <template #actions><a v-if="page" class="kt-button kt-button--secondary" :href="page.actions.back_url">返回题目分析</a></template>
  </PageHeader>

  <Alert v-if="error" tone="danger">{{ error }}</Alert>
  <p v-if="loading" role="status">正在加载作答过程事实…</p>

  <template v-else-if="detail">
    <p class="kt-analysis-generated">统计生成时间：{{ displayDate(detail.generated_at) }}</p>
    <StatusBadge :tone="detail.is_current_member ? 'success' : 'neutral'" :label="detail.is_current_member ? '当前班级成员' : '历史参与者'" />
    <section class="kt-fact-grid kt-analysis-facts" aria-label="作答过程概览">
      <article class="kt-card"><span>最终结果</span><strong>{{ detail.result_label }}</strong></article>
      <article class="kt-card"><span>得分</span><strong>{{ detail.score_display }}</strong></article>
      <article class="kt-card"><span>状态链校验</span><strong>{{ detail.process.completeness_label }}</strong></article>
      <article class="kt-card"><span>过程记录</span><strong>{{ detail.coverage_status_title }}</strong><small>{{ detail.coverage_status_description }}</small></article>
      <article class="kt-card"><span>时间质量</span><strong>{{ detail.coverage_time_title }}</strong><small>{{ detail.coverage_time_description }}</small></article>
    </section>

    <Alert v-if="detail.coverage_reason_labels.length" tone="info"><strong>记录质量说明：</strong>{{ detail.coverage_reason_labels.join('；') }}。缺失事件不会被解释为未发生行为。</Alert>
    <Alert v-if="detail.has_cross_instance_order_uncertainty" tone="warning"><strong>跨页面顺序依据同步估计，精确先后不可完全确定。</strong></Alert>
    <Alert v-if="detail.process.completeness === 'incomplete'" tone="warning"><strong>不完整链。</strong>平台只显示能够验证的最终事实，不补写缺失步骤，也不推断学生状态。</Alert>

    <section class="kt-card kt-analysis-section" aria-labelledby="process-timeline-title">
      <div class="kt-section-heading"><div><h2 id="process-timeline-title">作答过程</h2><p>合并导航、答案状态、提交与关闭事实；简答只显示编辑类型与字数变化。</p></div></div>
      <EmptyState v-if="!detail.process.steps.length" title="没有可验证的中间步骤"><p>最终提交与评分事实仍保留在上方。</p></EmptyState>
      <ol v-else class="kt-analysis-timeline"><li v-for="(step, index) in detail.process.steps" :key="`${step.label}-${index}`"><time>{{ displayTime(step.occurred_at) }}</time><div><strong>{{ step.label }}</strong><span v-if="step.value">{{ step.value }}</span><span v-else-if="step.length_after !== null">{{ step.length_before !== null ? `字数 ${step.length_before} → ${step.length_after}` : `字数 ${step.length_after}` }}</span><small>{{ step.time_source }}<template v-if="step.interval_ms !== null"> · 与前一事件间隔 {{ step.interval_ms }} ms</template></small></div></li></ol>
    </section>

    <section v-if="detail.process.final_value" class="kt-card kt-analysis-section" aria-labelledby="process-final-answer-title">
      <div class="kt-section-heading"><h2 id="process-final-answer-title">最终答案</h2></div>
      <div class="kt-analysis-final-answer">{{ detail.process.final_value }}</div>
    </section>
  </template>
</template>

<style scoped>
.kt-analysis-generated { color: var(--kt-text-muted); font-size: .875rem; }
.kt-analysis-facts { margin-top: 1rem; }
.kt-analysis-section { margin-top: 1.25rem; }
.kt-analysis-section p { color: var(--kt-text-muted); margin: .25rem 0 1rem; }
.kt-analysis-timeline { display: grid; gap: 1rem; list-style: none; margin: 0; padding: 0; }
.kt-analysis-timeline li { display: grid; gap: .75rem; grid-template-columns: minmax(10rem, auto) 1fr; border-left: 2px solid var(--kt-border, #d8dee9); padding-left: .75rem; }
.kt-analysis-timeline time, .kt-analysis-timeline small { color: var(--kt-text-muted); font-size: .875rem; }
.kt-analysis-timeline div { display: grid; gap: .25rem; }
.kt-analysis-final-answer { white-space: pre-wrap; overflow-wrap: anywhere; }
@media (max-width: 42rem) { .kt-analysis-timeline li { grid-template-columns: 1fr; gap: .25rem; } }
</style>
