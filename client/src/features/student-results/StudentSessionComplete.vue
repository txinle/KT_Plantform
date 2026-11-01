<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { createApiClient } from '@/api/client'
import { getBootstrapContext } from '@/app/bootstrap'
import WorkspaceShell from '@/design-system/components/WorkspaceShell.vue'
import QuestionGroupResults from '@/question-content/QuestionGroupResults.vue'
import type { ResultGroup, ScoreProgress } from '@/question-content/question-group-results'
import './student-results.css'

interface ResultItem { question_id: number; question_code: string; submitted_answer: string; is_correct?: boolean | null; score?: string | number; max_score?: string | number; correct_answer?: string; explanation?: string }
interface ComparisonMetric { status: string; percent?: string | number; position?: number; sample_size?: number }

interface Summary {
  result?: { feedback_phase: string; groups?: ResultGroup[]; score_progress?: ScoreProgress; items?: ResultItem[];
    score?: { earned: string | number; maximum: string | number; percent: string | number };
    correctness?: { correct: number; graded: number; percent: string | number };
    comparison?: { class_average?: ComparisonMetric; rank?: ComparisonMetric }; comparison_status?: string }
  session_id: number
  experiment_name: string
  status: string
  status_label: string
  submission_count: number
  attempt_count: number
  presentation_question_count?: number
  return_url: string
}
const route = useRoute()
const bootstrap = getBootstrapContext()
const summary = ref<Summary>()
const error = ref('')
const items = computed(() => summary.value?.result?.items || [])
const hasScore = computed(() => items.value.some(item => 'score' in item))
const hasCorrectness = computed(() => items.value.some(item => 'is_correct' in item))
const hasAnswers = computed(() => items.value.some(item => 'correct_answer' in item))
onMounted(async () => {
  try { summary.value = await createApiClient().get<Summary>(`/api/v1/student/sessions/${Number(route.params.sessionId)}/summary/`) }
  catch (caught) { error.value = caught instanceof Error ? caught.message : '无法加载会话状态。' }
})
</script>

<template>
  <WorkspaceShell
    :user-name="bootstrap.user.display_name"
    :csrf-token="bootstrap.csrf_token"
    password-change-url="/password/change/"
    :initial-password-reminder-pending="bootstrap.user.initial_password_reminder_pending"
    main-id="session-summary"
  >
    <template #navigation><a href="/student/">我的课程</a></template>
    <p v-if="error" role="alert" class="kt-alert kt-alert--danger">{{ error }}</p>
    <section v-else-if="summary" class="kt-session-summary" aria-labelledby="session-title">
      <header class="kt-session-summary__header"><div><h1 id="session-title">{{ summary.experiment_name }}</h1><p>当前状态：<strong>{{ summary.status_label }}</strong></p></div><a class="kt-button kt-button--secondary" :href="summary.return_url">返回作业列表</a></header>
      <dl class="kt-session-summary__metrics"><div v-if="summary.presentation_question_count !== undefined"><dt>完整题目</dt><dd>{{ summary.presentation_question_count }}</dd></div><div><dt>已正式提交作答项</dt><dd>{{ summary.submission_count }} / {{ summary.attempt_count }}</dd></div><div v-if="summary.result?.score"><dt>最终得分</dt><dd>{{ summary.result.score.earned }} / {{ summary.result.score.maximum }}</dd></div><div v-if="summary.result?.correctness"><dt>正确率</dt><dd>{{ summary.result.correctness.percent }}%</dd></div></dl>
      <QuestionGroupResults v-if="summary.result" :groups="summary.result.groups" :score-progress="summary.result.score_progress" />
      <p v-if="summary.result?.feedback_phase === 'not_released'">反馈将在服务器确认的开放时间显示。</p>
      <p v-else-if="summary.result?.feedback_phase === 'disabled'">本次作答已结束，教师设置为仅显示提交状态。</p>
      <section v-if="items.length" class="kt-session-summary__items"><h2>逐项作答与反馈</h2><div class="kt-session-summary__table-scroll"><table><thead><tr><th>题目 / 作答项</th><th>你的答案</th><th v-if="hasCorrectness">反馈</th><th v-if="hasScore">得分</th><th v-if="hasAnswers">参考答案与解析</th></tr></thead><tbody><tr v-for="item in items" :key="item.question_id"><td>{{ item.question_code }}</td><td>{{ item.submitted_answer }}</td><td v-if="hasCorrectness">{{ item.is_correct === true ? '正确' : item.is_correct === false ? '错误' : '未提供判定' }}</td><td v-if="hasScore">{{ item.score !== undefined ? `${item.score} / ${item.max_score}` : '—' }}</td><td v-if="hasAnswers"><template v-if="'correct_answer' in item">{{ item.correct_answer }}<p v-if="item.explanation">{{ item.explanation }}</p></template></td></tr></tbody></table></div></section>
      <section v-if="summary.result?.comparison" class="kt-session-summary__comparison"><h2>班级比较</h2><p v-if="summary.result.comparison.class_average?.status === 'available'">班级平均得分率：{{ summary.result.comparison.class_average.percent }}%（{{ summary.result.comparison.class_average.sample_size }} 人）</p><p v-if="summary.result.comparison.rank?.status === 'available'">本次排名：第 {{ summary.result.comparison.rank.position }} / {{ summary.result.comparison.rank.sample_size }} 名</p></section>
      <p v-if="summary.result?.comparison_status === 'pending' || summary.result?.comparison_status === 'updating'" role="status">班级比较正在生成，个人作答与评分已显示。</p>
    </section>
    <p v-else role="status">正在加载会话状态…</p>
  </WorkspaceShell>
</template>
