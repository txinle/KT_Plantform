<script setup lang="ts">
import { onMounted, provide, ref } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'

import { createApiClient } from '@/api/client'
import type { components } from '@/api/generated/schema'
import { getBootstrapContext } from '@/app/bootstrap'
import AppHeader from '@/design-system/components/AppHeader.vue'

import './teacher-experiments.css'

const route = useRoute()
const courseId = Number(route.params.courseId)
const bootstrap = getBootstrapContext()
const course = ref<components['schemas']['CourseContext']>()

provide('teacher-experiments-course', course)

onMounted(async () => {
  course.value = await createApiClient({
    getCsrfToken: () => bootstrap.csrf_token,
  }).get<components['schemas']['CourseContext']>(`/api/v1/courses/${courseId}/context/`)
})
</script>

<template>
  <div class="kt-teacher-experiments">
    <a class="kt-skip-link" href="#experiment-main">跳到主要内容</a>
    <AppHeader
      :user-name="bootstrap.user.display_name"
      :user-meta="bootstrap.user.username"
      :csrf-token="bootstrap.csrf_token"
      show-logout
    >
      <a href="/teacher/courses/">我的课程</a>
      <a :href="`/teacher/courses/${courseId}/`">概览</a>
      <a :href="`/teacher/courses/${courseId}/banks/`">题库</a>
      <a :href="`/teacher/courses/${courseId}/classes/`">班级</a>
      <RouterLink :to="`/teacher/courses/${courseId}/experiments/`" aria-current="page">实验</RouterLink>
      <a :href="`/teacher/courses/${courseId}/data/`">数据</a>
      <a :href="`/teacher/courses/${courseId}/settings/`">设置</a>
    </AppHeader>
    <div class="kt-course-bar">
      <div class="kt-shell">
        <div class="kt-course-context">
          <strong>{{ course?.name || '课程' }}</strong>
          <span v-if="course">{{ course.code }} · {{ course.term }}</span>
        </div>
      </div>
    </div>
    <main id="experiment-main" class="kt-shell kt-page-main kt-experiment-shell-main" tabindex="-1">
      <RouterView />
    </main>
  </div>
</template>
