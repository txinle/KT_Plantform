<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import Alert from '@/design-system/components/Alert.vue'
import EmptyState from '@/design-system/components/EmptyState.vue'
import PageHeader from '@/design-system/components/PageHeader.vue'
import StatusBadge from '@/design-system/components/StatusBadge.vue'

import { getTeacherDataCenterApi, routeQuery, type DataCenterOverview } from './api'

const route = useRoute()
const courseId = Number(route.params.courseId)
const api = getTeacherDataCenterApi()
const data = ref<DataCenterOverview>()
const error = ref('')

function statusTone(status: string) {
  if (status === 'succeeded') return 'success' as const
  if (status === 'failed' || status === 'invalidated') return 'danger' as const
  return 'warning' as const
}

onMounted(async () => {
  try { data.value = await api.overview(courseId, routeQuery(route.query)) }
  catch (caught) { error.value = caught instanceof Error ? caught.message : '数据中心加载失败。' }
})
</script>

<template>
  <PageHeader title="数据中心"><template #kicker>教学与研究数据</template><p>先确认数据完整、可用且可追溯，再进入分析或导出。</p><template v-if="data" #actions><a class="kt-button kt-button--secondary" :href="data.actions.governance_url">数据治理</a></template></PageHeader>
  <Alert v-if="error" tone="danger">{{ error }}</Alert>
  <p v-if="!data && !error" role="status">正在加载数据概览…</p>
  <template v-else-if="data">
    <section class="kt-data-facts" aria-label="当前范围概览"><article class="kt-card"><span>参与学生</span><strong>{{ data.facts.participants }}</strong><small>{{ data.facts.observed_participants }} 人已产生会话</small></article><article class="kt-card"><span>完成会话</span><strong>{{ data.facts.completed_sessions }} / {{ data.facts.total_sessions }}</strong><small>以会话终态为准</small></article><article class="kt-card"><span>已提交作答</span><strong>{{ data.facts.submitted_attempts }}</strong><small>存在权威 Submission</small></article><article class="kt-card"><span>质量告警</span><strong>{{ data.facts.quality_warnings }}</strong><small>{{ data.facts.not_evaluable }} 项不可评估</small></article></section>
    <section class="kt-section"><div class="kt-section-heading"><div><p class="kt-kicker">按用途进入</p><h2>常用任务</h2></div></div><div class="kt-task-link-grid"><a class="kt-card" :href="data.actions.teaching_url"><strong>查看学生作答</strong><span>学生、会话与单题时间线</span></a><a class="kt-card" :href="data.actions.exports_url"><strong>导出数据包</strong><span>按教学或研究用途选择包</span></a><a class="kt-card" :href="data.actions.quality_url"><strong>检查数据质量</strong><span>查看五态不变量结果</span></a></div></section>
    <section class="kt-section"><div class="kt-section-heading"><h2>最近导出</h2><a :href="data.actions.history_url">查看全部</a></div><EmptyState v-if="!data.recent_exports.length" title="当前范围尚无导出记录" /><div v-else class="kt-card kt-table-card"><div class="kt-table-scroll"><table class="kt-table"><thead><tr><th>数据包</th><th>实验</th><th>状态</th><th>创建时间</th></tr></thead><tbody><tr v-for="job in data.recent_exports" :key="job.job_id"><td>{{ job.package }}</td><td>{{ job.experiment_name || '课程范围' }}</td><td><StatusBadge :label="job.status_label" :tone="statusTone(job.status)" /></td><td>{{ new Date(job.created_at).toLocaleString('zh-CN') }}</td></tr></tbody></table></div></div></section>
  </template>
</template>
