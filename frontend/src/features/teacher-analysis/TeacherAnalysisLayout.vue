<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterView, useRoute } from 'vue-router'

import { createApiClient } from '@/api/client'
import type { components } from '@/api/generated/schema'
import { getBootstrapContext } from '@/app/bootstrap'
import WorkspaceShell from '@/design-system/components/WorkspaceShell.vue'

const route = useRoute()
const courseId = Number(route.params.courseId)
const classId = Number(route.params.classId)
const bootstrap = getBootstrapContext()
const course = ref<components['schemas']['CourseContext']>()

onMounted(async () => {
  course.value = await createApiClient({
    getCsrfToken: () => bootstrap.csrf_token,
  }).get<components['schemas']['CourseContext']>(`/api/v1/courses/${courseId}/context/`)
})
</script>

<template>
  <WorkspaceShell
    :user-name="bootstrap.user.display_name"
    :user-meta="bootstrap.user.username"
    :csrf-token="bootstrap.csrf_token"
    :course-name="course?.name || '课程'"
    :course-meta="course ? `${course.code} · ${course.term}` : undefined"
    main-id="teacher-analysis-main"
  >
    <template #navigation>
      <a href="/teacher/courses/">我的课程</a>
      <a :href="`/teacher/courses/${courseId}/`">概览</a>
      <a :href="`/teacher/courses/${courseId}/banks/`">题库</a>
      <a :href="`/teacher/courses/${courseId}/classes/${classId}/`" aria-current="page">班级</a>
      <a :href="`/teacher/courses/${courseId}/experiments/`">实验</a>
      <a :href="`/teacher/courses/${courseId}/data/`">数据</a>
      <a :href="`/teacher/courses/${courseId}/settings/`">设置</a>
    </template>
    <RouterView />
  </WorkspaceShell>
</template>
