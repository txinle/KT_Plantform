<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import Alert from '@/design-system/components/Alert.vue'
import Button from '@/design-system/components/Button.vue'
import EmptyState from '@/design-system/components/EmptyState.vue'
import StatusBadge from '@/design-system/components/StatusBadge.vue'

import {
  getStandaloneExperimentApi,
  type StandaloneDetail,
} from './standalone-api'

const route = useRoute()
const router = useRouter()
const api = getStandaloneExperimentApi()
const experimentId = Number(route.params.experimentId)
const detail = ref<StandaloneDetail>()
const selectedStudentIds = ref<number[]>([])
const loading = ref(true)
const working = ref(false)
const error = ref('')
const showDelete = ref(false)
const deleteConfirmation = ref('')

function statusTone(status: string) {
  if (status === 'draft') return 'warning' as const
  if (status === 'archived') return 'neutral' as const
  return 'success' as const
}

function dateTime(value: string) {
  return new Date(value).toLocaleString('zh-CN')
}

function setDetail(value: StandaloneDetail) {
  detail.value = value
  selectedStudentIds.value = value.available_students
    .filter((student) => student.selected)
    .map((student) => student.id)
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const response = await api.detail(experimentId)
    if (response.kind === 'canonical') {
      await router.replace(response.canonical_url)
      return
    }
    setDetail(response)
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '实验详情加载失败。'
  } finally {
    loading.value = false
  }
}

function toggleStudent(studentId: number, checked: boolean) {
  const selected = new Set(selectedStudentIds.value)
  if (checked) selected.add(studentId)
  else selected.delete(studentId)
  selectedStudentIds.value = [...selected]
}

async function assignStudents() {
  working.value = true
  error.value = ''
  try {
    setDetail(await api.assignStudents(experimentId, selectedStudentIds.value))
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '学生分配保存失败。'
  } finally {
    working.value = false
  }
}

async function syncQuestions() {
  working.value = true
  error.value = ''
  try {
    setDetail(await api.syncQuestions(experimentId))
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '题目同步失败。'
  } finally {
    working.value = false
  }
}

async function publish() {
  working.value = true
  error.value = ''
  try {
    setDetail(await api.publish(experimentId))
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '实验发布失败。'
  } finally {
    working.value = false
  }
}

async function remove() {
  if (deleteConfirmation.value !== 'delete') return
  working.value = true
  error.value = ''
  try {
    await api.remove(experimentId)
    await router.replace('/teacher/experiments/')
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '实验删除失败。'
  } finally {
    working.value = false
  }
}

onMounted(load)
</script>

