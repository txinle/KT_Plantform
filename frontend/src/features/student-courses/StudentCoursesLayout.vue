<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'

import { getBootstrapContext } from '@/app/bootstrap'
import WorkspaceShell from '@/design-system/components/WorkspaceShell.vue'

import type { StudentCourse } from './api'
import { getStudentCourseApi } from './api'
import './student-courses.css'

const route = useRoute()
const bootstrap = getBootstrapContext()
const courseId = computed(() => Number(route.params.courseId))
const course = ref<StudentCourse>()
const isFeedbackRoute = computed(() => route.name === 'student-result' || route.name === 'student-learning-question')

const contextError = ref('')

onMounted(async () => {
  try {
    if (!courseId.value || isFeedbackRoute.value) return
    const response = await getStudentCourseApi().tasks(courseId.value)
    course.value = response.course
  } catch (caught) {
    contextError.value = caught instanceof Error ? caught.message : '课程信息加载失败，请刷新重试。'
  }
})
</script>

<template>
  <WorkspaceShell
    :user-name="bootstrap.user.display_name"
    :user-meta="bootstrap.user.student_number || bootstrap.user.username"
    :csrf-token="bootstrap.csrf_token"
    :course-name="course?.course_name || (courseId ? '课程' : undefined)"
    :course-meta="course ? `${course.course_code} · ${course.course_term} · ${course.class_name}` : undefined"
    main-id="student-main"
  >
    <template #navigation>
      <a href="/student/" :aria-current="!courseId ? 'page' : undefined">我的课程</a>
      <template v-if="courseId">
        <RouterLink :to="`/student/courses/${courseId}/`" exact-active-class="is-active">作业</RouterLink>
        <RouterLink :to="`/student/courses/${courseId}/learning/`" active-class="is-active">班级中心</RouterLink>
      </template>
    </template>
    <p v-if="contextError" class="kt-alert kt-alert--danger" role="alert">{{ contextError }}</p>
    <RouterView v-else />
  </WorkspaceShell>
</template>
