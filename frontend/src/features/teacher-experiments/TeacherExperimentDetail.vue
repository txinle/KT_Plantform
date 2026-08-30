<script setup lang="ts">
import { computed, inject, onMounted, reactive, ref, type Ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import type { components } from '@/api/generated/schema'
import Alert from '@/design-system/components/Alert.vue'
import Button from '@/design-system/components/Button.vue'
import EmptyState from '@/design-system/components/EmptyState.vue'
import StatusBadge from '@/design-system/components/StatusBadge.vue'
import Icon from '@/design-system/Icon.vue'

import {
  getTeacherExperimentApi,
  type ClassTask,
  type ClassTaskProgress,
  type TeacherExperiment,
  type TeacherExperimentReadiness,
} from './api'

const route = useRoute()
const router = useRouter()
const api = getTeacherExperimentApi()
const courseId = Number(route.params.courseId)
const experimentId = Number(route.params.experimentId)
const course = inject<Ref<components['schemas']['CourseContext'] | undefined>>(
  'teacher-experiments-course',
  ref<components['schemas']['CourseContext']>(),
)
const experiment = ref<TeacherExperiment>()
const readiness = ref<TeacherExperimentReadiness>()
const tasks = ref<ClassTask[]>([])
const progress = reactive<Record<number, ClassTaskProgress>>({})
const loading = ref(true)
const working = ref(false)
const error = ref('')
const scheduleTaskId = ref<number>()
const scheduleForm = reactive({ opens_at: '', closes_at: '', reason: '' })
const confirmation = reactive({ taskId: 0, action: '' as '' | 'cancel' | 'manual_close', reason: '' })
const activeTab = ref<'definition' | 'tasks' | 'collection'>('definition')

const readinessTone = computed(() => readiness.value?.ready ? 'success' : 'warning')
const courseName = computed(() => course?.value?.name || '课程')
const isPublished = computed(() => experiment.value?.status !== 'draft')

function displayDate(value: string | null | undefined) {
  return value ? new Date(value).toLocaleString('zh-CN') : '未设置'
}

function displayLimit(value: number | null | undefined, unit: string) {
  return value === null || value === undefined ? '未设置' : `${value} ${unit}`
}

function dateTimeLocal(value: string | null) {
  if (!value) return ''
  const date = new Date(value)
  const offset = date.getTimezoneOffset() * 60_000
  return new Date(date.getTime() - offset).toISOString().slice(0, 16)
}

function isoOrNull(value: string) {
  return value ? new Date(value).toISOString() : null
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [definition, ready, taskCollection] = await Promise.all([
      api.detail(courseId, experimentId),
      api.readiness(courseId, experimentId),
      api.classTasks(experimentId),
    ])
    experiment.value = definition
    activeTab.value = definition.status === 'draft' ? 'definition' : 'tasks'
    readiness.value = ready
    tasks.value = taskCollection.items
    const rows = await Promise.all(tasks.value.map(async (task) => [task.id, await api.progress(task.id)] as const))
    for (const [taskId, row] of rows) progress[taskId] = row
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '实验详情加载失败。'
  } finally {
    loading.value = false
  }
}

async function startPublish() {
  working.value = true
  error.value = ''
  try {
    const plan = await api.startPlan(courseId, experimentId)
    await router.push(`/teacher/courses/${courseId}/experiments/${experimentId}/publish-plan/${plan.id}/step/${plan.current_step || 1}/`)
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '无法进入发布流程。'
  } finally {
    working.value = false
  }
}

function editSchedule(task: ClassTask) {
  scheduleTaskId.value = task.id
  scheduleForm.opens_at = dateTimeLocal(task.opens_at)
  scheduleForm.closes_at = dateTimeLocal(task.closes_at)
  scheduleForm.reason = ''
}

async function reviseSchedule(taskId: number) {
  working.value = true
  try {
    await api.reviseSchedule(taskId, {
      opens_at: isoOrNull(scheduleForm.opens_at),
      closes_at: isoOrNull(scheduleForm.closes_at),
      reason: scheduleForm.reason,
    })
    scheduleTaskId.value = undefined
    await load()
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '排期修订失败。'
  } finally {
    working.value = false
  }
}

function requestTerminalAction(taskId: number, action: 'cancel' | 'manual_close') {
  confirmation.taskId = taskId
  confirmation.action = action
  confirmation.reason = ''
}

