<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'

import { createApiClient } from '@/api/client'
import type { components } from '@/api/generated/schema'
import { getBootstrapContext } from '@/app/bootstrap'
import AppHeader from '@/design-system/components/AppHeader.vue'

import { getTeacherDataCenterApi, routeQuery, type DataCenterScope } from './api'
import './teacher-data-center.css'

const route = useRoute()
const courseId = Number(route.params.courseId)
const bootstrap = getBootstrapContext()
const api = getTeacherDataCenterApi()
const course = ref<components['schemas']['CourseContext']>()
const scope = ref<DataCenterScope>()
const currentPath = computed(() => route.path)

function dataPath(suffix = '') {
  return `/teacher/courses/${courseId}/data/${suffix}`
}

onMounted(async () => {
  const [courseContext, scopeContext] = await Promise.all([
    createApiClient({ getCsrfToken: () => bootstrap.csrf_token })
      .get<components['schemas']['CourseContext']>(`/api/v1/courses/${courseId}/context/`),
    api.scope(courseId, routeQuery(route.query)),
  ])
  course.value = courseContext
  scope.value = scopeContext
})
</script>

<template>
  <a class="kt-skip-link" href="#data-center-main">跳到主要内容</a>
  <AppHeader :user-name="bootstrap.user.display_name"><a href="/teacher/courses/">我的课程</a></AppHeader>
  <div class="kt-course-bar"><div class="kt-shell"><div class="kt-course-context"><strong>{{ course?.name || '课程' }}</strong><span v-if="course">{{ course.code }} · {{ course.term }}</span></div><nav aria-label="课程导航" class="kt-course-nav"><a :href="`/teacher/courses/${courseId}/`">概览</a><a :href="`/teacher/courses/${courseId}/banks/`">题库</a><a :href="`/teacher/courses/${courseId}/classes/`">班级</a><a :href="`/teacher/courses/${courseId}/experiments/`">实验</a><RouterLink :to="dataPath()" aria-current="page">数据</RouterLink></nav></div></div>
  <main id="data-center-main" class="kt-shell kt-page-main" tabindex="-1">
    <nav class="kt-data-nav" aria-label="数据中心导航">
      <RouterLink :to="dataPath()">概览</RouterLink>
      <a :href="dataPath('teaching/')">教学数据</a>
      <RouterLink :to="dataPath('exports/')">导出</RouterLink>
      <RouterLink :to="dataPath('quality/')">数据质量</RouterLink>
      <RouterLink :to="dataPath('exports/history/')">导出记录</RouterLink>
      <RouterLink :to="dataPath('advanced/')">高级数据工具</RouterLink>
    </nav>
    <section v-if="scope" class="kt-card kt-scope-bar" aria-label="当前数据范围">
      <div><span>范围</span><strong>{{ scope.current.display }}</strong><small>{{ scope.current.is_complete ? '已选择教学范围' : '未选择完整范围' }}</small></div>
      <details :open="!scope.current.is_complete"><summary>更改范围</summary><form method="post" :action="scope.scope_update_url"><input type="hidden" name="csrfmiddlewaretoken" :value="bootstrap.csrf_token"><input type="hidden" name="next" :value="currentPath"><label>实验<select name="experiment_id" :value="scope.current.experiment_id ?? ''"><option value="">选择实验</option><option v-for="experiment in scope.experiments" :key="experiment.id" :value="experiment.id">{{ experiment.name }}</option></select></label><label>班级<select name="class_id" :value="scope.current.class_id ?? ''"><option value="">选择班级</option><option v-for="item in scope.classes" :key="item.id" :value="item.id">{{ item.academic_year }} {{ item.name }}</option></select></label><button class="kt-button" type="submit">应用</button></form></details>
    </section>
    <RouterView />
  </main>
</template>
