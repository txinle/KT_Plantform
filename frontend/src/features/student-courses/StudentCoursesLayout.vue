<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'

import { getBootstrapContext } from '@/app/bootstrap'
import AppHeader from '@/design-system/components/AppHeader.vue'

import type { StudentCourse } from './api'
import { getStudentCourseApi } from './api'
import './student-courses.css'

const route = useRoute()
const bootstrap = getBootstrapContext()
const courseId = computed(() => Number(route.params.courseId))
const course = ref<StudentCourse>()
const isFeedbackRoute = computed(() => route.name === 'student-result' || route.name === 'student-learning-question')

onMounted(async () => {
  if (!courseId.value || isFeedbackRoute.value) return
  const response = await getStudentCourseApi().tasks(courseId.value)
  course.value = response.course
})
</script>

<template>
  <a class="kt-skip-link" href="#student-main">跳到主要内容</a>
  <AppHeader
    class="kt-app-header--student"
    :class="{ 'kt-app-header--feedback': isFeedbackRoute }"
    :user-name="bootstrap.user.display_name"
    :user-meta="bootstrap.user.student_number || bootstrap.user.username"
    :show-logout="isFeedbackRoute"
    :csrf-token="bootstrap.csrf_token"
  >
    <a v-if="!isFeedbackRoute" href="/student/" aria-current="page">我的课程</a>
  </AppHeader>
  <div v-if="courseId && !isFeedbackRoute" class="kt-course-bar">
    <div class="kt-shell">
      <div class="kt-course-context">
        <strong>{{ course?.course_name || '课程' }}</strong>
        <span v-if="course">{{ course.course_code }} · {{ course.course_term }} · {{ course.class_name }}</span>
      </div>
      <nav aria-label="课程导航" class="kt-course-nav">
        <RouterLink :to="`/student/courses/${courseId}/`" exact-active-class="is-active">实验任务</RouterLink>
        <RouterLink :to="`/student/courses/${courseId}/learning/`" active-class="is-active">我的学习</RouterLink>
      </nav>
    </div>
  </div>
  <main id="student-main" class="kt-shell kt-page-main" :class="{ 'kt-feedback-main': isFeedbackRoute }" tabindex="-1">
    <RouterView />
  </main>
</template>
