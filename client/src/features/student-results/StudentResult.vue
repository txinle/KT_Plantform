<script setup lang="ts">
import QuestionDirectoryPanel from '@/design-system/components/QuestionDirectoryPanel.vue'
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import Icon from '@/design-system/Icon.vue'

import type { StudentLearningExperiment } from '../student-courses/api'
import { getStudentCourseApi } from '../student-courses/api'
import { useComparisonPolling } from '../student-learning/useComparisonPolling'

const route = useRoute()
const courseId = Number(route.params.courseId)
const experimentId = Number(route.params.experimentId)
const result = ref<StudentLearningExperiment>()
const loading = ref(true)
const error = ref('')

const checks = useComparisonPolling(() => result.value,
  () => Boolean(result.value?.rank || result.value?.correct_rate_comparison?.class_average || result.value?.score_rate_comparison?.class_average),
  () => getStudentCourseApi().learningExperimentStatus(courseId, experimentId),
  async () => { result.value = await getStudentCourseApi().learningExperiment(courseId, experimentId) },
)
const refreshing = ref(false)
async function refresh() {
  refreshing.value = true
  try { await checks.refresh(); error.value = '' }
  catch (caught) { error.value = caught instanceof Error ? caught.message : '实验结果刷新失败。' }
  finally { refreshing.value = false }
}
const itemResults = computed(() => result.value?.item_results || [])

function metricText(metric: Record<string, unknown> | undefined) {
  const value = metric?.percent_display
  return typeof value === 'string' ? value : '—'
}

function numberField(source: Record<string, unknown> | undefined, key: string) {
  const value = source?.[key]
  return typeof value === 'number' ? value : undefined
}

const rankPosition = computed(() => numberField(result.value?.rank, 'position'))
const rankSampleSize = computed(() => numberField(result.value?.rank, 'sample_size'))

function formatGeneratedAt(value: string) {
  const parts = new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).formatToParts(new Date(value))
  const part = (type: Intl.DateTimeFormatPartTypes) => parts.find((item) => item.type === type)?.value || ''
  return `${part('year')}-${part('month')}-${part('day')} ${part('hour')}:${part('minute')}:${part('second')}`
}

onMounted(async () => {
  try {
    result.value = await getStudentCourseApi().learningExperiment(courseId, experimentId)
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '实验结果加载失败。'
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <section class="kt-feedback-page kt-experiment-feedback">
    <nav class="kt-feedback-breadcrumbs" aria-label="面包屑">
      <RouterLink :to="`/student/courses/${courseId}/`">作业</RouterLink>
      <Icon name="chevron-right" :size="16" />
      <span aria-current="page">作答详情</span>
    </nav>

    <header class="kt-feedback-page-header">
      <div>
        <h1>{{ result?.experiment_name || '作答详情' }}</h1>
        <p>只显示你的提交与教师已开放的结果反馈。</p>
      </div>
      <RouterLink class="kt-feedback-back" :to="`/student/courses/${courseId}/learning/`">返回班级中心</RouterLink>
      <button v-if="result" type="button" class="kt-button kt-button--secondary" :disabled="refreshing" @click="refresh">{{ refreshing ? '正在检查…' : '刷新结果' }}</button>
    </header>

    <p v-if="error" class="kt-alert kt-alert--danger kt-feedback-alert" role="alert">{{ error }}</p>
    <p v-if="loading" class="kt-feedback-loading" role="status">正在加载实验结果…</p>

    <template v-else-if="result">
      <p v-if="result.comparison_status === 'pending' || result.comparison_status === 'updating'" role="status">班级比较正在生成，个人作答与评分已显示。</p>
      <p v-else-if="result.comparison_status === 'failed'" role="status">班级比较暂时无法生成，个人作答与评分可正常查看。</p>
      <p class="kt-feedback-generated">统计生成时间：{{ formatGeneratedAt(result.generated_at) }}</p>

      <div class="kt-result-layout">
        <div class="kt-result-layout__main">
          <section class="kt-feedback-metrics" aria-label="实验指标">
            <article class="kt-feedback-metric">
              <span>状态</span>
              <strong>{{ result.status_label }}</strong>
              <small>本人本次作业</small>
            </article>
            <article class="kt-feedback-metric">
              <span>正确率</span>
              <strong>{{ metricText(result.correct_rate) }}</strong>
            </article>
            <article class="kt-feedback-metric">
              <span>得分率</span>
              <strong>{{ metricText(result.score_rate) }}</strong>
              <small v-if="numberField(result.grading_coverage, 'graded') !== undefined">
                已评分 {{ numberField(result.grading_coverage, 'graded') }} / {{ numberField(result.grading_coverage, 'submitted') }} 题
              </small>
            </article>
          </section>

          <aside v-if="rankPosition !== undefined && rankSampleSize !== undefined" class="kt-card kt-result-rank" aria-label="本次作业排名">
            <span>本次作业排名</span>
            <strong>第 {{ rankPosition }} / {{ rankSampleSize }} 名</strong>
          </aside>

          <section class="kt-feedback-results" aria-labelledby="item-results-title">
            <h2 id="item-results-title">逐题结果</h2>
            <div class="kt-feedback-table-card">
              <div v-if="itemResults.length" class="kt-feedback-table-scroll">
                <table class="kt-feedback-table">
                  <thead>
                    <tr><th>题目</th><th>知识点</th><th>结果</th><th>我的最终答案</th><th>得分</th><th>操作</th></tr>
                  </thead>
                  <tbody>
                    <tr v-for="item in itemResults" :id="`result-question-${item.question_id}`" :key="item.question_id" tabindex="0">
                      <td>{{ item.code }}</td>
                      <td>{{ item.kcs.join('、') }}</td>
                      <td><span class="kt-result-state" :class="`kt-result-state--${item.result_state}`">{{ item.result_label }}</span></td>
                      <td>{{ item.final_answer }}</td>
                      <td>{{ item.score ? `${item.score.value} / ${item.score.maximum}` : '—' }}</td>
                      <td>
                        <a class="kt-feedback-action" :href="`/student/courses/${courseId}/learning/experiments/${experimentId}/questions/${item.question_id}/`">
                          查看题目反馈 <Icon name="chevron-right" :size="18" />
                        </a>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <p v-else class="kt-feedback-empty">当前没有可显示的逐题结果。</p>
            </div>
          </section>
        </div>

        <QuestionDirectoryPanel v-if="itemResults.length">
          <ol>
            <li v-for="(item, index) in itemResults" :key="item.question_id">
              <a :href="`/student/courses/${courseId}/learning/experiments/${experimentId}/questions/${item.question_id}/`">
                <span>{{ index + 1 }}</span>{{ item.code }}
              </a>
            </li>
          </ol>
        </QuestionDirectoryPanel>
      </div>
    </template>
  </section>
</template>