async function applyTerminalAction() {
  working.value = true
  try {
    if (confirmation.action === 'cancel') await api.cancel(confirmation.taskId, confirmation.reason)
    if (confirmation.action === 'manual_close') await api.manualClose(confirmation.taskId, confirmation.reason)
    confirmation.action = ''
    await load()
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '任务状态更新失败。'
  } finally {
    working.value = false
  }
}

onMounted(load)
</script>

<template>
  <p v-if="loading" role="status">正在加载实验详情…</p>
  <template v-else-if="experiment">
    <header class="kt-experiment-page-header">
      <div class="kt-experiment-page-header__copy">
        <nav class="kt-experiment-breadcrumb" aria-label="层级目录">
          <a href="/teacher/courses/">我的课程</a><span aria-hidden="true">/</span>
          <a :href="`/teacher/courses/${courseId}/`">{{ courseName }}</a><span aria-hidden="true">/</span>
          <a :href="`/teacher/courses/${courseId}/experiments/`">实验</a><span aria-hidden="true">/</span>
          <span aria-current="page">{{ experiment.name }}</span>
        </nav>
        <div class="kt-experiment-title-row">
          <h1>{{ experiment.name }}</h1>
          <StatusBadge :label="experiment.status_label" :tone="experiment.status === 'draft' ? 'warning' : 'success'" />
        </div>
        <p>{{ experiment.description || '未填写实验说明。' }}</p>
      </div>
    </header>

    <nav class="kt-experiment-tabs" role="tablist" aria-label="实验详情导航">
      <button id="experiment-tab-definition" type="button" role="tab" :aria-selected="activeTab === 'definition'" aria-controls="experiment-panel-definition" :class="{ 'is-active': activeTab === 'definition' }" @click="activeTab = 'definition'">实验定义</button>
      <button id="experiment-tab-tasks" type="button" role="tab" :aria-selected="activeTab === 'tasks'" aria-controls="experiment-panel-tasks" :class="{ 'is-active': activeTab === 'tasks' }" @click="activeTab = 'tasks'">班级发布</button>
      <button id="experiment-tab-collection" type="button" role="tab" :aria-selected="activeTab === 'collection'" aria-controls="experiment-panel-collection" :class="{ 'is-active': activeTab === 'collection' }" @click="activeTab = 'collection'">采集状态</button>
    </nav>

    <Alert v-if="error" tone="danger">{{ error }}</Alert>

    <section v-if="activeTab === 'definition'" id="experiment-panel-definition" role="tabpanel" aria-labelledby="experiment-tab-definition">
      <div class="kt-experiment-draft-grid">
        <section class="kt-card kt-definition-summary" aria-labelledby="definition-summary">
          <div class="kt-section-heading"><h2 id="definition-summary">实验设置摘要</h2></div>
          <dl class="kt-description-list kt-definition-summary__list">
            <div><span class="kt-summary-icon" data-summary-icon><Icon name="bank" :size="17" /></span><dt>题库</dt><dd>{{ experiment.question_bank.name }}</dd></div>
            <div><span class="kt-summary-icon" data-summary-icon><Icon name="definition" :size="17" /></span><dt>选题</dt><dd>{{ experiment.selection_mode_label }}</dd></div>
            <div><span class="kt-summary-icon" data-summary-icon><Icon name="summary" :size="17" /></span><dt>题目顺序</dt><dd>{{ experiment.ordering_mode_label }}</dd></div>
            <div><span class="kt-summary-icon" data-summary-icon><Icon name="status" :size="17" /></span><dt>反馈</dt><dd>{{ experiment.feedback_mode_label }}</dd></div>
            <div><span class="kt-summary-icon" data-summary-icon><Icon name="status" :size="17" /></span><dt>总限时</dt><dd>{{ displayLimit(experiment.total_time_limit_minutes, '分钟') }}</dd></div>
            <div><span class="kt-summary-icon" data-summary-icon><Icon name="status" :size="17" /></span><dt>单题限时</dt><dd>{{ displayLimit(experiment.per_question_time_limit_seconds, '秒') }}</dd></div>
            <div><span class="kt-summary-icon" data-summary-icon><Icon name="data" :size="17" /></span><dt>中断续答</dt><dd>{{ experiment.resume_mode_label }}</dd></div>
            <div><span class="kt-summary-icon" data-summary-icon><Icon name="data" :size="17" /></span><dt>作答信心</dt><dd>{{ experiment.confidence_mode_label }}</dd></div>
          </dl>
          <div v-if="experiment.can_edit" class="kt-definition-summary__action">
            <a class="kt-button kt-button--secondary" :href="`/teacher/courses/${courseId}/experiments/${experimentId}/edit/`">编辑实验定义</a>
          </div>
        </section>
        <aside class="kt-experiment-side-stack">
          <section class="kt-card kt-publish-ready-card" aria-labelledby="publish-ready-title">
            <div v-if="!isPublished" class="kt-publish-empty-illustration" aria-hidden="true"><Icon name="publish" :size="31" /><Icon name="classes" :size="22" /><Icon name="check" :size="17" /></div>
            <h2 id="publish-ready-title">发布到班级</h2>
            <p v-if="isPublished">当前实验已有 {{ tasks.length }} 个班级任务，可继续将同一实验定义发布到其他班级。</p>
            <p v-else>首次发布后实验定义将冻结；班级、参与者和开放时段将在下一步确认。</p>
            <Button data-action="publish-from-definition" :disabled="working" @click="startPublish">{{ working ? '正在进入…' : (isPublished ? '继续发布' : '发布到班级') }}</Button>
          </section>
          <section class="kt-card kt-readiness-card" aria-labelledby="readiness-summary">
            <div class="kt-section-heading"><h2 id="readiness-summary">发布前检查</h2><StatusBadge :label="readiness?.ready ? '定义就绪' : '需要处理'" :tone="readinessTone" /></div>
            <dl class="kt-readiness-facts">
              <div><dt>可用资源</dt><dd>{{ readiness?.candidate_count ?? 0 }} 道可用题目</dd></div>
              <div><dt>已关联参与者</dt><dd>{{ readiness?.participant_count ?? 0 }}</dd></div>
            </dl>
            <ul class="kt-readiness-checklist">
              <li><Icon :name="readiness?.ready ? 'check' : 'warning'" :size="17" /><span>{{ readiness?.ready ? '实验定义已通过基础检查' : '实验定义仍需完成基础检查' }}</span></li>
              <li><Icon name="bank" :size="17" /><span>可用题目：{{ readiness?.candidate_count ?? 0 }} 道</span></li>
              <li><Icon name="classes" :size="17" /><span>已关联参与者：{{ readiness?.participant_count ?? 0 }}</span></li>
              <li v-for="issue in readiness?.issues || []" :key="issue"><Icon name="warning" :size="17" /><span>{{ issue }}</span></li>
            </ul>
          </section>
        </aside>
      </div>
      <Alert tone="info">{{ isPublished ? '实验定义已经冻结；如需调整内容，请按现有流程创建新的实验定义。' : '首次正式发布后，实验定义将冻结；后续可继续发布到其他班级。' }}</Alert>
    </section>

    <section v-else-if="activeTab === 'tasks'" id="experiment-panel-tasks" role="tabpanel" aria-labelledby="experiment-tab-tasks">
      <Alert v-if="isPublished" tone="success">已发布 {{ tasks.length }} 个班级任务。任务状态和可用操作由服务端返回。</Alert>
      <div class="kt-published-layout">
        <section class="kt-card kt-published-task-table" aria-labelledby="class-task-title">
          <div class="kt-section-heading"><div><p class="kt-kicker">班级发布</p><h2 id="class-task-title">已发布班级任务</h2></div><span>{{ tasks.length }} 个</span></div>
          <EmptyState v-if="!tasks.length" title="尚未发布到班级"><p>完成发布检查并提交后，这里会显示每个班级的任务状态。</p></EmptyState>
          <div v-else class="kt-table-scroll">
            <table class="kt-table">
              <thead><tr><th>班级</th><th>状态</th><th>参与者</th><th>开放时段</th><th>未开始</th><th>进行中</th><th>已完成</th><th>操作</th></tr></thead>
              <tbody>
                <template v-for="task in tasks" :key="task.id">
                  <tr>
                    <td><strong>{{ task.class_name }}</strong></td><td><StatusBadge :label="task.business_status" tone="neutral" /></td>
                    <td>{{ progress[task.id]?.total_participants ?? task.participant_count }}</td><td>{{ displayDate(task.opens_at) }} — {{ displayDate(task.closes_at) }}</td>
                    <td>{{ progress[task.id]?.not_started ?? '—' }}</td><td>{{ progress[task.id]?.in_progress ?? '—' }}</td><td>{{ progress[task.id]?.completed ?? '—' }}</td>
                    <td>
                      <div class="kt-table-actions">
                        <button v-if="task.available_actions.includes('revise_schedule')" data-action="revise-schedule" class="kt-text-button" type="button" @click="editSchedule(task)">修订排期</button>
                        <button v-if="task.available_actions.includes('cancel')" data-action="cancel-task" class="kt-text-button kt-text-button--danger" type="button" @click="requestTerminalAction(task.id, 'cancel')">取消任务</button>
                        <button v-if="task.available_actions.includes('manual_close')" data-action="manual-close" class="kt-text-button kt-text-button--danger" type="button" @click="requestTerminalAction(task.id, 'manual_close')">提前结束</button>
                      </div>
                    </td>
                  </tr>
                  <tr v-if="scheduleTaskId === task.id"><td colspan="8"><form class="kt-inline-panel" @submit.prevent="reviseSchedule(task.id)"><h3>修订排期</h3><div class="kt-inline-form-grid"><label>开放时间<input v-model="scheduleForm.opens_at" type="datetime-local"></label><label>截止时间<input v-model="scheduleForm.closes_at" type="datetime-local"></label><label>原因<input v-model="scheduleForm.reason" maxlength="500"></label></div><div class="kt-inline-actions"><Button type="submit" :disabled="working">保存修订</Button><button type="button" class="kt-text-button" @click="scheduleTaskId = undefined">取消</button></div></form></td></tr>
                  <tr v-if="confirmation.taskId === task.id && confirmation.action"><td colspan="8"><form class="kt-inline-panel kt-inline-panel--danger" @submit.prevent="applyTerminalAction"><h3>{{ confirmation.action === 'cancel' ? '确认取消该班任务' : '确认提前结束该班任务' }}</h3><p>此操作会立即改变学生端可用状态，请填写可审计原因后确认。</p><label>原因<input v-model="confirmation.reason" maxlength="500" required></label><div class="kt-inline-actions"><button class="kt-button kt-button--danger" type="submit" :disabled="working">确认执行</button><button type="button" class="kt-text-button" @click="confirmation.action = ''">返回</button></div></form></td></tr>
                </template>
              </tbody>
            </table>
          </div>
        </section>
        <aside class="kt-experiment-side-stack">
          <section class="kt-card kt-publish-ready-card"><h2>继续发布</h2><p>使用当前实验定义发布到尚未覆盖的班级。</p><Button data-action="continue-publish" :disabled="working" @click="startPublish">继续发布</Button></section>
          <section class="kt-card kt-publish-ready-card"><h2>数据中心</h2><p>查看和导出班级采集数据与相关指标。</p><a class="kt-button kt-button--secondary" :href="`/teacher/courses/${courseId}/data/`">查看采集数据</a></section>
        </aside>
      </div>
    </section>

    <section v-else id="experiment-panel-collection" role="tabpanel" aria-labelledby="experiment-tab-collection">
      <section class="kt-card kt-published-task-table" aria-labelledby="collection-status-title">
        <div class="kt-section-heading"><div><p class="kt-kicker">采集状态</p><h2 id="collection-status-title">班级任务进度</h2></div><span>{{ tasks.length }} 个</span></div>
        <EmptyState v-if="!tasks.length" title="暂无采集任务"><p>发布到班级后，服务端会在这里返回各任务的采集进度。</p></EmptyState>
        <div v-else class="kt-table-scroll"><table class="kt-table"><thead><tr><th>班级</th><th>状态</th><th>参与者</th><th>未开始</th><th>进行中</th><th>已完成</th></tr></thead><tbody><tr v-for="task in tasks" :key="task.id"><td>{{ task.class_name }}</td><td>{{ task.business_status }}</td><td>{{ progress[task.id]?.total_participants ?? task.participant_count }}</td><td>{{ progress[task.id]?.not_started ?? '—' }}</td><td>{{ progress[task.id]?.in_progress ?? '—' }}</td><td>{{ progress[task.id]?.completed ?? '—' }}</td></tr></tbody></table></div>
      </section>
    </section>
  </template>
  <Alert v-else tone="danger">{{ error || '实验不存在或无法访问。' }}</Alert>
</template>
