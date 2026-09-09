<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'

import { createApiClient } from '@/api/client'
import type { components } from '@/api/generated/schema'
import { getBootstrapContext } from '@/app/bootstrap'
import SidebarSection from '@/design-system/components/SidebarSection.vue'
import WorkspaceShell from '@/design-system/components/WorkspaceShell.vue'

import { getTeacherDataCenterApi, routeQuery, type DataCenterScope } from './api'
import './teacher-data-center.css'

const route = useRoute()
const courseId = Number(route.params.courseId)
const bootstrap = getBootstrapContext()
const api = getTeacherDataCenterApi()
const course = ref<components['schemas']['CourseContext']>()
const scope = ref<DataCenterScope>()
function dataPath(suffix = '') {
  return `/teacher/courses/${courseId}/data/${suffix}`
}

const contextError = ref('')
const scopeSaving = ref(false)
const selectedExperiment = ref<number | ''>('')
const selectedClass = ref<number | ''>('')

function applyScopeSelection(value: DataCenterScope) {
  selectedExperiment.value = value.current.experiment_id ?? ''
  selectedClass.value = value.current.class_id ?? ''
}

async function saveScope() {
  contextError.value = ''
  scopeSaving.value = true
  try {
    const result = await api.updateScope(courseId, {
      experiment_id: selectedExperiment.value === '' ? null : Number(selectedExperiment.value),
      class_id: selectedClass.value === '' ? null : Number(selectedClass.value),
    })
    scope.value = result
    applyScopeSelection(result)
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
      <RouterLink :to="dataPath()" aria-current="page">数据</RouterLink>
      <a :href="`/teacher/courses/${courseId}/settings/`">设置</a>
    </template>
    <SidebarSection>
      <nav class="kt-data-nav kt-sidebar-secondary-nav" aria-label="数据中心导航">
        <RouterLink :to="dataPath()">概览</RouterLink>
        <a :href="dataPath('teaching/')">教学数据</a>
        <RouterLink :to="dataPath('exports/')">导出</RouterLink>
        <RouterLink :to="dataPath('quality/')">数据质量</RouterLink>
        <RouterLink :to="dataPath('exports/history/')">导出记录</RouterLink>
        <RouterLink :to="dataPath('advanced/')">学生作答预览</RouterLink>
      </nav>
    </SidebarSection>
    <section v-if="scope" class="kt-card kt-scope-bar" aria-label="当前数据范围">
      <div><span>范围</span><strong>{{ scope.current.display }}</strong><small>{{ scope.current.is_complete ? '已选择教学范围' : '未选择完整范围' }}</small></div>
      <details :open="!scope.current.is_complete"><summary>更改范围</summary><form @submit.prevent="saveScope"><label>实验<select v-model="selectedExperiment" name="experiment_id"><option value="">选择实验</option><option v-for="experiment in scope.experiments" :key="experiment.id" :value="experiment.id">{{ experiment.name }}</option></select></label><label>班级<select v-model="selectedClass" name="class_id"><option value="">选择班级</option><option v-for="item in scope.classes" :key="item.id" :value="item.id">{{ item.academic_year }} {{ item.name }}</option></select></label><button class="kt-button" type="submit" :disabled="scopeSaving">{{ scopeSaving ? '正在保存…' : '应用' }}</button></form></details>
    </section>
    <p v-if="contextError" class="kt-alert kt-alert--danger" role="alert">{{ contextError }}</p>
    <RouterView v-else />
  </WorkspaceShell>
</template>
