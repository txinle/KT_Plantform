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
let latestRequest = 0

function tone(state: string) {
  if (state === 'PASS' || state.includes('正常')) return 'success' as const
  if (state === 'FAIL' || state.includes('不一致')) return 'danger' as const
  if (state === 'WARN' || state.includes('注意') || state.includes('核查')) return 'warning' as const
  return 'neutral' as const
}

async function load(page = currentPage.value) {
  const request = ++latestRequest
  error.value = ''
  data.value = undefined
  try {
    const result = await api.quality(courseId, { ...routeQuery(route.query), page })
    if (request === latestRequest) data.value = result
  } catch (caught) {
    if (request === latestRequest) error.value = caught instanceof Error ? caught.message : '质量报告加载失败。'
  }
}

async function navigate(page: number) {
  const query = { ...route.query, page: page === 1 ? undefined : String(page) }
  await router.push({ query })
  await load(page)
}

onMounted(() => load())
</script>

<template>
  <PageHeader title="数据质量">
    <template #kicker>研究可用性</template>
    <p>按学生、作答会话和题目说明具体问题，并明确该问题对研究分析的影响。</p>
  </PageHeader>
  <Alert v-if="error" tone="danger">{{ error }}</Alert>
  <p v-if="!data && !error" role="status">正在加载数据质量报告…</p>
  <template v-else-if="data">
    <Alert
      v-if="data.preparation && data.preparation.state !== 'ready'"
      :tone="data.preparation.state === 'failed' ? 'danger' : data.preparation.state === 'updating' ? 'info' : 'warning'"
      data-testid="preparation-state"
    >
      <strong>研究数据状态：</strong>{{ data.preparation.message }}
    </Alert>
    <section class="kt-quality-counts" aria-label="质量状态汇总">
      <article v-for="(count, label) in data.counts" :key="label" class="kt-card"><StatusBadge :label="label" :tone="tone(label)" /><strong>{{ count }}</strong></article>
    </section>
    <EmptyState v-if="!data.scope.is_complete" title="请先选择实验和班级" />
    <EmptyState v-else-if="!data.items.length" title="当前范围没有需要展示的质量结果" />
    <div v-else class="kt-card kt-table-card">
      <div class="kt-table-scroll kt-desktop-only">
        <table class="kt-table">
          <thead><tr><th>学生</th><th>作答会话</th><th>题目</th><th>结果</th><th>发现的问题</th><th>对研究的影响</th></tr></thead>
          <tbody><tr v-for="row in data.items" :key="`${row.student_number}-${row.session_ref}-${row.question_code}-${row.problem}`"><td><strong>{{ row.student_name || '姓名未记录' }}</strong><span class="kt-cell-note">{{ row.student_number || '学号未记录' }}</span></td><td>{{ row.session_ref || '—' }}</td><td>{{ row.question_code || '—' }}</td><td><StatusBadge :label="row.state_label" :tone="tone(row.state)" /></td><td>{{ row.problem }}</td><td>{{ row.impact }}</td></tr></tbody>
        </table>
      </div>
      <MobileCardList class="kt-mobile-only">
        <li v-for="row in data.items" :key="`${row.student_number}-${row.session_ref}-${row.question_code}-${row.problem}`" class="kt-mobile-data-card">
          <div class="kt-section-heading"><strong>{{ row.student_name || '姓名未记录' }}</strong><StatusBadge :label="row.state_label" :tone="tone(row.state)" /></div>
          <dl><div><dt>学号</dt><dd>{{ row.student_number || '—' }}</dd></div><div><dt>作答会话</dt><dd>{{ row.session_ref || '—' }}</dd></div><div><dt>题目</dt><dd>{{ row.question_code || '—' }}</dd></div></dl>
          <p><strong>发现的问题：</strong>{{ row.problem }}</p><p><strong>对研究的影响：</strong>{{ row.impact }}</p>
        </li>
      </MobileCardList>
      <Paginator :page="data.page.number" :total-pages="data.page.total_pages" @navigate="navigate" />
    </div>
  </template>
</template>
