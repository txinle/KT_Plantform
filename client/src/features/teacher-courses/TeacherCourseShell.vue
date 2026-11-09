<script setup lang="ts">
import { RouterLink } from 'vue-router'
import { getBootstrapContext } from '@/app/bootstrap'
import WorkspaceShell from '@/design-system/components/WorkspaceShell.vue'
import type { TeacherCourse, TeacherCourseSettingsCourse } from './api'
import './teacher-courses.css'

defineProps<{
  courseId: number
  course?: TeacherCourseSettingsCourse & Partial<Pick<TeacherCourse, 'banks_url' | 'classes_url' | 'experiments_url' | 'data_url' | 'settings_url'>>
  active: 'overview' | 'settings'
  mainId: string
}>()
const bootstrap = getBootstrapContext()
</script>

<template>
  <div class="kt-teacher-courses">
    <WorkspaceShell
      :user-name="bootstrap.user.display_name"
      :user-meta="bootstrap.user.username"
      :csrf-token="bootstrap.csrf_token"
      :course-name="course?.name || '课程'"
      :course-meta="course ? `${course.code} · ${course.term}` : undefined"
      :main-id="mainId"
    >
      <template #navigation>
        <a href="/teacher/courses/">我的课程</a>
        <RouterLink :to="`/teacher/courses/${courseId}/`" :aria-current="active === 'overview' ? 'page' : undefined">概览</RouterLink>
        <a :href="course?.banks_url || `/teacher/courses/${courseId}/banks/`">题库</a>
        <a :href="course?.classes_url || `/teacher/courses/${courseId}/classes/`">班级</a>
        <a :href="course?.experiments_url || `/teacher/courses/${courseId}/experiments/`">实验</a>
        <a :href="course?.data_url || `/teacher/courses/${courseId}/data/`">数据</a>
        <a :href="course?.settings_url || `/teacher/courses/${courseId}/settings/`" :aria-current="active === 'settings' ? 'page' : undefined">设置</a>
      </template>
      <slot />
    </WorkspaceShell>
  </div>
</template>
