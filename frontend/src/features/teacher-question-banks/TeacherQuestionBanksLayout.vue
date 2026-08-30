<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'

import { createApiClient } from '@/api/client'
import type { components } from '@/api/generated/schema'
import { getBootstrapContext } from '@/app/bootstrap'
import AppHeader from '@/design-system/components/AppHeader.vue'

import './teacher-question-banks.css'

const route = useRoute()
const courseId = Number(route.params.courseId)
const bootstrap = getBootstrapContext()
const course = ref<components['schemas']['CourseContext']>()

onMounted(async () => {
  course.value = await createApiClient({
    getCsrfToken: () => bootstrap.csrf_token,
  }).get<components['schemas']['CourseContext']>(`/api/v1/courses/${courseId}/context/`)
})
</script>

<template>
  <a class="kt-skip-link" href="#question-bank-main">跳到主要内容</a>
  <AppHeader :user-name="bootstrap.user.display_name">
    <a href="/teacher/courses/">我的课程</a>
  </AppHeader>
  <div class="kt-course-bar">
    <div class="kt-shell">
      <div class="kt-course-context">
        <strong>{{ course?.name || '课程' }}</strong>
        <span v-if="course">{{ course.code }} · {{ course.term }}</span>
      </div>
      <nav aria-label="课程导航" class="kt-course-nav">
        <a :href="`/teacher/courses/${courseId}/`">概览</a>
        <RouterLink :to="`/teacher/courses/${courseId}/banks/`" aria-current="page">
          题库
        </RouterLink>
        <a :href="`/teacher/courses/${courseId}/classes/`">班级</a>
        <a :href="`/teacher/courses/${courseId}/experiments/`">实验</a>
        <a :href="`/teacher/courses/${courseId}/data/`">数据</a>
      </nav>
    </div>
  </div>
  <main id="question-bank-main" class="kt-shell kt-page-main" tabindex="-1">
    <RouterView />
  </main>
</template>
