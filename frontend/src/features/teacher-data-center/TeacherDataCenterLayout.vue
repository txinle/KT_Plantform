<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
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
const isAdvancedDetail = computed(() => route.path.endsWith('/advanced/') && route.query.view && route.query.view !== 'overview')
const isExportScopePage = computed(() => /\/data\/(?:export|exports|exports\/history)\/$/.test(route.path))
const scopeClass = computed(() => scope.value?.classes.find(item => item.id === scope.value?.current.class_id))
const scopeExperiments = computed(() => {
  const ids = new Set(scope.value?.current.experiment_ids || [])
  return scope.value?.experiments.filter(item => ids.has(item.id)) || []
})
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
const selectedExperiments = ref<number[]>([])
const selectedClass = ref<number | ''>('')
const selectedExportExperiment = ref<number | null>(null)
const selectedExportClassRange = ref<number | 'all' | ''>('')
const exportScopeComplete = computed(() => Boolean(
  selectedExportExperiment.value && selectedExportClassRange.value !== '',
))
const exportScopeClassLabel = computed(() => {
  if (selectedExportClassRange.value === 'all') return '全部已发布班级'
  const selected = scope.value?.classes.find(item => item.id === selectedExportClassRange.value)
  return selected ? `${selected.academic_year} ${selected.name}` : '未选择'
})

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

async function saveScope() {
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
    await router.replace({
      query: {
        experiment_ids: result.current.experiment_ids?.length ? result.current.experiment_ids.join(',') : undefined,
        class_id: result.current.class_id ?? undefined,
        view: route.path.endsWith('/advanced/') ? 'overview' : undefined,
      },
    })
    scopeRevision.value += 1
  } catch (caught) {
    contextError.value = caught instanceof Error ? caught.message : '数据范围保存失败，请检查选择后重试。'
  } finally {
    scopeSaving.value = false
  }
}

onMounted(async () => {
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
      <a href="/teacher/courses/">我的课程</a>
      <a :href="`/teacher/courses/${courseId}/`">概览</a>
      <a :href="`/teacher/courses/${courseId}/banks/`">题库</a>
      <a :href="`/teacher/courses/${courseId}/classes/`">班级</a>
      <a :href="`/teacher/courses/${courseId}/experiments/`">实验</a>
      <RouterLink :to="dataLocation()" aria-current="page">数据</RouterLink>
      <a :href="`/teacher/courses/${courseId}/settings/`">设置</a>
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
      <div class="kt-scope-summary"><span>研究数据范围</span><strong>课程：{{ course?.code }} {{ course?.name }}</strong><small>班级：{{ isExportScopePage ? exportScopeClassLabel : (scopeClass ? `${scopeClass.academic_year} ${scopeClass.name}` : '未选择') }}</small><small v-if="isExportScopePage">已选择实验：{{ scope.experiments.find(item => item.id === selectedExportExperiment)?.name || '未选择' }}（{{ selectedExportExperiment ? 1 : 0 }} 个实验）</small><small v-else>已选择实验：{{ scopeExperiments.length ? scopeExperiments.map(item => item.name).join('、') : '未选择' }}（{{ scopeExperiments.length }} 个实验）</small></div>
      <details :open="isExportScopePage ? !exportScopeComplete : !scope.current.is_complete">
        <summary>更改范围</summary><form @submit.prevent="saveScope">
          <template v-if="isExportScopePage">
            <fieldset class="kt-scope-experiments"><legend>实验 <span data-selected-experiment-count>已选择 {{ selectedExportExperiment ? 1 : 0 }} 个</span></legend><div class="kt-scope-experiment-options"><label v-for="experiment in scope.experiments" :key="experiment.id" class="kt-scope-experiment-choice" :class="{ 'is-selected': selectedExportExperiment === experiment.id }"><input v-model="selectedExportExperiment" name="experiment_id" type="radio" :value="experiment.id" @change="selectedExportClassRange = 'all'"><span class="kt-scope-checkbox" aria-hidden="true">✓</span><span>{{ experiment.name }}</span></label></div><small>标准数据包一次导出一个实验</small></fieldset>
            <label>班级范围<select v-model="selectedExportClassRange" name="export_class_range"><option value="">选择班级范围</option><option value="all">全部已发布班级</option><option v-for="item in scope.classes" :key="item.id" :value="item.id">{{ item.academic_year }} {{ item.name }}</option></select></label>
          </template>
          <template v-else>
            <fieldset class="kt-scope-experiments"><legend>实验 <span data-selected-experiment-count>已选择 {{ selectedExperiments.length }} 个</span></legend><div class="kt-scope-experiment-options"><label v-for="experiment in scope.experiments" :key="experiment.id" class="kt-scope-experiment-choice" :class="{ 'is-selected': selectedExperiments.includes(experiment.id) }"><input v-model="selectedExperiments" name="experiment_ids" type="checkbox" :value="experiment.id"><span class="kt-scope-checkbox" aria-hidden="true">✓</span><span>{{ experiment.name }}</span></label></div><small>可选择多个实验</small></fieldset><label>班级<select v-model="selectedClass" name="class_id"><option value="">选择班级</option><option v-for="item in scope.classes" :key="item.id" :value="item.id">{{ item.academic_year }} {{ item.name }}</option></select></label>
          </template>
          <button class="kt-button" type="submit" :disabled="scopeSaving">{{ scopeSaving ? '正在保存…' : '应用' }}</button>
        </form>
      </details>
    </section>
    <p v-if="contextError" class="kt-alert kt-alert--danger" role="alert">{{ contextError }}</p>
    <div v-else-if="scope" class="kt-data-page" :data-scope-revision="scopeRevision"><RouterView :key="scopeRevision" /></div>
    <p v-else role="status">正在加载数据范围…</p>
  </WorkspaceShell>
</template>
