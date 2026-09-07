<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import Alert from '@/design-system/components/Alert.vue'
import EmptyState from '@/design-system/components/EmptyState.vue'
import PageHeader from '@/design-system/components/PageHeader.vue'

import { getTeacherAnalysisApi, type StudentDetailResponse } from './api'

const route = useRoute()
const api = getTeacherAnalysisApi()
const courseId = Number(route.params.courseId)
const classId = Number(route.params.classId)
const studentId = Number(route.params.studentId)
const page = ref<StudentDetailResponse>()
const loading = ref(true)
const error = ref('')
const detail = computed(() => page.value?.detail)
const studentTrendPoints = computed(() => (
  detail.value?.trend_rows.map((row) => `${row.x},${row.student_y}`).join(' ') || ''
))
const classTrendPoints = computed(() => (
  detail.value?.trend_rows.map((row) => `${row.x},${row.class_y}`).join(' ') || ''
))

function displayDate(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString('zh-CN', { hour12: false })
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    page.value = await api.studentDetail(courseId, classId, studentId)
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '学生学习事实加载失败。'
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <PageHeader :title="detail ? `${detail.display_name} · 学习数据` : '学生学习数据'">
    <template #kicker>{{ detail ? `${detail.student_number} · ${page?.class_group.name}` : '班级学习事实' }}</template>
    <p>仅呈现该学生在当前班级正式实验中的学习事实，不生成能力、掌握或心理状态判断。</p>
    <template #actions><a v-if="page" class="kt-button kt-button--secondary" :href="page.actions.back_url">返回学生表现</a></template>
  </PageHeader>

  <Alert v-if="error" tone="danger">{{ error }}</Alert>
  <p v-if="loading" role="status">正在加载学生学习事实…</p>

  <template v-else-if="detail && page">
    <p class="kt-analysis-generated">统计生成时间：{{ displayDate(detail.generated_at) }}</p>

    <section class="kt-fact-grid" aria-label="学生概览">
      <article class="kt-card"><span>实验完成</span><strong>{{ detail.completed_experiments }} / {{ detail.eligible_experiments }}</strong><small>当前班级正式实验</small></article>
      <article class="kt-card"><span>个人正确率</span><strong>{{ detail.correct_rate.percent_display }}</strong><small>{{ detail.correct_rate.numerator }} / {{ detail.correct_rate.denominator }} 条有效已评分作答</small></article>
      <article class="kt-card"><span>个人得分率</span><strong>{{ detail.score_rate.percent_display }}</strong><small>有效得分 / 对应满分</small></article>
      <article class="kt-card"><span>平均作答时间</span><strong>{{ detail.average_answer_time_display }}</strong><small>已正式提交作答</small></article>
    </section>

    <section class="kt-card kt-analysis-section" aria-labelledby="student-trend-title">
      <div class="kt-section-heading"><div><h2 id="student-trend-title">实验正确率趋势</h2><p>个人正确率与同实验当前班级学生等权平均；两条线使用各自可比较集合。</p></div></div>
      <div v-if="detail.show_trend_chart" class="kt-analysis-trend">
        <svg viewBox="0 0 100 108" role="img" aria-labelledby="student-trend-svg-title student-trend-svg-desc">
          <title id="student-trend-svg-title">{{ detail.display_name }}与班级平均正确率趋势</title>
          <desc id="student-trend-svg-desc">下方列表提供每次实验的精确数值。</desc>
          <line x1="5" y1="95" x2="95" y2="95" class="trend-axis" />
          <polyline :points="studentTrendPoints" class="trend-line trend-line-student" />
          <polyline :points="classTrendPoints" class="trend-line trend-line-class" />
          <template v-for="row in detail.trend_rows" :key="row.experiment_id">
            <circle :cx="row.x" :cy="row.student_y" r="1.6" class="trend-dot trend-dot-student" />
            <circle :cx="row.x" :cy="row.class_y" r="1.6" class="trend-dot trend-dot-class" />
          </template>
        </svg>
        <div class="kt-analysis-trend__legend"><span><i class="trend-key trend-key-student" />学生本人</span><span><i class="trend-key trend-key-class" />班级平均</span></div>
      </div>
      <div class="kt-table-scroll"><table class="kt-table"><thead><tr><th>实验</th><th>学生本人</th><th>班级平均</th><th>学生样本</th></tr></thead><tbody><tr v-for="row in detail.trend_rows" :key="row.experiment_id"><td>{{ row.name }}</td><td>{{ row.student_correct_rate.percent_display }}</td><td>{{ row.class_average_correct_rate.percent_display }}</td><td>{{ row.class_average_correct_rate.sample_size ?? 0 }} 人</td></tr></tbody></table></div>
      <EmptyState v-if="!detail.trend_rows.length" title="尚无可比较的实验正确率" />
    </section>

    <section class="kt-card kt-analysis-section" aria-labelledby="student-kc-title">
      <div class="kt-section-heading"><div><h2 id="student-kc-title">知识点正确率</h2><p>学生本人和当前班级学生等权平均分别显示；这不是模型估计掌握度。</p></div></div>
      <EmptyState v-if="!detail.kc_rows.length" title="暂无知识点统计" />
      <dl v-else class="kt-analysis-comparisons"><div v-for="row in detail.kc_rows" :key="row.name"><dt>{{ row.name }}</dt><dd>学生本人 {{ row.comparison.student_value.percent_display }}（{{ row.comparison.student_value.numerator }} / {{ row.comparison.student_value.denominator }}）</dd><dd>班级平均 {{ row.comparison.class_average.percent_display }}（{{ row.comparison.sample_size }} 人）</dd></div></dl>
    </section>

    <section class="kt-card kt-analysis-section" aria-labelledby="student-experiment-title">
      <div class="kt-section-heading"><h2 id="student-experiment-title">实验表现</h2></div>
      <EmptyState v-if="!detail.experiments.length" title="暂无正式实验" />
      <div v-else class="kt-table-scroll"><table class="kt-table"><thead><tr><th>实验</th><th>完成</th><th>正确率</th><th>得分率</th><th>排名</th><th>平均用时</th><th>操作</th></tr></thead><tbody><tr v-for="row in detail.experiments" :key="row.experiment_id"><td>{{ row.name }}</td><td>{{ row.completed ? '已完成' : '未完成' }}</td><td>{{ row.correct_rate.percent_display }}</td><td>{{ row.score_rate.percent_display }}</td><td>{{ row.rank ? `${row.rank} / ${row.rank_sample_size}` : '—' }}</td><td>{{ row.average_answer_time_display }}</td><td><a :href="row.detail_url">查看逐题事实</a></td></tr></tbody></table></div>
    </section>
  </template>
</template>

<style scoped>
.kt-analysis-generated { color: var(--kt-text-muted); font-size: .875rem; }
.kt-analysis-section { margin-top: 1.25rem; }
.kt-analysis-section p { color: var(--kt-text-muted); margin: .25rem 0 1rem; }
.kt-analysis-trend { max-width: 40rem; margin: 1rem 0; }
.kt-analysis-trend svg { display: block; width: 100%; height: auto; }
.trend-axis { stroke: var(--kt-border, #cbd5e1); stroke-width: .8; }
.trend-line { fill: none; stroke-width: 1.4; }
.trend-line-student, .trend-dot-student { stroke: #2563eb; fill: #2563eb; }
.trend-line-class, .trend-dot-class { stroke: #a16207; fill: #a16207; }
.kt-analysis-trend__legend { display: flex; gap: 1rem; color: var(--kt-text-muted); font-size: .875rem; }
.trend-key { display: inline-block; width: .75rem; height: .75rem; margin-right: .35rem; border-radius: 50%; }
.trend-key-student { background: #2563eb; }
.trend-key-class { background: #a16207; }
.kt-analysis-comparisons { display: grid; gap: .75rem; margin: 0; }
.kt-analysis-comparisons > div { border-top: 1px solid var(--kt-border, #d8dee9); padding-top: .75rem; }
.kt-analysis-comparisons dt { font-weight: 700; }
.kt-analysis-comparisons dd { margin: .25rem 0 0; color: var(--kt-text-muted); }
</style>
