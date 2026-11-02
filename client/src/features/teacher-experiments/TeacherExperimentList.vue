<script setup lang="ts">
import { useExperimentPageUrl } from './experiment-page-url'
const pageUrl = useExperimentPageUrl()
import { computed, inject, nextTick, onBeforeUnmount, onMounted, ref, watch, type Ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import type { components } from '@/api/generated/schema'
import ExperimentClassTaskTable from './ExperimentClassTaskTable.vue'
import Alert from '@/design-system/components/Alert.vue'
import EmptyState from '@/design-system/components/EmptyState.vue'
import ModalDialog from '@/design-system/components/ModalDialog.vue'
import Icon from '@/design-system/Icon.vue'
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
const search = ref('')
const searchInput = ref<HTMLInputElement>()
const searchBox = ref<HTMLElement>()
const searchResults = ref<TeacherExperimentCollection>()
const searchOpen = ref(false)
const searchLoading = ref(false)
const searchError = ref('')
const activeSearchIndex = ref(-1)
let searchRequest = 0
let searchTimer: ReturnType<typeof setTimeout> | undefined
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
const expandedProgressError = ref('')
let expansionRequest = 0
async function loadExpanded(experimentId: number) {
  const request = ++expansionRequest
  expandedLoading.value = true
  expandedError.value = ''
  expandedProgressError.value = ''
  expandedTasks.value = []
  expandedProgress.value = {}
  try {
    const result = await api.classTasks(experimentId)
    if (request !== expansionRequest) return
    expandedTasks.value = result.items
    expandedLoading.value = false
    await Promise.all(result.items.map(async task => {
      try {
        const row = await api.progress(task.id)
        if (request === expansionRequest) expandedProgress.value[task.id] = row
      } catch {
        if (request === expansionRequest) expandedProgressError.value = '部分作答进度加载失败，班级排期仍可查看和操作。'
      }
    }))
  } catch (caught) {
    if (request === expansionRequest) expandedError.value = caught instanceof Error ? caught.message : '班级排期加载失败。'
  } finally { if (request === expansionRequest) expandedLoading.value = false }
}
function closeSchedules() {
  expandedId.value = undefined
  ++expansionRequest
}

async function toggleSchedules(experiment: TeacherExperiment) {
  document.getElementById(`experiment-menu-${experiment.id}`)?.hidePopover?.()
  if (expandedId.value === experiment.id) { closeSchedules(); return }
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

let listRequest = 0
async function load(page = currentPage.value, archived = archivedView.value) {
  const request = ++listRequest
  loading.value = true
  error.value = ''
  try {
    const result = archived ? await api.list(courseId, page, true) : await api.list(courseId, page)
    if (request === listRequest) collection.value = result
  } catch (caught) {
    if (request === listRequest) error.value = caught instanceof Error ? caught.message : '实验列表加载失败。'
  } finally {
    if (request === listRequest) loading.value = false
  }
}

async function navigate(page: number, archived = archivedView.value) {
  closeSchedules()
  searchOpen.value = false
  await router.push({ query: { ...(page === 1 ? {} : { page: String(page) }), ...(archived ? { archived: '1' } : {}) } })
  await load(page, archived)
}

async function loadSearch(page = 1) {
  clearTimeout(searchTimer)
  const query = search.value.trim()
  if (!query) return
  const request = ++searchRequest
  searchLoading.value = true
  searchError.value = ''
  activeSearchIndex.value = -1
  try {
    const result = await api.list(courseId, page, archivedView.value, query)
    if (request === searchRequest) searchResults.value = result
  } catch (caught) {
    if (request === searchRequest) searchError.value = caught instanceof Error ? caught.message : '搜索暂时失败，请重试。'
  } finally {
    if (request === searchRequest) searchLoading.value = false
  }
}

watch([search, archivedView], () => {
  clearTimeout(searchTimer)
  ++searchRequest
  searchResults.value = undefined
  searchError.value = ''
  activeSearchIndex.value = -1
  searchLoading.value = Boolean(search.value.trim())
  searchOpen.value = Boolean(search.value.trim()) && document.activeElement === searchInput.value
  if (search.value.trim()) searchTimer = setTimeout(() => void loadSearch(), 250)
})

async function clearSearch() {
  search.value = ''
  searchOpen.value = false
  await nextTick()
  searchInput.value?.focus()
}

function closeSearchOnBlur(event: FocusEvent) {
  if (!searchBox.value?.contains(event.relatedTarget as Node | null)) searchOpen.value = false
}

function moveSearchSelection(step: number) {
  searchOpen.value = Boolean(search.value.trim())
  const count = searchLoading.value ? 0 : searchResults.value?.items.length || 0
  if (!count) return
  activeSearchIndex.value = activeSearchIndex.value < 0
    ? step > 0 ? 0 : count - 1
    : (activeSearchIndex.value + step + count) % count
  document.getElementById(`experiment-search-result-${activeSearchIndex.value}`)?.scrollIntoView({ block: 'nearest' })
}

async function submitSearch() {
  const selected = searchOpen.value && !searchLoading.value && !searchError.value
    ? searchResults.value?.items[activeSearchIndex.value] : undefined
  if (selected) {
    searchOpen.value = false
    await router.push(pageUrl(`/teacher/courses/${courseId}/experiments/${selected.id}/`)!)
  } else if (search.value.trim()) {
    searchOpen.value = true
    await loadSearch()
  }
}

onBeforeUnmount(() => { clearTimeout(searchTimer); ++searchRequest })

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
    await router.push(pageUrl(`/teacher/courses/${courseId}/experiments/${experiment.id}/publish-plan/${plan.id}/step/${plan.current_step || 1}/`)!)
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
  <header class="kt-experiment-cards-heading">
    <nav class="kt-experiment-breadcrumb" aria-label="层级目录"><RouterLink :to="pageUrl('/teacher/courses/')">我的课程</RouterLink><span>/</span><RouterLink :to="pageUrl(`/teacher/courses/${courseId}/`)">{{ course?.name || '课程' }}</RouterLink><span>/</span><span aria-current="page">实验</span></nav>
    <div><h1>实验</h1><span v-if="collection" aria-live="polite">{{ collection.page.total_items }} 个实验</span></div>
  </header>
  <form class="kt-experiment-list-toolbar" role="search" @submit.prevent="submitSearch">
    <div class="kt-experiment-list-filters">
      <div ref="searchBox" class="kt-experiment-search" @focusout="closeSearchOnBlur" @keydown.esc.stop.prevent="searchOpen = false">
        <div class="kt-experiment-search-field">
          <input ref="searchInput" v-model="search" type="search" role="combobox" autocomplete="off" aria-autocomplete="list" aria-label="搜索实验名称" placeholder="搜索实验名称" aria-controls="experiment-search-results" :aria-expanded="searchOpen" :aria-activedescendant="searchOpen && activeSearchIndex >= 0 ? `experiment-search-result-${activeSearchIndex}` : undefined" @focus="searchOpen = Boolean(search.trim())" @keydown.down.prevent="moveSearchSelection(1)" @keydown.up.prevent="moveSearchSelection(-1)">
          <button v-if="search" class="kt-experiment-search-clear" type="button" aria-label="清除搜索" title="清除搜索" @click="clearSearch"><svg width="14" height="14" viewBox="0 0 20 20" aria-hidden="true"><path d="m5 5 10 10M15 5 5 15" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" /></svg></button>
        </div>
        <div v-if="searchOpen" class="kt-experiment-search-preview">
          <p v-if="searchLoading" role="status">正在搜索…</p>
          <p v-else-if="searchError" role="alert">{{ searchError }}</p>
          <p v-else-if="!searchResults?.items.length" role="status">没有匹配的实验</p>
          <template v-else>
            <p class="kt-experiment-search-count">找到 {{ searchResults.page.total_items }} 个实验</p>
            <ul id="experiment-search-results" role="listbox" aria-label="匹配的实验">
              <li v-for="(experiment, index) in searchResults.items" :key="experiment.id" role="none">
                <RouterLink :id="`experiment-search-result-${index}`" :to="pageUrl(`/teacher/courses/${courseId}/experiments/${experiment.id}/`)" role="option" :aria-selected="activeSearchIndex === index" @click="searchOpen = false"><span>{{ experiment.name }}</span><StatusBadge :tone="statusTone(experiment.status)" :label="experiment.status_label" /></RouterLink>
              </li>
            </ul>
            <Paginator v-if="searchResults.page.total_pages > 1" :page="searchResults.page.number" :total-pages="searchResults.page.total_pages" @navigate="loadSearch" />
          </template>
        </div>
      </div>
      <button class="kt-visually-hidden" type="submit">搜索</button>
      <select aria-label="按归档状态筛选" data-show-archived :value="archivedView ? 'archived' : 'current'" :disabled="loading" @change="navigate(1, ($event.target as HTMLSelectElement).value === 'archived')">
        <option value="current">当前实验</option><option value="archived">已归档</option>
      </select>
    </div>
    <RouterLink v-if="canManage" class="kt-button" :to="pageUrl(`/teacher/courses/${courseId}/experiments/create/`)">＋ 创建实验</RouterLink>
  </form>
  <Alert v-if="error" tone="danger">{{ error }}</Alert>
  <p v-if="loading" role="status">正在加载实验…</p>
  <EmptyState v-else-if="!collection?.items.length" :title="archivedView ? '暂无归档实验' : '暂无当前实验'">
    <p>{{ archivedView ? '归档后的实验及数据可以在这里查看。' : '创建实验后，再选择班级和排期进行发布。' }}</p>
  </EmptyState>
  <section v-else aria-label="实验列表">
    <div class="kt-experiment-card-grid">
      <article v-for="experiment in collection.items" :key="experiment.id" class="kt-experiment-card" :aria-label="experiment.name">
        <div class="kt-experiment-card-head">
          <h2><RouterLink :to="pageUrl(`/teacher/courses/${courseId}/experiments/${experiment.id}/`)" :title="experiment.name">{{ experiment.name }}</RouterLink></h2>
          <StatusBadge :tone="statusTone(experiment.status)" :label="experiment.status_label" />
        </div>
        <div class="kt-experiment-card-foot">
          <button v-if="experiment.activity?.schedules.length" type="button" class="kt-experiment-card-classes" :aria-label="`${experiment.name}：查看 ${experiment.activity.schedules.length} 个班级`" :data-class-schedules="experiment.id" @click="toggleSchedules(experiment)"><Icon name="classes" :size="17" />{{ experiment.activity.schedules.length }} 个班级</button>
          <span v-else class="kt-experiment-card-classes kt-muted"><Icon name="classes" :size="17" />0 个班级</span>
          <template v-if="canManage">
            <button class="kt-button kt-button--secondary kt-experiment-card-actions" type="button" :popovertarget="`experiment-menu-${experiment.id}`" :aria-label="`${experiment.name}操作`" @click="positionActionMenu($event, experiment.id)">操作 <Icon name="chevron-down" :size="13" /></button>
                <div :id="`experiment-menu-${experiment.id}`" popover="auto" class="kt-experiment-row-menu">
                  <RouterLink :to="pageUrl(`/teacher/courses/${courseId}/experiments/${experiment.id}/`)">详情</RouterLink>
                  <RouterLink v-if="experiment.can_edit" :to="pageUrl(`/teacher/courses/${courseId}/experiments/${experiment.id}/edit/`)">编辑定义</RouterLink>
                  <button v-if="experiment.activity?.schedules.length" type="button" class="kt-text-button" :data-expand-schedules="experiment.id" :aria-expanded="expandedId === experiment.id" @click="toggleSchedules(experiment)">修改排期 / 延时</button>
                  <RouterLink v-if="experiment.student_preview_url" :to="pageUrl(experiment.student_preview_url)">模拟学生作答</RouterLink>
                  <a v-if="experiment.activity?.grading_url" :href="pageUrl(experiment.activity.grading_url)" data-pending-grading>人工批改（待批改：{{ experiment.activity.pending_grading_count }}）</a>
                  <button v-if="experiment.can_publish" class="kt-text-button" type="button" :data-publish-experiment="experiment.id" :disabled="publishingId !== undefined" @click="startPublish(experiment)">{{ publishingId === experiment.id ? '正在进入…' : '发布到班级' }}</button>
                  <button v-if="experiment.can_restore" class="kt-text-button" type="button" :data-restore-experiment="experiment.id" :disabled="restoringId !== undefined" @click="restore(experiment)">{{ restoringId === experiment.id ? '恢复中…' : '恢复' }}</button>
                  <button v-if="experiment.can_archive" class="kt-text-button" type="button" :data-archive-experiment="experiment.id" @click="requestArchive(experiment)">归档</button>
                  <button class="kt-text-button kt-text-button--danger" type="button" :data-delete-experiment="experiment.id" @click="deletionTarget = { kind: 'experiment', experimentId: experiment.id }">删除</button>
                </div>
          </template>
        </div>
      </article>
    </div>
    <Paginator :page="collection.page.number" :total-pages="collection.page.total_pages" @navigate="navigate" />
  </section>
  <ModalDialog v-if="expandedId !== undefined" :open="true" title="班级排期与操作" wide @close="closeSchedules">
    <section class="kt-experiment-inline-tasks" :data-inline-schedules="expandedId">
      <h3>{{ collection?.items.find(item => item.id === expandedId)?.name }}</h3>
      <p v-if="expandedLoading" role="status">正在加载班级排期…</p>
      <Alert v-if="expandedError" tone="danger">{{ expandedError }}</Alert>
      <Alert v-if="expandedProgressError" tone="warning">{{ expandedProgressError }}</Alert>
      <ExperimentClassTaskTable v-if="!expandedLoading && expandedTasks.length" :course-id="courseId" :experiment-id="expandedId" :tasks="expandedTasks" :progress="expandedProgress" @changed="refreshExpanded" />
      <p v-else-if="!expandedLoading && !expandedError">暂无班级排期。</p>
    </section>
    <template #footer><button class="kt-button kt-button--secondary" type="button" @click="closeSchedules">关闭</button></template>
  </ModalDialog>
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
