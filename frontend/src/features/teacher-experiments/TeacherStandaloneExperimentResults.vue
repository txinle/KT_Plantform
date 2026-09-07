<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import Alert from '@/design-system/components/Alert.vue'
import Button from '@/design-system/components/Button.vue'
import EmptyState from '@/design-system/components/EmptyState.vue'
import StatusBadge from '@/design-system/components/StatusBadge.vue'
import { getBootstrapContext } from '@/app/bootstrap'

import {
  getStandaloneExperimentApi,
  type StandaloneResults,
} from './standalone-api'

const route = useRoute()
const router = useRouter()
const api = getStandaloneExperimentApi()
const bootstrap = getBootstrapContext()
const experimentId = Number(route.params.experimentId)
const results = ref<StandaloneResults>()
const selectedStatus = ref(String(route.query.status || ''))
const loading = ref(true)
const error = ref('')

function statusTone(status: string) {
  if (status === 'completed') return 'success' as const
  if (status === 'in_progress') return 'warning' as const
  if (status === 'expired') return 'danger' as const
  return 'neutral' as const
}

function dateTime(value: string | null) {
  return value ? new Date(value).toLocaleString('zh-CN') : '—'
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const response = await api.results(experimentId, selectedStatus.value)
    if (response.kind === 'canonical') {
      await router.replace(response.canonical_url)
      return
    }
    results.value = response
    selectedStatus.value = response.selected_status
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '实验结果加载失败。'
  } finally {
    loading.value = false
  }
}

async function applyStatus() {
  await router.replace({ query: selectedStatus.value ? { status: selectedStatus.value } : {} })
  await load()
}

onMounted(load)
</script>

<template>
  <p v-if="loading" role="status">正在加载实验结果…</p>
  <template v-else-if="results">
    <header class="kt-experiment-page-header">
      <div class="kt-experiment-page-header__copy">
        <nav class="kt-experiment-breadcrumb" aria-label="层级目录"><RouterLink to="/teacher/experiments/">实验</RouterLink><span aria-hidden="true">/</span><RouterLink :to="`/teacher/experiments/${experimentId}/`">{{ results.experiment.name }}</RouterLink><span aria-hidden="true">/</span><span aria-current="page">采集结果</span></nav>
        <div class="kt-experiment-title-row"><h1>{{ results.experiment.name }}结果</h1></div>
        <p>主导出直接包含原始身份字段。请只选择研究所需数据，并限制在获授权的内部场景使用。</p>
      </div>
    </header>

    <Alert v-if="error" tone="danger">{{ error }}</Alert>
    <section class="kt-card kt-standalone-results-section" aria-labelledby="standalone-exports-title">
      <div class="kt-section-heading"><h2 id="standalone-exports-title">数据导出</h2></div>
      <article>
        <h3>实名可选字段 Excel</h3>
        <p>每名学生每道题一行。学号、姓名和班级默认包含且直接使用原始值；可取消不需要的列。</p>
        <form method="post" :action="results.actions.identified_export_url">
          <input type="hidden" name="csrfmiddlewaretoken" :value="bootstrap.csrf_token">
          <div class="kt-export-fields"><fieldset v-for="group in results.export_field_groups" :key="group.label"><legend>{{ group.label }}</legend><label v-for="field in group.fields" :key="field.key"><input type="checkbox" name="fields" :value="field.key" checked> {{ field.label }}</label></fieldset></div>
          <Alert tone="warning">该文件含可直接识别学生身份的信息。下载、保存和共享均应遵守研究授权与数据治理要求。</Alert>
          <Button type="submit">下载所选字段 Excel</Button>
        </form>
      </article>
      <article class="kt-standalone-export-row"><div><h3>实名导航事件</h3><p>每次进入题目一行，包含原始学号、姓名、班级、进入顺序和服务器接收时间。</p></div><form method="post" :action="results.actions.identified_navigation_export_url"><input type="hidden" name="csrfmiddlewaretoken" :value="bootstrap.csrf_token"><Button type="submit">下载实名导航事件 CSV</Button></form></article>
      <details><summary>实名 CSV 导出</summary><div class="kt-standalone-export-links"><a class="kt-button kt-button--secondary" :href="results.actions.kt_export_url">实名 KT CSV</a><a class="kt-button kt-button--secondary" :href="results.actions.navigation_export_url">实名导航 CSV</a><a class="kt-button kt-button--danger" :href="results.actions.identity_export_url">学生名册 CSV</a></div></details>
    </section>

    <section class="kt-card kt-standalone-results-section" aria-labelledby="standalone-sessions-title">
      <div class="kt-section-heading"><h2 id="standalone-sessions-title">学生会话</h2><span>{{ results.sessions.length }} 条</span></div>
      <form class="kt-inline-panel" @submit.prevent="applyStatus"><label for="standalone-session-status">完成状态<select id="standalone-session-status" v-model="selectedStatus"><option value="">全部状态</option><option v-for="choice in results.session_status_choices" :key="choice.value" :value="choice.value">{{ choice.label }}</option></select></label><div class="kt-inline-actions"><Button type="submit">筛选</Button><RouterLink v-if="selectedStatus" class="kt-button kt-button--secondary" :to="`/teacher/experiments/${experimentId}/results/`" @click="selectedStatus = ''; load()">清除筛选</RouterLink></div></form>
      <EmptyState v-if="!results.sessions.length" :title="selectedStatus ? '该状态下暂无学生会话' : '暂无学生作答会话'"><p>{{ selectedStatus ? '请选择其他状态或清除筛选。' : '学生开始实验后，会话状态和提交数量将在此显示。' }}</p></EmptyState>
      <div v-else class="kt-table-scroll"><table class="kt-table"><thead><tr><th>学号</th><th>姓名</th><th>状态</th><th>开始时间</th><th>提交题数</th></tr></thead><tbody><tr v-for="session in results.sessions" :key="session.id"><td>{{ session.student_number || '—' }}</td><td>{{ session.student_name }}</td><td><StatusBadge :tone="statusTone(session.status)" :label="session.status_label" /></td><td>{{ dateTime(session.started_at) }}</td><td>{{ session.submission_count }}</td></tr></tbody></table></div>
    </section>
  </template>
  <Alert v-else tone="danger">{{ error || '实验结果不存在或无法访问。' }}</Alert>
</template>

<style scoped>
.kt-standalone-results-section { margin-bottom: 1.25rem; }
.kt-export-fields { display: grid; gap: 1rem; grid-template-columns: repeat(auto-fit, minmax(13rem, 1fr)); margin: 1rem 0; }
.kt-export-fields fieldset { display: grid; gap: .45rem; }
.kt-export-fields legend { font-weight: 700; }
.kt-standalone-export-row { align-items: center; display: flex; gap: 1rem; justify-content: space-between; margin: 1rem 0; }
.kt-standalone-export-links { display: flex; flex-wrap: wrap; gap: .6rem; margin-top: .8rem; }
@media (max-width: 640px) { .kt-standalone-export-row { align-items: stretch; flex-direction: column; } }
</style>
