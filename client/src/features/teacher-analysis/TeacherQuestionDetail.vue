<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import Alert from '@/design-system/components/Alert.vue'
import EmptyState from '@/design-system/components/EmptyState.vue'
import PageHeader from '@/design-system/components/PageHeader.vue'

import { getTeacherAnalysisApi, type QuestionDetailResponse } from './api'

const route = useRoute()
const api = getTeacherAnalysisApi()
const courseId = Number(route.params.courseId)
const classId = Number(route.params.classId)
const experimentId = Number(route.params.experimentId)
const questionId = Number(route.params.questionId)
const page = ref<QuestionDetailResponse>()
const loading = ref(true)
const error = ref('')
const detail = computed(() => page.value?.detail)
const optionMaximum = computed(() => Math.max(detail.value?.answer_count || 0, 1))

function displayDate(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString('zh-CN', { hour12: false })
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    page.value = await api.questionDetail(courseId, classId, experimentId, questionId)
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '题目教学事实加载失败。'
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <PageHeader :title="detail ? `${detail.code} · 题目分析` : '题目分析'">
    <template #kicker>{{ detail ? `${detail.experiment_name} · ${detail.kcs.join('、')}` : '班级实验分析' }}</template>
    <p>{{ detail?.text || '正在加载题目事实。' }}</p>
    <template #actions><a v-if="page" class="kt-button kt-button--secondary" :href="page.actions.back_url">返回实验分析</a></template>
  </PageHeader>

  <Alert v-if="error" tone="danger">{{ error }}</Alert>
  <p v-if="loading" role="status">正在加载题目教学事实…</p>

  <template v-else-if="detail">
    <p class="kt-analysis-generated">统计生成时间：{{ displayDate(detail.generated_at) }}</p>
    <section class="kt-fact-grid" aria-label="题目概览">
      <article class="kt-card"><span>总体正确率</span><strong>{{ detail.correct_rate.percent_display }}</strong><small>{{ detail.correct_count }} 正确 / {{ detail.incorrect_count }} 错误</small></article>
      <article class="kt-card"><span>作答人数</span><strong>{{ detail.answer_count }} / {{ detail.eligible_count }}</strong><small>冻结应参与名单</small></article>
      <article class="kt-card"><span>有效时长中位数</span><strong>{{ detail.timing.median_display }}</strong><small>IQR {{ detail.timing.iqr_display }} · N={{ detail.timing.sample_size }}</small></article>
      <article class="kt-card"><span>过程链覆盖率</span><strong>{{ detail.chain_coverage.percent_display }}</strong><small>{{ detail.chain_coverage.numerator }} / {{ detail.chain_coverage.denominator }} 条可完整重放</small></article>
    </section>
    <Alert tone="info">参与名单：当前成员 {{ detail.current_member_count }} 人；历史参与者 {{ detail.historical_participant_count }} 人。历史参与者只在本次冻结实验范围内显示。</Alert>

    <section class="kt-card kt-analysis-section" aria-labelledby="timing-quality-title">
      <div class="kt-section-heading"><div><h2 id="timing-quality-title">时长质量构成</h2><p>只有精确有效时长和同步估算有效时长进入上方中位数与 IQR；墙钟回退不混入有效时长分布。</p></div></div>
      <dl class="kt-analysis-quality"><div><dt>精确有效</dt><dd>{{ detail.timing.quality.exact_count }}</dd></div><div><dt>同步估算</dt><dd>{{ detail.timing.quality.estimated_count }}</dd></div><div><dt>仅墙钟回退</dt><dd>{{ detail.timing.quality.wall_clock_count }}</dd></div><div><dt>不可计算</dt><dd>{{ detail.timing.quality.unavailable_count }}</dd></div></dl>
      <p>质量构成共 {{ detail.timing.quality.submitted_count }} 条正式提交。</p>
    </section>

    <section v-if="detail.option_rows.length" class="kt-card kt-analysis-section" aria-labelledby="option-distribution-title">
      <div class="kt-section-heading"><h2 id="option-distribution-title">选项分布</h2></div>
      <ul class="kt-analysis-option-list"><li v-for="row in detail.option_rows" :key="row.key"><span>{{ row.key }} · {{ row.label }}{{ row.is_correct ? '（正确答案）' : '' }}</span><progress :max="optionMaximum" :value="row.count">{{ row.count }} 人</progress><strong>{{ row.count }} 人</strong></li></ul>
    </section>

    <section class="kt-card kt-analysis-section" aria-labelledby="transition-title">
      <div class="kt-section-heading"><div><h2 id="transition-title">首答与终答</h2><p>只描述可验证的答案状态变化，不解释为犹豫或能力。</p></div></div>
      <dl v-if="detail.transition_applicability === 'applicable'" class="kt-analysis-quality"><div v-for="row in detail.transition_rows" :key="row.key"><dt>{{ row.label }}</dt><dd>{{ row.count }} 人</dd></div></dl>
      <Alert v-else tone="info">该题型不适用单一首答×终答四分类；上方仍保留选项分布与过程链覆盖率。</Alert>
    </section>

    <section class="kt-card kt-analysis-section" aria-labelledby="question-participants-title">
      <div class="kt-section-heading"><h2 id="question-participants-title">学生作答事实</h2></div>
      <EmptyState v-if="!detail.participants.length" title="暂无正式提交" />
      <div v-else class="kt-table-scroll"><table class="kt-table"><thead><tr><th>学号</th><th>姓名</th><th>成员关系</th><th>结果</th><th>最终答案</th><th>过程</th></tr></thead><tbody><tr v-for="row in detail.participants" :key="row.student_id"><td>{{ row.student_number }}</td><td><a :href="row.student_experiment_url">{{ row.display_name }}</a></td><td>{{ row.is_current_member ? '当前成员' : '历史参与者' }}</td><td>{{ row.result_label }}</td><td>{{ row.final_answer }}</td><td><a :href="row.process_url">{{ row.process_complete ? '查看完整过程' : '查看可用事实' }}</a></td></tr></tbody></table></div>
    </section>
  </template>
</template>

<style scoped>
.kt-analysis-generated { color: var(--kt-text-muted); font-size: .875rem; }
.kt-analysis-section { margin-top: 1.25rem; }
.kt-analysis-section p { color: var(--kt-text-muted); margin: .25rem 0 1rem; }
.kt-analysis-quality { display: grid; grid-template-columns: repeat(auto-fit, minmax(9rem, 1fr)); gap: .75rem; margin: 0; }
.kt-analysis-quality > div { border-left: 2px solid var(--kt-border, #d8dee9); padding-left: .75rem; }
.kt-analysis-quality dt { color: var(--kt-text-muted); }
.kt-analysis-quality dd { margin: .25rem 0 0; font-weight: 700; }
.kt-analysis-option-list { display: grid; gap: .75rem; list-style: none; margin: 0; padding: 0; }
.kt-analysis-option-list li { display: grid; align-items: center; gap: .75rem; grid-template-columns: minmax(10rem, 1fr) minmax(6rem, 2fr) auto; }
.kt-analysis-option-list progress { width: 100%; }
@media (max-width: 42rem) { .kt-analysis-option-list li { grid-template-columns: 1fr; gap: .25rem; } }
</style>
