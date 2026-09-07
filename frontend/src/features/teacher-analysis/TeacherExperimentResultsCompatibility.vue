<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import Alert from '@/design-system/components/Alert.vue'
import EmptyState from '@/design-system/components/EmptyState.vue'
import PageHeader from '@/design-system/components/PageHeader.vue'
import StatusBadge from '@/design-system/components/StatusBadge.vue'

import { getTeacherAnalysisApi, type ExperimentResultsResolution } from './api'

const route = useRoute()
const router = useRouter()
const api = getTeacherAnalysisApi()
const courseId = Number(route.params.courseId)
const experimentId = Number(route.params.experimentId)
const page = ref<ExperimentResultsResolution>()
const loading = ref(true)
const error = ref('')
const selectedStatus = ref(String(route.query.status || ''))
const title = computed(() => page.value ? `${page.value.experiment.name}结果` : '实验结果')

function displayDate(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString('zh-CN', { hour12: false })
}

function tone(status: string): 'success' | 'warning' | 'danger' | 'neutral' {
  if (status === 'completed') return 'success'
  if (status === 'in_progress') return 'warning'
  if (status === 'expired') return 'danger'
  return 'neutral'
}

async function load(status = selectedStatus.value) {
  loading.value = true
  error.value = ''
  try {
    page.value = await api.experimentResultsResolution(courseId, experimentId, status)
    selectedStatus.value = page.value.selected_status
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '实验结果加载失败。'
  } finally {
    loading.value = false
  }
}

async function applyStatus() {
  const status = selectedStatus.value
  await router.push({ query: status ? { status } : {} })
  await load(status)
}

onMounted(load)
</script>

<template>
  <PageHeader :title="title">
    <template #kicker>{{ page?.course.code || '实验结果' }}</template>
    <p>结果范围和可进入的班级分析由服务端按发布时冻结任务解析。</p>
    <template #actions><a v-if="page" class="kt-button kt-button--secondary" :href="page.actions.back_url">返回实验列表</a></template>
  </PageHeader>

  <Alert v-if="error" tone="danger">{{ error }}</Alert>
  <p v-if="loading" role="status">正在解析实验结果…</p>

  <template v-else-if="page">
    <Alert v-if="page.kind === 'canonical' && page.canonical_url" tone="info">该实验结果已解析到唯一的班级任务。<a :href="page.canonical_url">进入班级分析</a></Alert>

    <section v-else-if="page.kind === 'class_tasks'" class="kt-card kt-result-section" aria-labelledby="class-task-title">
      <div class="kt-section-heading"><div><h2 id="class-task-title">选择班级任务</h2><p>教学分析以发布时冻结的班级任务为范围。请选择要查看的班级。</p></div></div>
      <EmptyState v-if="!page.tasks.length" title="没有可进入普通统计的班级任务"><p>草稿、归档实验或历史冲突不会进入班级教学分析。</p></EmptyState>
      <div v-else class="kt-table-scroll"><table class="kt-table"><thead><tr><th>学年</th><th>班级</th><th>发布时班级快照</th><th>操作</th></tr></thead><tbody><tr v-for="task in page.tasks" :key="task.analysis_url"><td>{{ task.academic_year || '未设置' }}</td><td>{{ task.class_name }}</td><td>{{ task.class_snapshot_name }}</td><td><a :href="task.analysis_url">查看班级分析</a></td></tr></tbody></table></div>
    </section>

    <template v-else-if="page.kind === 'sessions'">
      <form class="kt-card kt-result-filter" aria-label="筛选会话状态" @submit.prevent="applyStatus">
        <label for="session-status-filter">完成状态</label>
        <select id="session-status-filter" v-model="selectedStatus"><option value="">全部状态</option><option v-for="choice in page.session_status_choices" :key="choice.value" :value="choice.value">{{ choice.label }}</option></select>
        <button class="kt-button kt-button--secondary" type="submit">筛选</button>
        <a v-if="page.selected_status" :href="page.actions.clear_filter_url">清除筛选</a>
      </form>
      <section class="kt-card kt-result-section" aria-labelledby="session-list-title">
        <div class="kt-section-heading"><h2 id="session-list-title">学生会话</h2><span>{{ page.sessions.length }} 条</span></div>
        <EmptyState v-if="!page.sessions.length" :title="page.selected_status ? '该状态下暂无学生会话' : '暂无学生作答会话'"><p>{{ page.selected_status ? '请选择其他状态或清除筛选。' : '学生开始实验后，会话状态和提交数量将在此显示。' }}</p></EmptyState>
        <div v-else class="kt-table-scroll"><table class="kt-table"><thead><tr><th>学号</th><th>姓名</th><th>状态</th><th>开始时间</th><th>提交题数</th><th>操作</th></tr></thead><tbody><tr v-for="session in page.sessions" :key="session.detail_url"><td>{{ session.student_number }}</td><td>{{ session.display_name }}</td><td><StatusBadge :tone="tone(session.status)" :label="session.status_label" /></td><td>{{ displayDate(session.started_at) }}</td><td>{{ session.submission_count }}</td><td><a :href="session.detail_url">查看作答详情</a></td></tr></tbody></table></div>
      </section>
    </template>
  </template>
</template>

<style scoped>
.kt-result-section { margin-top: 1.25rem; }
.kt-result-section p { color: var(--kt-text-muted); margin: .25rem 0 1rem; }
.kt-result-filter { align-items: end; display: flex; flex-wrap: wrap; gap: .75rem; margin-top: 1.25rem; }
.kt-result-filter label { display: grid; gap: .35rem; }
</style>