<template>
  <p v-if="loading" role="status">正在加载实验详情…</p>
  <template v-else-if="detail">
    <header class="kt-experiment-page-header">
      <div class="kt-experiment-page-header__copy">
        <nav class="kt-experiment-breadcrumb" aria-label="层级目录"><RouterLink to="/teacher/experiments/">实验</RouterLink><span aria-hidden="true">/</span><span aria-current="page">{{ detail.experiment.name }}</span></nav>
        <div class="kt-experiment-title-row"><h1>{{ detail.experiment.name }}</h1><StatusBadge :tone="statusTone(detail.experiment.status)" :label="detail.experiment.status_label" /></div>
        <p>{{ detail.experiment.description || '未填写实验说明。' }}</p>
        <small class="kt-standalone-updated">更新：{{ dateTime(detail.experiment.updated_at) }}</small>
      </div>
      <div class="kt-page-actions">
        <RouterLink class="kt-button kt-button--secondary" :to="`/teacher/experiments/${experimentId}/results/`">结果与导出</RouterLink>
        <RouterLink v-if="detail.actions.can_edit" class="kt-button kt-button--secondary" :to="`/teacher/experiments/${experimentId}/edit/`">编辑配置</RouterLink>
        <button class="kt-button kt-button--danger" type="button" @click="showDelete = !showDelete">删除实验</button>
      </div>
    </header>

    <Alert v-if="error" tone="danger">{{ error }}</Alert>
    <ol class="kt-standalone-lifecycle" aria-label="实验生命周期"><li class="is-complete">配置草稿</li><li :class="{ 'is-current': detail.experiment.status === 'draft', 'is-complete': detail.experiment.status !== 'draft' }">发布快照</li><li :class="{ 'is-complete': ['closed', 'archived'].includes(detail.experiment.status) }">结束采集</li></ol>
    <form v-if="showDelete" class="kt-inline-panel kt-inline-panel--danger" @submit.prevent="remove"><h2>确认永久删除实验</h2><p>这会删除实验、发布版本、会话和作答记录。输入 <code>delete</code> 后确认。</p><label>确认<input v-model="deleteConfirmation" autocomplete="off"></label><div class="kt-inline-actions"><Button type="submit" :disabled="working || deleteConfirmation !== 'delete'">{{ working ? '正在删除…' : '永久删除' }}</Button><button class="kt-text-button" type="button" @click="showDelete = false">取消</button></div></form>

    <div class="kt-standalone-detail-grid">
      <div>
        <section class="kt-card kt-definition-summary" aria-labelledby="standalone-configuration-title">
          <div class="kt-section-heading"><h2 id="standalone-configuration-title">实验配置</h2></div>
          <dl class="kt-description-list kt-definition-summary__list">
            <div><dt>题库</dt><dd>{{ detail.experiment.question_bank.name }}</dd></div>
            <div><dt>题目数量</dt><dd>{{ detail.experiment.question_count }} 题</dd></div>
            <div><dt>选题方式</dt><dd>{{ detail.experiment.selection_mode_label }}</dd></div>
            <div><dt>题目顺序</dt><dd>{{ detail.experiment.ordering_mode_label }}</dd></div>
            <div><dt>反馈</dt><dd>{{ detail.experiment.feedback_mode_label }}</dd></div>
            <div><dt>反馈发布</dt><dd>{{ detail.experiment.feedback_release_policy_label }}</dd></div>
            <div><dt>时间</dt><dd>{{ detail.experiment.time_mode_label }}</dd></div>
            <div><dt>续答</dt><dd>{{ detail.experiment.resume_mode_label }}</dd></div>
          </dl>
          <div v-if="detail.experiment.selection_mode === 'quota'" class="kt-table-scroll"><table class="kt-table"><thead><tr><th>题型</th><th>每生配额</th></tr></thead><tbody><tr v-for="row in detail.selection.quota_rows" :key="row.question_type"><td>{{ row.label }}</td><td>{{ row.amount }}</td></tr></tbody></table></div>
        </section>

        <section class="kt-card kt-standalone-section" aria-labelledby="standalone-inventory-title">
          <div class="kt-section-heading"><h2 id="standalone-inventory-title">候选池库存</h2></div>
          <p>当前题库可用候选题：<strong>{{ detail.selection.current_candidate_count }}</strong> 题。发布时会重新锁定并校验题池。</p>
          <div class="kt-table-scroll"><table class="kt-table"><thead><tr><th>题型</th><th>需要</th><th>可用</th></tr></thead><tbody><tr v-for="row in detail.selection.current_inventory_rows" :key="row.label"><td>{{ row.label }}</td><td>{{ row.need }}</td><td>{{ row.available }}</td></tr></tbody></table></div>
        </section>

        <section class="kt-card kt-standalone-section" aria-labelledby="standalone-version-title">
          <div class="kt-section-heading"><h2 id="standalone-version-title">发布快照</h2></div>
          <EmptyState v-if="!detail.selection.version_rows.length" title="尚未生成发布快照"><p>完成题目、主 KC 和学生检查后再发布。</p></EmptyState>
          <article v-for="version in detail.selection.version_rows" :key="version.version_number" class="kt-standalone-version"><h3>版本 {{ version.version_number }}</h3><p>候选池 {{ version.candidate_count }} 题 · {{ dateTime(version.published_at) }}</p><div class="kt-table-scroll"><table class="kt-table"><thead><tr><th>题型</th><th>需要</th><th>可用</th></tr></thead><tbody><tr v-for="row in version.inventory_rows" :key="row.label"><td>{{ row.label }}</td><td>{{ row.need }}</td><td>{{ row.available }}</td></tr></tbody></table></div></article>
        </section>
      </div>

      <aside class="kt-experiment-side-stack">
        <section class="kt-card" aria-labelledby="standalone-students-title">
          <div class="kt-section-heading"><h2 id="standalone-students-title">参加学生</h2><span>{{ detail.experiment.student_count }} 人</span></div>
          <form v-if="detail.actions.can_assign" class="kt-standalone-student-picker" @submit.prevent="assignStudents"><label v-for="student in detail.available_students" :key="student.id"><input :checked="selectedStudentIds.includes(student.id)" type="checkbox" @change="toggleStudent(student.id, ($event.target as HTMLInputElement).checked)"><span><b>{{ student.student_number || '—' }}</b> {{ student.display_name }}<small>{{ student.class_name || '未分班' }}</small></span></label><EmptyState v-if="!detail.available_students.length" title="暂无可分配学生"><p>请先创建可用班级和学生。</p></EmptyState><Button type="submit" :disabled="working">{{ working ? '正在保存…' : '保存学生分配' }}</Button></form>
          <ul v-else class="kt-standalone-roster"><li v-for="student in detail.students" :key="student.id"><b>{{ student.student_number || '—' }}</b><span>{{ student.display_name }}</span><small>{{ student.class_name || '未分班' }}</small></li><li v-if="!detail.students.length">未分配学生</li></ul>
        </section>

        <section class="kt-card" aria-labelledby="standalone-research-title"><div class="kt-section-heading"><h2 id="standalone-research-title">研究过程采集</h2></div><dl class="kt-description-list"><div><dt>作答信心</dt><dd>{{ detail.experiment.confidence_mode_label }}</dd></div><template v-if="detail.experiment.confidence_mode === 'sampled'"><div><dt>抽样单位</dt><dd>{{ detail.experiment.confidence_sampling_unit_label }}</dd></div><div><dt>抽样概率</dt><dd>{{ detail.experiment.confidence_probability }}</dd></div></template><div><dt>分配规则版本</dt><dd>{{ detail.experiment.confidence_assignment_version }}</dd></div><div><dt>量表版本</dt><dd>{{ detail.experiment.confidence_scale_version }}</dd></div><div><dt>提示版本</dt><dd>{{ detail.experiment.confidence_prompt_version }}</dd></div><div><dt>随机种子</dt><dd>{{ detail.experiment.confidence_randomization_seed || '发布时自动生成' }}</dd></div><div><dt>活跃心跳</dt><dd>{{ detail.experiment.heartbeat_interval_seconds }} 秒</dd></div></dl></section>

        <section v-if="detail.actions.can_publish" class="kt-card" aria-labelledby="standalone-publish-title"><div class="kt-section-heading"><h2 id="standalone-publish-title">发布前检查</h2></div><ul class="kt-readiness-checklist"><li>{{ detail.experiment.question_count ? '通过' : '待办' }} 已载入题目：{{ detail.experiment.question_count }}</li><li>{{ detail.experiment.student_count ? '通过' : '待办' }} 已分配学生：{{ detail.experiment.student_count }}</li><li>核对所有题目均有主 KC。</li></ul><div class="kt-form-actions"><Button data-action="sync-questions" type="button" :disabled="working" @click="syncQuestions">重新同步题库题目</Button><Button data-action="publish" type="button" :disabled="working" @click="publish">{{ working ? '正在发布…' : '发布并锁定实验' }}</Button></div></section>
        <Alert v-else tone="info">实验已锁定。题目、配置和学生分配以发布快照为准。</Alert>
      </aside>
    </div>
  </template>
  <Alert v-else tone="danger">{{ error || '实验不存在或无法访问。' }}</Alert>
