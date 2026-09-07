<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import Alert from '@/design-system/components/Alert.vue'
import EmptyState from '@/design-system/components/EmptyState.vue'
import PageHeader from '@/design-system/components/PageHeader.vue'
import StatusBadge from '@/design-system/components/StatusBadge.vue'

import { getTeacherAnalysisApi, type StudentExperimentDetailResponse } from './api'

const route = useRoute()
const api = getTeacherAnalysisApi()
const courseId = Number(route.params.courseId)
const classId = Number(route.params.classId)
const studentId = Number(route.params.studentId)
const experimentId = Number(route.params.experimentId)
const page = ref<StudentExperimentDetailResponse>()
const loading = ref(true)
const error = ref('')
const detail = computed(() => page.value?.detail)

function displayDate(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString('zh-CN', { hour12: false })
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    page.value = await api.studentExperimentDetail(courseId, classId, studentId, experimentId)
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '学生实验事实加载失败。'
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <PageHeader :title="detail ? `${detail.display_name} · ${detail.experiment_name}` : '学生实验事实'">
    <template #kicker>{{ detail ? `${detail.student_number} · ${page?.class_group.name}` : '班级实验分析' }}</template>
    <p>该页只显示这一名学生在本次冻结实验任务中的事实。</p>
    <template #actions><a v-if="page" class="kt-button kt-button--secondary" :href="page.actions.back_url">返回实验分析</a></template>
  </PageHeader>

  <Alert v-if="error" tone="danger">{{ error }}</Alert>
  <p v-if="loading" role="status">正在加载学生实验事实…</p>

  <template v-else-if="detail">
    <p class="kt-analysis-generated">统计生成时间：{{ displayDate(detail.generated_at) }}</p>
    <StatusBadge :tone="detail.is_current_member ? 'success' : 'neutral'" :label="detail.is_current_member ? '当前班级成员' : '历史参与者'" />

    <section class="kt-fact-grid kt-analysis-facts" aria-label="学生实验概览">
      <article class="kt-card"><span>完成状态</span><strong>{{ detail.completed ? '已完成' : '未完成' }}</strong><small>本次正式实验</small></article>
      <article class="kt-card"><span>个人正确率</span><strong>{{ detail.correct_rate.percent_display }}</strong><small>{{ detail.correct_rate.numerator }} / {{ detail.correct_rate.denominator }} 条有效已评分作答</small></article>
      <article class="kt-card"><span>个人得分率</span><strong>{{ detail.score_rate.percent_display }}</strong><small>有效得分 / 对应满分</small></article>
      <article class="kt-card"><span>本次任务排名</span><strong>{{ detail.rank ? `${detail.rank} / ${detail.rank_sample_size}` : '—' }}</strong><small>仅完整评分的冻结参与者</small></article>
    </section>

    <section class="kt-card kt-analysis-section" aria-labelledby="student-question-facts-title">
      <div class="kt-section-heading"><div><h2 id="student-question-facts-title">逐题事实</h2><p>选择题才显示可比的选择修改次数；数值、填空与简答不生成跨题型修改次数。</p></div></div>
      <EmptyState v-if="!detail.questions.length" title="没有正式提交的题目" />
      <div v-else class="kt-table-scroll"><table class="kt-table"><thead><tr><th>题目</th><th>知识点</th><th>结果</th><th>首答</th><th>终答</th><th>修改次数</th><th>用时</th><th>操作</th></tr></thead><tbody><tr v-for="row in detail.questions" :key="row.question_id"><td><a :href="row.question_url">{{ row.code }}</a></td><td>{{ row.kcs.join('、') }}</td><td>{{ row.result_label }}</td><td>{{ row.first_answer || '—' }}</td><td>{{ row.final_answer }}</td><td>{{ row.revision_count ?? '—' }}</td><td>{{ row.answer_time_display }}</td><td><a :href="row.process_url">查看作答过程</a></td></tr></tbody></table></div>
    </section>
  </template>
</template>

<style scoped>
.kt-analysis-generated { color: var(--kt-text-muted); font-size: .875rem; }
.kt-analysis-facts { margin-top: 1rem; }
.kt-analysis-section { margin-top: 1.25rem; }
.kt-analysis-section p { color: var(--kt-text-muted); margin: .25rem 0 1rem; }
</style>
