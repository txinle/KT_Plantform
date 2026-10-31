<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import Alert from '@/design-system/components/Alert.vue'
import EmptyState from '@/design-system/components/EmptyState.vue'
import Drawer from '@/design-system/components/Drawer.vue'
import MobileCardList from '@/design-system/components/MobileCardList.vue'
import PageHeader from '@/design-system/components/PageHeader.vue'
import Paginator from '@/design-system/components/Paginator.vue'
import StatusBadge from '@/design-system/components/StatusBadge.vue'
import DeletionDialog from '@/features/teacher-deletion/DeletionDialog.vue'
import type { DeletionTarget } from '@/features/teacher-deletion/api'

import { getTeacherExperimentApi, type TeacherExperiment, type TeacherExperimentCollection } from './api'

const route = useRoute()
const router = useRouter()
const api = getTeacherExperimentApi()
const courseId = Number(route.params.courseId)
const collection = ref<TeacherExperimentCollection>()
const loading = ref(true)
const error = ref('')
const currentPage = computed(() => Number(route.query.page || 1))
const archivedView = computed(() => route.query.archived === '1')
const deletionTarget = ref<DeletionTarget>()
const archiveTarget = ref<TeacherExperiment>()
const archiving = ref(false)
const archiveError = ref('')
const restoringId = ref<number>()
const canManage = computed(() => collection.value?.capabilities.can_manage === true)

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
    collection.value = archived ? await api.list(courseId, page, true) : await api.list(courseId, page)
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '实验列表加载失败。'
  } finally {
    loading.value = false
  }
}

async function navigate(page: number, archived = archivedView.value) {
  await router.push({ query: { ...(page === 1 ? {} : { page: String(page) }), ...(archived ? { archived: '1' } : {}) } })
  await load(page, archived)
}

