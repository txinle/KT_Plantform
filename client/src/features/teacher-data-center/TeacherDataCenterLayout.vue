<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, provide, ref, watch } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'

import { createApiClient } from '@/api/client'
import type { components } from '@/api/generated/schema'
import { getBootstrapContext } from '@/app/bootstrap'
import SidebarSection from '@/design-system/components/SidebarSection.vue'
import WorkspaceShell from '@/design-system/components/WorkspaceShell.vue'

import { getTeacherDataCenterApi, routeQuery, type DataCenterScope } from './api'
import './teacher-data-center.css'

const route = useRoute()
const router = useRouter()
const courseId = Number(route.params.courseId)
const bootstrap = getBootstrapContext()
const api = getTeacherDataCenterApi()
const course = ref<components['schemas']['CourseContext']>()
const scope = ref<DataCenterScope>()
const isRouteRefreshPage = computed(() => route.path.endsWith('/advanced/') || route.path === dataPath())
const isAdvancedDetail = computed(() => route.path.endsWith('/advanced/') && route.query.view && route.query.view !== 'overview')
const isExportScopePage = computed(() => /\/data\/(?:export|exports|exports\/history)\/$/.test(route.path))
function dataPath(suffix = '') {
  return `/teacher/courses/${courseId}/data/${suffix}`
}

function dataLocation(suffix = '') {
  const query = routeQuery(route.query)
  delete query.table
  delete query.page
  delete query.package
  delete query.view
  delete query.question_ref
  delete query.visit
  const keepsExportScope = suffix === 'exports/' || suffix === 'exports/history/'
  if (!keepsExportScope) {
    delete query.scope_version
    delete query.class_mode
  }
  const keepsIndividualScope = suffix === 'exports/' || suffix === 'quality/' || suffix === 'exports/history/'
  if (!keepsIndividualScope) {
    delete query.student_id
    delete query.session_ref
  } else if (query.student_id === undefined || query.student_id === null || query.student_id === '') {
    delete query.session_ref
  }
  const isExportV2 = keepsExportScope && String(query.scope_version || '') === '2'
  if (isExportV2) {
    delete query.experiment_ids
    if (query.class_mode === 'all') delete query.class_id
  } else {
    if (query.experiment_ids === undefined || query.experiment_ids === null || query.experiment_ids === '') {
      query.experiment_ids = scope.value?.current.experiment_ids?.join(',')
    }
    if (query.class_id === undefined || query.class_id === null || query.class_id === '') {
      query.class_id = scope.value?.current.class_id
    }
  }
  return {
    path: dataPath(suffix),
    query,
  }
}

function sharedScopeQuery() {
  const query = routeQuery(route.query)
  delete query.scope_version
  delete query.class_mode
  return query
}

const contextError = ref('')
const scopeSaving = ref(false)
const scopeRevision = ref(0)
const sameScopeRefresh = ref(0)
provide('teacher-data-scope-refresh', sameScopeRefresh)
const selectedExperiments = ref<number[]>([])
const selectedClass = ref<number | ''>('')
const selectedExportExperiment = ref<number | null>(null)
const selectedExportClassRange = ref<number | 'all' | ''>('')
const experimentPicker = ref<HTMLDetailsElement>()
const selectedExperimentLabel = computed(() => {
  if (!selectedExperiments.value.length) return '选择实验'
  if (selectedExperiments.value.length > 1) return `已选 ${selectedExperiments.value.length} 个实验`
  return scope.value?.experiments.find(item => item.id === selectedExperiments.value[0])?.name || '选择实验'
})
function closeExperimentPicker() {
  if (experimentPicker.value) experimentPicker.value.open = false
}
function onOutsidePicker(event: PointerEvent) {
  if (event.target instanceof Node && !experimentPicker.value?.contains(event.target)) closeExperimentPicker()
}
const exportScopeComplete = computed(() => Boolean(
  selectedExportExperiment.value && selectedExportClassRange.value !== '',
))

