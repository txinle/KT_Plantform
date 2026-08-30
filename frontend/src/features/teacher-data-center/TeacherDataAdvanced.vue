<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import Alert from '@/design-system/components/Alert.vue'
import EmptyState from '@/design-system/components/EmptyState.vue'
import MobileCardList from '@/design-system/components/MobileCardList.vue'
import PageHeader from '@/design-system/components/PageHeader.vue'
import Paginator from '@/design-system/components/Paginator.vue'

import { getTeacherDataCenterApi, routeQuery, type DataCenterAdvanced } from './api'

const route = useRoute()
const router = useRouter()
const courseId = Number(route.params.courseId)
const api = getTeacherDataCenterApi()
const data = ref<DataCenterAdvanced>()
const error = ref('')
const search = ref('')
const groups = computed(() => (data.value?.groups || []).map(group => ({
  ...group,
  tables: group.tables.filter(table => `${table.label} ${table.key}`.toLocaleLowerCase().includes(search.value.toLocaleLowerCase())),
})))

async function load(query = routeQuery(route.query)) {
  try { data.value = await api.advanced(courseId, query) }
  catch (caught) { error.value = caught instanceof Error ? caught.message : '高级数据目录加载失败。' }
}
async function selectTable(table: string) {
  const query = { ...route.query, table, page: undefined }
  await router.push({ query })
  await load(routeQuery(query))
}
async function navigate(page: number) {
  const query = { ...route.query }
  if (page === 1) delete query.page
  else query.page = String(page)
  await router.push({ query })
  await load(routeQuery(query))
}
onMounted(() => load())
</script>

<template>
  <PageHeader title="高级数据工具"><template #kicker>Schema 与来源</template><p>按语义定位并审阅具体表、Schema 与来源；导出目录不是导航结构。</p></PageHeader>
  <Alert v-if="error" tone="danger">{{ error }}</Alert><p v-if="!data && !error" role="status">正在加载高级数据工具…</p>
  <div v-else-if="data" class="kt-advanced-layout">
    <aside class="kt-card kt-advanced-sidebar" aria-label="研究数据分类"><label>查找数据表<input v-model="search" type="search" placeholder="中文名称或表名"></label><details v-for="group in groups" :key="group.key" :open="group.active"><summary><span>{{ group.label }}</span><span>{{ group.tables.length }}</span></summary><div><button v-for="table in group.tables" :key="table.key" type="button" :aria-current="table.active ? 'page' : undefined" @click="selectTable(table.key)"><span>{{ table.label }}</span><code>{{ table.key }}</code><small>{{ table.role_label }}</small></button></div></details></aside>
    <section class="kt-advanced-content"><div class="kt-section-heading"><div><p class="kt-kicker">{{ data.selected_table.source_layer }}</p><h2>{{ data.selected_table.label }}</h2><code>{{ data.selected_table.key }}</code></div><strong>{{ data.selected_table.role_label }}</strong></div><Alert v-if="data.selected_table.role === 'source_of_truth'" tone="info"><strong>权威总事件表 / Source of Truth。</strong>用途分类视图不是并列的原始事实源。</Alert><Alert v-else-if="data.selected_table.role === 'classified_view'" tone="info"><strong>分类视图。</strong>权威来源仍是 assessment_events。</Alert><section class="kt-card kt-technical-info"><h3>技术信息</h3><dl><div><dt>数据表</dt><dd><code>{{ data.selected_table.key }}</code></dd></div><div><dt>导出路径</dt><dd><code>{{ data.selected_table.path }}</code></dd></div><div><dt>Schema</dt><dd><code>{{ data.selected_table.schema_version }}</code></dd></div><div><dt>来源</dt><dd>{{ data.selected_table.source_layer }}</dd></div></dl></section><EmptyState v-if="!data.selected_experiment" title="请先在范围栏选择实验"><p>预览只读取当前明确的数据范围。</p></EmptyState><template v-else-if="data.preview"><div class="kt-card kt-table-card"><div class="kt-table-scroll kt-desktop-only"><table class="kt-table"><thead><tr><th v-for="column in data.preview.columns" :key="column.name"><code>{{ column.name }}</code><span class="kt-cell-note">{{ column.description }}</span></th></tr></thead><tbody><tr v-for="(row, rowIndex) in data.preview.rows" :key="rowIndex"><td v-for="(value, columnIndex) in row" :key="columnIndex">{{ value ?? '' }}</td></tr></tbody></table></div><MobileCardList class="kt-mobile-only"><li v-for="(row, rowIndex) in data.preview.rows" :key="rowIndex" class="kt-mobile-data-card"><dl><div v-for="(column, columnIndex) in data.preview.columns" :key="column.name"><dt><code>{{ column.name }}</code></dt><dd>{{ row[columnIndex] ?? '—' }}</dd></div></dl></li></MobileCardList><Paginator :page="data.preview.page.number" :total-pages="data.preview.page.total_pages" @navigate="navigate" /></div><EmptyState v-if="!data.preview.rows.length" title="当前范围暂无记录" /></template></section>
  </div>
</template>