function requestArchive(experiment: TeacherExperiment) {
  archiveError.value = ''
  archiveTarget.value = experiment
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
    <template #kicker>教学实验</template>
    <p>管理实验定义、班级排期和发布后的任务进度。</p>
    <template #actions>
      <button class="kt-button kt-button--secondary" type="button" data-show-archived :disabled="loading" @click="navigate(1, !archivedView)">
        {{ archivedView ? '返回当前实验' : '显示已归档' }}
      </button>
      <RouterLink class="kt-button" :to="`/teacher/courses/${courseId}/experiments/create/`">
        创建实验
      </RouterLink>
    </template>
  </PageHeader>

  <Alert v-if="error" tone="danger">{{ error }}</Alert>
  <p v-if="loading" role="status">正在加载实验…</p>
  <EmptyState v-else-if="!collection?.items.length" :title="archivedView ? '暂无归档实验' : '暂无当前实验'">
    <p>{{ archivedView ? '归档后的实验及数据可以在这里查看。' : '创建实验后，再选择班级和排期进行发布。' }}</p>
  </EmptyState>
  <section v-else class="kt-card kt-table-card" aria-labelledby="experiment-list-title">
    <div class="kt-section-heading">
      <h2 id="experiment-list-title">{{ archivedView ? '已归档实验' : '当前实验' }}</h2>
      <span>{{ collection.page.total_items }} 个</span>
    </div>
    <div class="kt-table-scroll kt-desktop-only">
      <table class="kt-table kt-experiment-list-table">
        <thead><tr><th>实验</th><th>开始时间</th><th>截止时间</th><th>实验限时</th><th>状态</th><th v-if="canManage">操作</th></tr></thead>
        <tbody>
          <tr v-for="experiment in collection.items" :key="experiment.id">
            <td>
              <RouterLink :to="`/teacher/courses/${courseId}/experiments/${experiment.id}/`">
                <strong>{{ experiment.name }}</strong>
              </RouterLink>
              <a v-if="experiment.activity?.grading_url" data-pending-grading class="kt-experiment-pending" :href="experiment.activity.grading_url">待批改：{{ experiment.activity.pending_grading_count }}</a>
              <span v-else class="kt-cell-note">待批改：{{ experiment.activity?.pending_grading_count ?? 0 }}</span>
              <span class="kt-cell-note">{{ experiment.description || '无说明' }}</span>
              <span class="kt-cell-note">{{ experiment.question_banks?.map((bank) => bank.name).join('、') || experiment.question_bank?.name || '未选择题库' }} · {{ experiment.sampling_mode_label || experiment.selection_mode_label }}</span>
              <span class="kt-cell-note">更新 {{ new Date(experiment.updated_at).toLocaleString('zh-CN') }}</span>
            </td>
            <td><div v-for="schedule in experiment.activity?.schedules || []" :key="schedule.task_id" class="kt-experiment-schedule-cell"><span>{{ displayDate(schedule.opens_at, '立即开放') }}</span><small>{{ schedule.class_name }}</small></div><span v-if="!experiment.activity?.schedules.length">未安排</span></td>
            <td><div v-for="schedule in experiment.activity?.schedules || []" :key="schedule.task_id" class="kt-experiment-schedule-cell"><span>{{ displayDate(schedule.closes_at, '无截止') }}</span><small>{{ schedule.class_name }}</small></div><span v-if="!experiment.activity?.schedules.length">未安排</span></td>
            <td><div v-for="schedule in experiment.activity?.schedules || []" :key="schedule.task_id" class="kt-experiment-schedule-cell"><span>{{ displayLimit(schedule.total_time_limit_minutes, schedule.per_question_time_limit_seconds) }}</span><small>{{ schedule.class_name }}</small></div><span v-if="!experiment.activity?.schedules.length">{{ displayLimit(experiment.total_time_limit_minutes, experiment.per_question_time_limit_seconds) }}</span></td>
            <td><StatusBadge :tone="statusTone(experiment.status)" :label="experiment.status_label" /></td>
            <td v-if="canManage">
              <div class="kt-inline-actions">
                <button v-if="experiment.can_restore" class="kt-text-button" type="button" :data-restore-experiment="experiment.id" :disabled="restoringId !== undefined" @click="restore(experiment)">{{ restoringId === experiment.id ? '恢复中…' : '恢复' }}</button>
                <button v-if="experiment.can_archive" class="kt-text-button" type="button" :data-archive-experiment="experiment.id" @click="requestArchive(experiment)">归档</button>
                <button class="kt-text-button kt-text-button--danger" type="button" :data-delete-experiment="experiment.id" @click="deletionTarget = { kind: 'experiment', experimentId: experiment.id }">删除</button>
              </div>
            </td>
          </tr>
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
          <button v-if="experiment.can_restore" class="kt-text-button" type="button" :data-restore-experiment="experiment.id" :disabled="restoringId !== undefined" @click="restore(experiment)">{{ restoringId === experiment.id ? '恢复中…' : '恢复实验' }}</button>
          <button v-if="experiment.can_archive" class="kt-text-button" type="button" :data-archive-experiment="experiment.id" @click="requestArchive(experiment)">归档实验</button>
          <button class="kt-text-button kt-text-button--danger" type="button" :data-delete-experiment="experiment.id" @click="deletionTarget = { kind: 'experiment', experimentId: experiment.id }">删除实验</button>
        </div>
      </li>
    </MobileCardList>
    <Paginator
      :page="collection.page.number"
      :total-pages="collection.page.total_pages"
      @navigate="navigate"
    />
  </section>
  <DeletionDialog v-if="deletionTarget" :course-id="courseId" :target="deletionTarget" @cancelled="deletionTarget = undefined" @completed="afterDeletion" />
  <Drawer :open="!!archiveTarget" title="归档实验" @close="!archiving && (archiveTarget = undefined)">
    <h2>归档“{{ archiveTarget?.name }}”</h2>
    <p>归档后从当前实验列表移除，保留已有作答和研究数据。可通过“显示已归档”查看。</p>
    <Alert v-if="archiveError" tone="danger">{{ archiveError }}</Alert>
    <div class="kt-inline-actions">
      <button class="kt-button" type="button" data-archive-confirm :disabled="archiving" @click="confirmArchive">{{ archiving ? '正在归档…' : '确认归档' }}</button>
      <button class="kt-button kt-button--secondary" type="button" :disabled="archiving" @click="archiveTarget = undefined">取消</button>
    </div>
  </Drawer>
</template>