function applyScopeSelection(value: DataCenterScope) {
  selectedExperiments.value = value.current.experiment_ids?.length
    ? [...value.current.experiment_ids]
    : value.current.experiment_id ? [value.current.experiment_id] : []
  selectedClass.value = value.current.class_id ?? ''
  if (isExportScopePage.value) {
    selectedExportExperiment.value = Number(route.query.experiment_id || value.current.experiment_id || value.experiments[0]?.id || 0) || null
    selectedExportClassRange.value = route.query.class_mode === 'all'
      ? 'all'
      : Number(route.query.class_id || value.current.class_id || value.classes[0]?.id || 0) || ''
  }
}

watch(isExportScopePage, exportPage => {
  if (exportPage && scope.value) applyScopeSelection(scope.value)
})

async function saveScope() {
  closeExperimentPicker()
  contextError.value = ''
  if (isExportScopePage.value) {
    if (!exportScopeComplete.value) {
      contextError.value = '请选择一个实验和班级范围。'
      return
    }
    await router.replace({
      query: {
        scope_version: '2',
        experiment_id: selectedExportExperiment.value ?? undefined,
        class_mode: selectedExportClassRange.value === 'all' ? 'all' : 'single',
        class_id: selectedExportClassRange.value === 'all' ? undefined : selectedExportClassRange.value,
      },
    })
    scopeRevision.value += 1
    return
  }
  scopeSaving.value = true
  try {
    const result = await api.updateScope(courseId, {
      experiment_ids: selectedExperiments.value.map(Number),
      class_id: selectedClass.value === '' ? null : Number(selectedClass.value),
    })
    scope.value = result
    applyScopeSelection(result)
    const previousQuery = route.query
    await router.replace({
      query: {
        experiment_ids: result.current.experiment_ids?.length ? result.current.experiment_ids.join(',') : undefined,
        class_id: result.current.class_id ?? undefined,
        view: route.path.endsWith('/advanced/') ? 'overview' : undefined,
      },
    })
    if (isRouteRefreshPage.value && route.query === previousQuery) sameScopeRefresh.value += 1
    scopeRevision.value += 1
  } catch (caught) {
    contextError.value = caught instanceof Error ? caught.message : '数据范围保存失败，请检查选择后重试。'
  } finally {
    scopeSaving.value = false
  }
}

onMounted(async () => {
  document.addEventListener('pointerdown', onOutsidePicker)
  try {
    const [courseContext, scopeContext] = await Promise.all([
      createApiClient({ getCsrfToken: () => bootstrap.csrf_token })
        .get<components['schemas']['CourseContext']>(`/api/v1/courses/${courseId}/context/`),
      api.scope(courseId, sharedScopeQuery()),
    ])
    applyScopeSelection(scopeContext)
    course.value = courseContext
    if (isExportScopePage.value && String(route.query.scope_version || '') !== '2') {
      await saveScope()
    }
    scope.value = scopeContext
  } catch (caught) {
    contextError.value = caught instanceof Error ? caught.message : '课程信息加载失败，请刷新重试。'
  }
})
onBeforeUnmount(() => document.removeEventListener('pointerdown', onOutsidePicker))
</script>

