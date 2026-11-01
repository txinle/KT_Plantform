<script setup lang="ts">
import { computed, inject, onMounted, ref, type Ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import type { components } from '@/api/generated/schema'
import ExperimentClassTaskTable from './ExperimentClassTaskTable.vue'
import Alert from '@/design-system/components/Alert.vue'
import EmptyState from '@/design-system/components/EmptyState.vue'
import ModalDialog from '@/design-system/components/ModalDialog.vue'
import MobileCardList from '@/design-system/components/MobileCardList.vue'
import PageHeader from '@/design-system/components/PageHeader.vue'
import Paginator from '@/design-system/components/Paginator.vue'
import StatusBadge from '@/design-system/components/StatusBadge.vue'
import DeletionDialog from '@/features/teacher-deletion/DeletionDialog.vue'
import type { DeletionTarget } from '@/features/teacher-deletion/api'

import { getTeacherExperimentApi, type TeacherExperiment, type TeacherExperimentCollection, type ClassTask, type ClassTaskProgress } from './api'

const route = useRoute()
const router = useRouter()
const api = getTeacherExperimentApi()
const courseId = Number(route.params.courseId)
const course = inject<Ref<components['schemas']['CourseContext'] | undefined>>('teacher-experiments-course', ref())
const collection = ref<TeacherExperimentCollection>()
const loading = ref(true)
const error = ref('')
const currentPage = computed(() => Number(route.query.page || 1))
const archivedView = computed(() => route.query.archived === '1')
const search = ref(typeof route.query.q === 'string' ? route.query.q : '')
const deletionTarget = ref<DeletionTarget>()
const archiveTarget = ref<TeacherExperiment>()
const archiving = ref(false)
const archiveError = ref('')
const restoringId = ref<number>()
const publishingId = ref<number>()
const canManage = computed(() => collection.value?.capabilities.can_manage === true)

const expandedId = ref<number>()
const expandedTasks = ref<ClassTask[]>([])
const expandedProgress = ref<Record<number, ClassTaskProgress>>({})
const expandedLoading = ref(false)
const expandedError = ref('')
let expansionRequest = 0
async function loadExpanded(experimentId: number) {
  const request = ++expansionRequest
  expandedLoading.value = true
  expandedError.value = ''
  expandedTasks.value = []
  expandedProgress.value = {}
  try {
    const result = await api.classTasks(experimentId)
    if (request !== expansionRequest) return
    expandedTasks.value = result.items
    await Promise.all(result.items.map(async task => {
      const row = await api.progress(task.id)
      if (request === expansionRequest) expandedProgress.value[task.id] = row
    }))
  } catch (caught) {
    if (request === expansionRequest) expandedError.value = caught instanceof Error ? caught.message : '班级排期加载失败。'
  } finally { if (request === expansionRequest) expandedLoading.value = false }
}
async function toggleSchedules(experiment: TeacherExperiment) {
  document.getElementById(`experiment-menu-${experiment.id}`)?.hidePopover?.()
  if (expandedId.value === experiment.id) { expandedId.value = undefined; ++expansionRequest; return }
  expandedId.value = experiment.id
  await loadExpanded(experiment.id)
}
async function refreshExpanded() {
  if (expandedId.value === undefined) return
  await loadExpanded(expandedId.value)
  await load()
}

function statusTone(status: string) {
  if (status === 'draft') return 'warning' as const
  if (status === 'archived') return 'neutral' as const
  return 'success' as const
}

function displayDate(value: string | null, empty: string) {
  return value ? new Date(value).toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : empty
}

function displayLimit(total: number | null | undefined, item: number | null | undefined) {
  if (total != null) return `${total} 分钟`
  if (item != null) return `每题 ${item} 秒`
  return '不限时'
}

async function load(page = currentPage.value, archived = archivedView.value) {
  loading.value = true
  error.value = ''
  try {
    collection.value = search.value.trim()
      ? await api.list(courseId, page, archived, search.value.trim())
      : archived ? await api.list(courseId, page, true) : await api.list(courseId, page)
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '实验列表加载失败。'
  } finally {
    loading.value = false
  }
}

async function navigate(page: number, archived = archivedView.value) {
  await router.push({ query: { ...(page === 1 ? {} : { page: String(page) }), ...(archived ? { archived: '1' } : {}), ...(search.value.trim() ? { q: search.value.trim() } : {}) } })
  await load(page, archived)
}

function requestArchive(experiment: TeacherExperiment) {
  archiveError.value = ''
  archiveTarget.value = experiment
}

function positionActionMenu(event: MouseEvent, id: number) {
  const trigger = event.currentTarget as HTMLElement
  const menu = document.getElementById(`experiment-menu-${id}`)
  if (!menu) return
  const bounds = trigger.getBoundingClientRect()
  menu.style.left = `${Math.max(8, Math.min(bounds.left, window.innerWidth - 206))}px`
  menu.style.top = `${Math.max(8, Math.min(bounds.bottom + 6, window.innerHeight - 330))}px`
}

async function startPublish(experiment: TeacherExperiment) {
  if (publishingId.value !== undefined) return
  publishingId.value = experiment.id
  error.value = ''
  try {
    const plan = await api.startPlan(courseId, experiment.id)
    await router.push(`/teacher/courses/${courseId}/experiments/${experiment.id}/publish-plan/${plan.id}/step/${plan.current_step || 1}/`)
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '发布计划加载失败。'
  } finally { publishingId.value = undefined }
}

async function confirmArchive() {
  if (!archiveTarget.value || archiving.value) return
  archiving.value = true
  archiveError.value = ''
  try {
    await api.archive(courseId, archiveTarget.value.id)
    archiveTarget.value = undefined
    await navigate(1)
  } catch (caught) {
    archiveError.value = caught instanceof Error ? caught.message : '归档失败，请重试。'
  } finally { archiving.value = false }
}

async function afterDeletion() {
  deletionTarget.value = undefined
  const page = currentPage.value
  await load(page)
  if (!collection.value?.items.length && page > 1) {
    await navigate(Math.max(1, collection.value?.page.total_pages || page - 1))
  }
}

async function restore(experiment: TeacherExperiment) {
  if (restoringId.value !== undefined) return
  restoringId.value = experiment.id
  error.value = ''
  try {
    await api.restore(courseId, experiment.id)
    await navigate(1)
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '恢复失败，请重试。'
  } finally { restoringId.value = undefined }
}

onMounted(() => load())
</script>

<template>
  <PageHeader title="实验">
    <template #kicker><nav class="kt-experiment-breadcrumb" aria-label="层级目录"><a href="/teacher/courses/">我的课程</a><span>/</span><a :href="`/teacher/courses/${courseId}/`">{{ course?.name || '课程' }}</a><span>/</span><span aria-current="page">实验</span></nav></template>
    <p>按实验管理发布时间、作答时限、班级任务和人工批改。</p>
    <template #actions>
      <RouterLink class="kt-button" :to="`/teacher/courses/${courseId}/experiments/create/`">
        创建实验
      </RouterLink>
    </template>
  </PageHeader>

  <form class="kt-experiment-list-toolbar" role="search" @submit.prevent="navigate(1)"><input v-model="search" type="search" aria-label="搜索实验名称" placeholder="搜索实验名称"><button class="kt-visually-hidden" type="submit">搜索</button><label class="kt-experiment-archive-filter"><input type="checkbox" data-show-archived :checked="archivedView" :disabled="loading" @change="navigate(1, ($event.target as HTMLInputElement).checked)">查看已归档实验</label><span v-if="collection" class="kt-muted">{{ collection.page.total_items }} 个实验</span></form>

  <Alert v-if="error" tone="danger">{{ error }}</Alert>
  <p v-if="loading" role="status">正在加载实验…</p>
  <EmptyState v-else-if="!collection?.items.length" :title="archivedView ? '暂无归档实验' : '暂无当前实验'">
    <p>{{ archivedView ? '归档后的实验及数据可以在这里查看。' : '创建实验后，再选择班级和排期进行发布。' }}</p>
  </EmptyState>
  <section v-else class="kt-card kt-table-card" aria-label="实验列表">
    <div class="kt-table-scroll kt-desktop-only">
      <table class="kt-table kt-experiment-list-table">
        <thead><tr><th>实验</th><th>开始时间</th><th>截止时间</th><th>实验限时</th><th>状态</th><th>班级</th><th v-if="canManage">操作</th></tr></thead>
        <tbody>
          <template v-for="experiment in collection.items" :key="experiment.id">
            <tr>
              <td>
                <div class="kt-experiment-name-row"><RouterLink :to="`/teacher/courses/${courseId}/experiments/${experiment.id}/`"><strong>{{ experiment.name }}</strong></RouterLink><a v-if="experiment.activity?.grading_url" data-pending-grading class="kt-experiment-pending" :href="experiment.activity.grading_url">待批改：{{ experiment.activity.pending_grading_count }}</a><span v-else class="kt-experiment-pending">待批改：{{ experiment.activity?.pending_grading_count ?? 0 }}</span></div>
                <span class="kt-cell-note"><template v-if="experiment.sampling_total_count != null">{{ experiment.sampling_total_count }} 道题 · </template>更新 {{ displayDate(experiment.updated_at, '—') }}</span>
              </td>
              <td><div v-for="schedule in experiment.activity?.schedules || []" :key="schedule.task_id" class="kt-experiment-schedule-cell"><span>{{ displayDate(schedule.opens_at, '立即开放') }}</span><small v-if="(experiment.activity?.schedules.length || 0) > 1">{{ schedule.class_name }}</small></div><span v-if="!experiment.activity?.schedules.length">未安排</span></td>
              <td><div v-for="schedule in experiment.activity?.schedules || []" :key="schedule.task_id" class="kt-experiment-schedule-cell"><span>{{ displayDate(schedule.closes_at, '无截止') }}</span><small v-if="(experiment.activity?.schedules.length || 0) > 1">{{ schedule.class_name }}</small></div><span v-if="!experiment.activity?.schedules.length">未安排</span></td>
              <td><div v-for="schedule in experiment.activity?.schedules || []" :key="schedule.task_id" class="kt-experiment-schedule-cell"><span>{{ displayLimit(schedule.total_time_limit_minutes, schedule.per_question_time_limit_seconds) }}</span><small v-if="(experiment.activity?.schedules.length || 0) > 1">{{ schedule.class_name }}</small></div><span v-if="!experiment.activity?.schedules.length">{{ displayLimit(experiment.total_time_limit_minutes, experiment.per_question_time_limit_seconds) }}</span></td>
              <td><StatusBadge :tone="statusTone(experiment.status)" :label="experiment.status_label" /></td>
              <td><RouterLink :to="`/teacher/courses/${courseId}/experiments/${experiment.id}/?tab=tasks`">{{ experiment.activity?.schedules.length || 0 }} 个</RouterLink></td>
              <td v-if="canManage">
                <button class="kt-button kt-button--secondary" type="button" :popovertarget="`experiment-menu-${experiment.id}`" :aria-label="`${experiment.name}操作`" @click="positionActionMenu($event, experiment.id)">操作 ⌄</button>
                <div :id="`experiment-menu-${experiment.id}`" popover="auto" class="kt-experiment-row-menu">
                  <RouterLink :to="`/teacher/courses/${courseId}/experiments/${experiment.id}/`">详情</RouterLink>
                  <RouterLink v-if="experiment.can_edit" :to="`/teacher/courses/${courseId}/experiments/${experiment.id}/edit/`">编辑定义</RouterLink>
                  <button v-if="experiment.activity?.schedules.length" type="button" class="kt-text-button" :data-expand-schedules="experiment.id" :aria-expanded="expandedId === experiment.id" @click="toggleSchedules(experiment)">{{ expandedId === experiment.id ? '收起班级操作' : '修改排期 / 延时' }}</button>
                  <RouterLink v-if="experiment.student_preview_url" :to="experiment.student_preview_url">模拟学生作答</RouterLink>
                  <a v-if="experiment.activity?.grading_url" :href="experiment.activity.grading_url">人工批改</a>
                  <button v-if="experiment.can_publish" class="kt-text-button" type="button" :data-publish-experiment="experiment.id" :disabled="publishingId !== undefined" @click="startPublish(experiment)">{{ publishingId === experiment.id ? '正在进入…' : '发布到班级' }}</button>
                  <button v-if="experiment.can_restore" class="kt-text-button" type="button" :data-restore-experiment="experiment.id" :disabled="restoringId !== undefined" @click="restore(experiment)">{{ restoringId === experiment.id ? '恢复中…' : '恢复' }}</button>
                  <button v-if="experiment.can_archive" class="kt-text-button" type="button" :data-archive-experiment="experiment.id" @click="requestArchive(experiment)">归档</button>
                  <button class="kt-text-button kt-text-button--danger" type="button" :data-delete-experiment="experiment.id" @click="deletionTarget = { kind: 'experiment', experimentId: experiment.id }">删除</button>
                </div>
              </td>
            </tr><tr v-if="expandedId === experiment.id"><td :colspan="canManage ? 7 : 6"><section class="kt-experiment-inline-tasks" :data-inline-schedules="experiment.id"><div class="kt-section-heading"><h3>{{ experiment.name }} · 班级排期与操作</h3><button class="kt-text-button" type="button" @click="toggleSchedules(experiment)">收起</button></div><p v-if="expandedLoading" role="status">正在加载班级排期…</p><Alert v-if="expandedError" tone="danger">{{ expandedError }}</Alert><ExperimentClassTaskTable v-if="!expandedLoading && expandedTasks.length" :course-id="courseId" :experiment-id="experiment.id" :tasks="expandedTasks" :progress="expandedProgress" @changed="refreshExpanded" /></section></td></tr>
          </template>
        </tbody>
      </table>
    </div>
    <MobileCardList class="kt-mobile-only">
      <li v-for="experiment in collection.items" :key="experiment.id" class="kt-mobile-experiment-card">
        <div class="kt-section-heading">
          <RouterLink :to="`/teacher/courses/${courseId}/experiments/${experiment.id}/`">
            <strong>{{ experiment.name }}</strong>
          </RouterLink>
          <StatusBadge :tone="statusTone(experiment.status)" :label="experiment.status_label" />
        </div>
        <p>{{ experiment.description || '无说明' }}</p>
        <a v-if="experiment.activity?.grading_url" class="kt-experiment-pending" :href="experiment.activity.grading_url">待批改：{{ experiment.activity.pending_grading_count }}</a>
        <span v-else class="kt-cell-note">待批改：{{ experiment.activity?.pending_grading_count ?? 0 }}</span>
        <dl>
          <div><dt>题库</dt><dd>{{ experiment.question_banks?.map((bank) => bank.name).join('、') || experiment.question_bank?.name || '未选择题库' }}</dd></div>
          <div><dt>选题</dt><dd>{{ experiment.sampling_mode_label || experiment.selection_mode_label }}</dd></div>
          <div v-for="schedule in experiment.activity?.schedules || []" :key="schedule.task_id"><dt>{{ schedule.class_name }}</dt><dd>开始 {{ displayDate(schedule.opens_at, '立即开放') }}<br>截止 {{ displayDate(schedule.closes_at, '无截止') }}<br>实验限时 {{ displayLimit(schedule.total_time_limit_minutes, schedule.per_question_time_limit_seconds) }}</dd></div>
        </dl>
        <div v-if="canManage" class="kt-inline-actions">
          <button v-if="experiment.activity?.schedules.length" type="button" class="kt-text-button" :data-expand-schedules="experiment.id" :aria-expanded="expandedId === experiment.id" @click="toggleSchedules(experiment)">{{ expandedId === experiment.id ? '收起班级操作' : '修改排期 / 延时' }}</button>
          <RouterLink v-if="experiment.student_preview_url" class="kt-text-button" :to="experiment.student_preview_url">模拟学生作答</RouterLink>
          <button v-if="experiment.can_publish" class="kt-text-button" type="button" :disabled="publishingId !== undefined" @click="startPublish(experiment)">发布到班级</button>
          <button v-if="experiment.can_restore" class="kt-text-button" type="button" :data-restore-experiment="experiment.id" :disabled="restoringId !== undefined" @click="restore(experiment)">{{ restoringId === experiment.id ? '恢复中…' : '恢复实验' }}</button>
          <button v-if="experiment.can_archive" class="kt-text-button" type="button" :data-archive-experiment="experiment.id" @click="requestArchive(experiment)">归档实验</button>
          <button class="kt-text-button kt-text-button--danger" type="button" :data-delete-experiment="experiment.id" @click="deletionTarget = { kind: 'experiment', experimentId: experiment.id }">删除实验</button>
        </div>
        <template v-if="expandedId === experiment.id"><section class="kt-experiment-inline-tasks" :data-inline-schedules="experiment.id"><div class="kt-section-heading"><h3>{{ experiment.name }} · 班级排期与操作</h3><button class="kt-text-button" type="button" @click="toggleSchedules(experiment)">收起</button></div><p v-if="expandedLoading" role="status">正在加载班级排期…</p><Alert v-if="expandedError" tone="danger">{{ expandedError }}</Alert><ExperimentClassTaskTable v-if="!expandedLoading && expandedTasks.length" :course-id="courseId" :experiment-id="experiment.id" :tasks="expandedTasks" :progress="expandedProgress" @changed="refreshExpanded" /></section></template>
      </li>
    </MobileCardList>
    <Paginator
      :page="collection.page.number"
      :total-pages="collection.page.total_pages"
      @navigate="navigate"
    />
  </section>
  <DeletionDialog v-if="deletionTarget" :course-id="courseId" :target="deletionTarget" @cancelled="deletionTarget = undefined" @completed="afterDeletion" />
  <ModalDialog v-if="archiveTarget" :open="true" title="归档实验" @close="!archiving && (archiveTarget = undefined)">
    <h2>归档“{{ archiveTarget?.name }}”</h2>
    <p>归档后从当前实验列表移除，保留已有作答和研究数据。可通过“显示已归档”查看。</p>
    <Alert v-if="archiveError" tone="danger">{{ archiveError }}</Alert>
    <template #footer>
      <button class="kt-button" type="button" data-archive-confirm :disabled="archiving" @click="confirmArchive">{{ archiving ? '正在归档…' : '确认归档' }}</button>
      <button class="kt-button kt-button--secondary" type="button" :disabled="archiving" @click="archiveTarget = undefined">取消</button>
    </template>
  </ModalDialog>
</template>
