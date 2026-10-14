<script setup lang="ts">
import { inject, onBeforeUnmount, ref, watch, type Ref } from 'vue'
import { useRoute } from 'vue-router'

import Alert from '@/design-system/components/Alert.vue'
import EmptyState from '@/design-system/components/EmptyState.vue'
import PageHeader from '@/design-system/components/PageHeader.vue'
import StatusBadge from '@/design-system/components/StatusBadge.vue'

import { getTeacherDataCenterApi, routeQuery, type DataCenterOverview } from './api'

const route = useRoute()
const courseId = Number(route.params.courseId)
const api = getTeacherDataCenterApi()
const scopeRefresh = inject<Ref<number>>('teacher-data-scope-refresh', ref(0))
const data = ref<DataCenterOverview>()
const error = ref('')
let latestRequest = 0
let controller: AbortController | undefined

function statusTone(status: string) {
  if (status === 'succeeded') return 'success' as const
  if (status === 'failed' || status === 'invalidated') return 'danger' as const
  return 'warning' as const
}

watch([() => route.query, scopeRefresh], async ([query]) => {
  const request = ++latestRequest
  controller?.abort()
  controller = new AbortController()
  error.value = ''
  try {
    const result = await api.overview(courseId, routeQuery(query), controller.signal)
    if (request === latestRequest) data.value = result
  } catch (caught) {
    if (request === latestRequest && !(caught instanceof Error && caught.name === 'AbortError')) {
      error.value = caught instanceof Error ? caught.message : '数据中心加载失败。'
    }
  }
}, { deep: true, immediate: true })

onBeforeUnmount(() => {
  latestRequest += 1
  controller?.abort()
})
</script>

<template>
  <PageHeader title="数据中心"><template #kicker>教学与研究数据</template><p>先确认数据完整、可用且可追溯，再进入分析或导出。</p><template v-if="data" #actions><a class="kt-button kt-button--secondary" :href="data.actions.governance_url">数据治理</a></template></PageHeader>
  <Alert v-if="error" tone="danger">{{ error }}</Alert>
  <p v-if="!data && !error" role="status">正在加载数据概览…</p>
  <template v-else-if="data">
    <Alert v-if="data.preparation && data.preparation.state !== 'ready'" :tone="data.preparation.state === 'failed' ? 'danger' : 'info'" data-testid="preparation-state">{{ data.preparation.message }}</Alert>
    <p v-if="data.preparation?.updated_at" data-testid="statistics-updated-at">统计更新：{{ new Date(data.preparation.updated_at).toLocaleString('zh-CN') }}</p>
    <section class="kt-data-facts" aria-label="当前范围概览"><article class="kt-card"><span>参与学生</span><strong>{{ data.facts.participants }}</strong><small>{{ data.facts.observed_participants }} 人已产生作答会话</small></article><article class="kt-card"><span>完成会话</span><strong>{{ data.facts.completed_sessions }} / {{ data.facts.total_sessions }}</strong><small>已完成 / 全部会话</small></article><article class="kt-card"><span>已提交作答</span><strong>{{ data.facts.submitted_attempts }}</strong><small>已完成正式提交</small></article><article class="kt-card"><span>行为过程记录</span><strong>{{ data.facts.process_records ?? '待整理' }}</strong><small>已记录的行为或状态变化</small></article><article class="kt-card"><span>需要核查</span><strong>{{ data.facts.quality_warnings ?? '待整理' }}</strong><small>{{ data.facts.not_evaluable ?? '待整理' }} 项暂无法评估</small></article></section>
    <section class="kt-section"><div class="kt-section-heading"><div><p class="kt-kicker">按任务进入</p><h2>研究数据工作</h2></div></div><div class="kt-task-link-grid"><a class="kt-card" :href="data.actions.preview_url"><strong>预览研究数据</strong><span>查看六张可关联的实验数据表</span></a><a class="kt-card" :href="data.actions.exports_url"><strong>导出实验数据</strong><span>生成包含 7 张 CSV 与 1 个 JSON 的 ZIP</span></a><a class="kt-card" :href="data.actions.quality_url"><strong>检查数据质量</strong><span>查看具体问题及其研究影响</span></a></div></section>
    <section class="kt-section"><div class="kt-section-heading"><h2>最近导出</h2><a :href="data.actions.history_url">查看全部</a></div><EmptyState v-if="!data.recent_exports.length" title="当前范围尚无导出记录" /><div v-else class="kt-card kt-table-card"><div class="kt-table-scroll"><table class="kt-table"><thead><tr><th>数据包</th><th>实验</th><th>班级</th><th>状态</th><th>创建时间</th></tr></thead><tbody><tr v-for="job in data.recent_exports" :key="job.job_id"><td>{{ job.package_label }}</td><td>{{ job.experiment_name || '未记录实验' }}</td><td>{{ job.class_name }}</td><td><StatusBadge :label="job.status_label" :tone="statusTone(job.status)" /></td><td>{{ new Date(job.created_at).toLocaleString('zh-CN') }}</td></tr></tbody></table></div></div></section>
  </template>
</template>