</template>

<style scoped>
.kt-standalone-detail-grid { display: grid; gap: 1.25rem; grid-template-columns: minmax(0, 1.5fr) minmax(18rem, .85fr); }
.kt-standalone-updated { opacity: .72; }
.kt-standalone-lifecycle { display: flex; gap: .5rem; list-style: none; margin: 1rem 0; padding: 0; }
.kt-standalone-lifecycle li { border: 1px solid var(--kt-border, #d8d9de); border-radius: 999px; padding: .3rem .65rem; }
.kt-standalone-lifecycle .is-current { border-color: var(--kt-accent, #1f5eff); }
.kt-standalone-lifecycle .is-complete { background: var(--kt-success-subtle, #edf8ef); }
.kt-standalone-section, .kt-standalone-version { margin-top: 1rem; }
.kt-standalone-version + .kt-standalone-version { border-top: 1px solid var(--kt-border, #d8d9de); padding-top: 1rem; }
.kt-standalone-student-picker { display: grid; gap: .65rem; }
.kt-standalone-student-picker label, .kt-standalone-roster li { align-items: start; display: flex; gap: .55rem; }
.kt-standalone-student-picker small, .kt-standalone-roster small { display: block; opacity: .72; }
.kt-standalone-roster { display: grid; gap: .65rem; list-style: none; margin: 0; padding: 0; }
@media (max-width: 850px) { .kt-standalone-detail-grid { grid-template-columns: 1fr; } }
</style>