<template>
  <WorkspaceShell
    class="kt-data-center"
    :user-name="bootstrap.user.display_name"
    :user-meta="bootstrap.user.username"
    :csrf-token="bootstrap.csrf_token"
    :course-name="course?.name || '课程'"
    :course-meta="course ? `${course.code} · ${course.term}` : undefined"
    main-id="data-center-main"
  >
    <template #navigation>
      <RouterLink to="/teacher/courses/">我的课程</RouterLink>
      <RouterLink :to="`/teacher/courses/${courseId}/`">概览</RouterLink>
      <RouterLink :to="`/teacher/courses/${courseId}/banks/`">题库</RouterLink>
      <RouterLink :to="`/teacher/courses/${courseId}/classes/`">班级</RouterLink>
      <RouterLink :to="`/teacher/courses/${courseId}/experiments/`">实验</RouterLink>
      <RouterLink :to="dataLocation()" aria-current="page">数据</RouterLink>
      <RouterLink :to="`/teacher/courses/${courseId}/settings/`">设置</RouterLink>
    </template>
    <SidebarSection>
      <nav class="kt-data-nav kt-sidebar-secondary-nav" aria-label="数据中心导航">
        <RouterLink :to="dataLocation()">概览</RouterLink>
        <a :href="dataPath('teaching/')">教学数据</a>
        <RouterLink :to="dataLocation('advanced/')">研究数据预览</RouterLink>
        <RouterLink :to="dataLocation('exports/')">导出</RouterLink>
        <RouterLink :to="dataLocation('quality/')">数据质量</RouterLink>
        <RouterLink :to="dataLocation('exports/history/')">导出记录</RouterLink>
      </nav>
    </SidebarSection>
    <section v-if="scope && !isAdvancedDetail" class="kt-card kt-scope-bar" aria-label="当前数据范围">
      <div class="kt-scope-summary"><span>当前数据范围</span><strong>课程：{{ course?.code }} {{ course?.name }}</strong></div>
      <form class="kt-scope-controls" @submit.prevent="saveScope">
        <template v-if="isExportScopePage">
          <label>实验<select v-model="selectedExportExperiment" name="experiment_id" @change="selectedExportClassRange = 'all'"><option :value="null" disabled>选择实验</option><option v-for="experiment in scope.experiments" :key="experiment.id" :value="experiment.id">{{ experiment.name }}</option></select></label>
          <label>班级范围<select v-model="selectedExportClassRange" name="export_class_range"><option value="">选择班级范围</option><option value="all">全部已发布班级</option><option v-for="item in scope.classes" :key="item.id" :value="item.id">{{ item.academic_year }} {{ item.name }}</option></select></label>
        </template>
        <template v-else>
          <div class="kt-scope-experiment-select">
            <span id="scope-experiment-label">实验</span>
            <details ref="experimentPicker" class="kt-scope-picker" data-experiment-picker @keydown.esc.prevent="closeExperimentPicker">
              <summary aria-labelledby="scope-experiment-label"><span>{{ selectedExperimentLabel }}</span><svg width="18" height="18" viewBox="0 0 20 20" fill="none" stroke="currentColor" aria-hidden="true"><path d="m5 7.5 5 5 5-5" /></svg></summary>
              <fieldset class="kt-scope-experiments kt-scope-picker-panel">
                <legend class="kt-sr-only">选择实验，可多选</legend>
                <div class="kt-scope-experiment-options"><label v-for="experiment in scope.experiments" :key="experiment.id" class="kt-scope-experiment-choice" :class="{ 'is-selected': selectedExperiments.includes(experiment.id) }"><input v-model="selectedExperiments" name="experiment_ids" type="checkbox" :value="experiment.id"><span class="kt-scope-checkbox" aria-hidden="true">✓</span><span>{{ experiment.name }}</span></label></div>
                <div class="kt-scope-picker-footer"><small data-selected-experiment-count>已选择 {{ selectedExperiments.length }} 个</small><button type="button" class="kt-text-button" @click="closeExperimentPicker">完成选择</button></div>
              </fieldset>
            </details>
          </div>
          <label>班级<select v-model="selectedClass" name="class_id"><option value="">选择班级</option><option v-for="item in scope.classes" :key="item.id" :value="item.id">{{ item.academic_year }} {{ item.name }}</option></select></label>
        </template>
        <button class="kt-button" type="submit" :disabled="scopeSaving">{{ scopeSaving ? '正在保存…' : '应用' }}</button>
      </form>
    </section>
    <p v-if="contextError" class="kt-alert kt-alert--danger" role="alert">{{ contextError }}</p>
    <div v-else-if="scope || !isExportScopePage" class="kt-data-page" :data-scope-revision="scopeRevision"><RouterView :key="isRouteRefreshPage ? undefined : scopeRevision" /></div>
    <p v-else role="status">正在加载数据范围…</p>
  </WorkspaceShell>
</template>
