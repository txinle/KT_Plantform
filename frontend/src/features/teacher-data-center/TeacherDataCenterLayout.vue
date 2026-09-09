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
  const keepsIndividualScope = suffix === 'exports/' || suffix === 'quality/' || suffix === 'exports/history/'
  if (!keepsIndividualScope) {
    delete query.student_id
    delete query.session_ref
  } else if (query.student_id === undefined || query.student_id === null || query.student_id === '') {
    delete query.session_ref
  }
  if (query.experiment_ids === undefined || query.experiment_ids === null || query.experiment_ids === '') {
    query.experiment_ids = scope.value?.current.experiment_ids?.join(',')
  }
  if (query.class_id === undefined || query.class_id === null || query.class_id === '') {
    query.class_id = scope.value?.current.class_id
  }
  return {
    path: dataPath(suffix),
    query,
  }
}

const contextError = ref('')
const scopeSaving = ref(false)
const scopeRevision = ref(0)
const selectedExperiments = ref<number[]>([])
const selectedClass = ref<number | ''>('')

function applyScopeSelection(value: DataCenterScope) {
  selectedExperiments.value = value.current.experiment_ids?.length
    ? [...value.current.experiment_ids]
    : value.current.experiment_id ? [value.current.experiment_id] : []
  selectedClass.value = value.current.class_id ?? ''
}

async function saveScope() {
  contextError.value = ''
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
      api.scope(courseId, routeQuery(route.query)),
    ])
    course.value = courseContext
    scope.value = scopeContext
    applyScopeSelection(scopeContext)
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
      <div class="kt-scope-summary"><span>研究数据范围</span><strong>课程：{{ course?.code }} {{ course?.name }}</strong><small>班级：{{ scopeClass ? `${scopeClass.academic_year} ${scopeClass.name}` : '未选择' }}</small><small>已选择实验：{{ scopeExperiments.length ? scopeExperiments.map(item => item.name).join('、') : '未选择' }}（{{ scopeExperiments.length }} 个实验）</small></div>
      <details :open="!scope.current.is_complete"><summary>更改范围</summary><form @submit.prevent="saveScope"><label>实验<select v-model="selectedExperiments" name="experiment_ids" multiple><option v-for="experiment in scope.experiments" :key="experiment.id" :value="experiment.id">{{ experiment.name }}</option></select><small>可选择多个实验</small></label><label>班级<select v-model="selectedClass" name="class_id"><option value="">选择班级</option><option v-for="item in scope.classes" :key="item.id" :value="item.id">{{ item.academic_year }} {{ item.name }}</option></select></label><button class="kt-button" type="submit" :disabled="scopeSaving">{{ scopeSaving ? '正在保存…' : '应用' }}</button></form></details>
    </section>
    <p v-if="contextError" class="kt-alert kt-alert--danger" role="alert">{{ contextError }}</p>
    <div v-else class="kt-data-page" :data-scope-revision="scopeRevision"><RouterView :key="scopeRevision" /></div>
  </WorkspaceShell>
</template>
