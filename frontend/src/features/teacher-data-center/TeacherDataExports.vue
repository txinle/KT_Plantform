<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import { getBootstrapContext } from '@/app/bootstrap'
import Alert from '@/design-system/components/Alert.vue'
import PageHeader from '@/design-system/components/PageHeader.vue'

import { getTeacherDataCenterApi, queryString, routeQuery, type DataCenterExportCatalog } from './api'

const route = useRoute()
const courseId = Number(route.params.courseId)
const api = getTeacherDataCenterApi()
const bootstrap = getBootstrapContext()
const data = ref<DataCenterExportCatalog>()
const error = ref('')
const scopeEntries = computed(() => Object.entries(data.value?.scope.values || {}))

function packageUrl(key: string) {
  const query = { ...routeQuery(route.query), package: key }
  return `${route.path}${queryString(query)}`
}

onMounted(async () => {
  try { data.value = await api.exports(courseId, routeQuery(route.query)) }
  catch (caught) { error.value = caught instanceof Error ? caught.message : '导出目录加载失败。' }
})
</script>

<template>
  <PageHeader title="导出数据包"><template #kicker>冻结与审计</template><p>每次请求都会冻结新的 ExportSnapshot；范围、Schema 与高水位写入 Manifest。</p></PageHeader>
  <Alert v-if="error" tone="danger">{{ error }}</Alert><p v-if="!data && !error" role="status">正在加载导出目录…</p>
  <template v-else-if="data">
    <Alert tone="warning"><strong>实名数据边界：</strong>{{ data.identified_data_notice }}</Alert>
    <section v-if="data.selected_export_version" class="kt-card kt-export-version"><h2>本次导出版本：Version {{ data.selected_export_version.version_number }}</h2><p>该版本只用于本次导出，不改变普通教学范围。</p><a :href="data.actions.reset_url">更换版本</a></section>
    <section v-else-if="data.export_versions.length" class="kt-card kt-export-version"><h2>选择导出版本</h2><p>教学成绩、KT 和 CD 只能使用单一实验版本。</p><form method="get"><input v-for="[key, value] in scopeEntries.filter(([key]) => !['version_mode', 'version_id'].includes(key))" :key="key" type="hidden" :name="key" :value="String(value)"><input type="hidden" name="version_mode" value="single"><label>实验版本<select name="version_id" required><option value="">选择版本</option><option v-for="version in data.export_versions" :key="version.id" :value="version.id">Version {{ version.version_number }}</option></select></label><button class="kt-button" type="submit">使用此版本</button></form></section>
    <section class="kt-export-grid" aria-label="可用导出包"><article v-for="item in data.packages" :key="item.key" class="kt-card"><div><h2>{{ item.label }}</h2><p>{{ item.description }}</p></div><p v-if="item.blocked" class="kt-warning-text">该包需要选择单一实验版本。</p><a v-if="item.blocked" class="kt-button kt-button--secondary" href="#export-version">选择导出版本</a><a v-else class="kt-button" :href="packageUrl(item.key)">选择用途</a></article></section>
    <section v-if="data.selected_package" class="kt-card kt-export-confirm"><h2>确认导出：{{ data.selected_package.label }}</h2><p>将按范围“{{ data.scope.display }}”冻结新的 ExportSnapshot 和 ExportJob。该包含直接身份标识。</p><p v-if="data.selected_package.blocked" class="kt-warning-text">当前范围不是单一实验版本，不能生成。</p><form v-else method="post" :action="data.actions.create_url"><input type="hidden" name="csrfmiddlewaretoken" :value="bootstrap.csrf_token"><input v-for="[key, value] in scopeEntries" :key="key" type="hidden" :name="key" :value="String(value)"><input type="hidden" name="package" :value="data.selected_package.key"><input type="hidden" name="confirm_identified" value="yes"><button class="kt-button kt-button--danger" type="submit">确认生成实名数据包</button><a class="kt-button kt-button--secondary" :href="data.actions.reset_url">返回选择</a></form></section>
  </template>
</template>
