<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import Alert from '@/design-system/components/Alert.vue'
import EmptyState from '@/design-system/components/EmptyState.vue'
import MobileCardList from '@/design-system/components/MobileCardList.vue'
import PageHeader from '@/design-system/components/PageHeader.vue'
import Paginator from '@/design-system/components/Paginator.vue'
import StatusBadge from '@/design-system/components/StatusBadge.vue'

import { getTeacherDataCenterApi, routeQuery, type DataCenterQualityCollection } from './api'

const route = useRoute()
const router = useRouter()
const courseId = Number(route.params.courseId)
const api = getTeacherDataCenterApi()
const data = ref<DataCenterQualityCollection>()
const error = ref('')
const currentPage = computed(() => Number(route.query.page || 1))

function tone(state: string) {
  if (state === 'PASS') return 'success' as const
  if (state === 'FAIL') return 'danger' as const
  if (state === 'WARN') return 'warning' as const
  return 'neutral' as const
}

async function load(page = currentPage.value) {
  try { data.value = await api.quality(courseId, { ...routeQuery(route.query), page }) }
  catch (caught) { error.value = caught instanceof Error ? caught.message : '质量报告加载失败。' }
}

async function navigate(page: number) {
  const query = { ...route.query }
  if (page === 1) delete query.page
  else query.page = String(page)
  await router.push({ query })
  await load(page)
}

onMounted(() => load())
</script>

<template>
  <PageHeader title="数据质量"><template #kicker>五态不变量</template><p>质量规则描述身份、状态链、提交、排序时间与血缘，不把技术记录推断为心理状态。</p></PageHeader>
  <Alert v-if="error" tone="danger">{{ error }}</Alert><p v-if="!data && !error" role="status">正在加载质量报告…</p>
  <template v-else-if="data">
    <section class="kt-quality-counts" aria-label="质量状态汇总"><article v-for="(count, state) in data.counts" :key="state" class="kt-card"><StatusBadge :label="state" :tone="tone(state)" /><strong>{{ count }}</strong></article></section>
    <EmptyState v-if="!data.items.length" title="当前范围没有质量规则结果" />
    <div v-else class="kt-card kt-table-card"><div class="kt-table-scroll kt-desktop-only"><table class="kt-table"><thead><tr><th>规则</th><th>结果</th><th>作答</th><th>来源</th><th>原因</th><th>证据</th></tr></thead><tbody><tr v-for="row in data.items" :key="`${row.rule}-${row.entity_id}`"><td><code>{{ row.rule }}</code><span class="kt-cell-note">{{ row.rule_version }}</span></td><td><StatusBadge :label="row.state" :tone="tone(row.state)" /></td><td>{{ row.entity_id ?? '—' }}</td><td>{{ row.source_mode }}</td><td><code>{{ row.reason_code || '—' }}</code></td><td>{{ row.evidence_summary || '—' }}</td></tr></tbody></table></div><MobileCardList class="kt-mobile-only"><li v-for="row in data.items" :key="`${row.rule}-${row.entity_id}`" class="kt-mobile-data-card"><div class="kt-section-heading"><code>{{ row.rule }}</code><StatusBadge :label="row.state" :tone="tone(row.state)" /></div><dl><div><dt>作答</dt><dd>{{ row.entity_id ?? '—' }}</dd></div><div><dt>来源</dt><dd>{{ row.source_mode }}</dd></div><div><dt>原因</dt><dd>{{ row.reason_code || '—' }}</dd></div></dl><p>{{ row.evidence_summary || '无补充证据' }}</p></li></MobileCardList><Paginator :page="data.page.number" :total-pages="data.page.total_pages" @navigate="navigate" /></div>
  </template>
</template>
