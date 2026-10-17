<script setup lang="ts">
import { computed, onBeforeUnmount, provide, ref, shallowRef, watch } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'

import { getAuthenticationGeneration, getBootstrapContext } from '@/app/bootstrap'
import { canNavigateStudentCourse } from '@/app/student-navigation'
import { currentAssessmentAction, finishAssessmentOperation, timedAssessmentStage } from '@/assessment-runtime/operation-timing'
import WorkspaceShell from '@/design-system/components/WorkspaceShell.vue'

import type { StudentTaskCollection } from './api'
import { getStudentCourseApi } from './api'
import { studentTaskContext } from './task-context'
import './student-courses.css'

const route = useRoute()
const bootstrap = computed(() => {
  getAuthenticationGeneration()
  try { return getBootstrapContext() } catch { return undefined }
})
const courseId = computed(() => Number(route.params.courseId))
const response = shallowRef<StudentTaskCollection>()
const course = computed(() => response.value?.course)
const loading = ref(false)
const isFeedbackRoute = computed(() => route.name === 'student-result' || route.name === 'student-learning-question')

const contextError = ref('')
let requestSequence = 0

async function reload() {
  const sequence = ++requestSequence
  const requestedCourse = courseId.value
  const generation = getAuthenticationGeneration()
  response.value = undefined
  contextError.value = ''
  loading.value = false
  if (!bootstrap.value || !requestedCourse || isFeedbackRoute.value) return
  loading.value = true
  try {
    const result = await timedAssessmentStage('task_list_read', () => getStudentCourseApi().tasks(requestedCourse))
    if (sequence === requestSequence && generation === getAuthenticationGeneration()) response.value = result
  } catch (caught) {
    if (sequence === requestSequence && generation === getAuthenticationGeneration()) {
      contextError.value = caught instanceof Error ? caught.message : '课程信息加载失败，请刷新重试。'
    }
  } finally {
    if (sequence === requestSequence) {
      loading.value = false
      if (generation === getAuthenticationGeneration() && currentAssessmentAction() === 'login') finishAssessmentOperation(!contextError.value)
    }
  }
}
provide(studentTaskContext, { response, loading, reload })
watch([courseId, () => route.name, getAuthenticationGeneration], reload, { immediate: true })
onBeforeUnmount(() => { requestSequence += 1 })
</script>

<template>
  <WorkspaceShell
    v-if="bootstrap"
    :user-name="bootstrap.user.display_name"
    :user-meta="bootstrap.user.student_number || bootstrap.user.username"
    :csrf-token="bootstrap.csrf_token"
    password-change-url="/password/change/"
    :initial-password-reminder-pending="bootstrap.user.initial_password_reminder_pending"
    :course-name="course?.course_name || (courseId ? '课程' : undefined)"
    :course-meta="course ? `${course.course_code} · ${course.course_term} · ${course.class_name}` : undefined"
    main-id="student-main"
  >
    <template #navigation>
      <RouterLink v-if="canNavigateStudentCourse(bootstrap, '/student/')" to="/student/" :aria-current="!courseId ? 'page' : undefined">我的课程</RouterLink>
      <a v-else href="/student/" :aria-current="!courseId ? 'page' : undefined">我的课程</a>
      <template v-if="courseId">
        <RouterLink :to="`/student/courses/${courseId}/`" exact-active-class="is-active">作业</RouterLink>
        <RouterLink :to="`/student/courses/${courseId}/learning/`" active-class="is-active">班级中心</RouterLink>
      </template>
    </template>
    <div v-if="contextError">
      <p class="kt-alert kt-alert--danger" role="alert">{{ contextError }}</p>
      <button class="kt-button" type="button" data-refresh-tasks @click="reload">重试</button>
    </div>
    <RouterView v-else />
  </WorkspaceShell>
</template>
