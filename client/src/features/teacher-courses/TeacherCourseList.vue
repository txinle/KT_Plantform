<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { getBootstrapContext } from '@/app/bootstrap'
import WorkspaceShell from '@/design-system/components/WorkspaceShell.vue'

import type { TeacherCourseCard } from './api'
import { getTeacherCourseApi } from './api'
import './teacher-courses.css'

const bootstrap = getBootstrapContext()
const courses = ref<TeacherCourseCard[]>([])
const createUrl = ref('/teacher/courses/create/')
const loading = ref(true)
const error = ref('')

onMounted(async () => {
  try {
    const response = await getTeacherCourseApi().list()
    courses.value = response.items
    createUrl.value = response.create_url
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '课程加载失败。'
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div class="kt-teacher-courses">
    <WorkspaceShell
      :user-name="bootstrap.user.display_name"
      :user-meta="bootstrap.user.username"
      :csrf-token="bootstrap.csrf_token"
      main-id="teacher-courses-main"
    >
      <template #navigation>
        <RouterLink to="/teacher/courses/" aria-current="page">我的课程</RouterLink>
      </template>
      <section class="kt-teacher-courses-page">
        <header class="kt-teacher-courses-heading">
          <div>
            <nav class="kt-course-list-breadcrumb" aria-label="层级目录"><span aria-current="page">我的课程</span></nav>
            <h1>我的课程</h1>
            <span>进入课程后管理题库、班级、实验与数据。</span>
          </div>
          <RouterLink class="kt-button" :to="createUrl">新建课程</RouterLink>
        </header>

        <p v-if="loading" role="status">正在加载课程…</p>
        <p v-else-if="error" class="kt-alert kt-alert--danger" role="alert">{{ error }}</p>
        <div v-else-if="courses.length" class="kt-teacher-course-grid">
          <article v-for="course in courses" :key="course.id" :data-course-card="course.id" class="kt-card kt-teacher-course-card">
            <span v-if="course.term" class="kt-teacher-course-card__term">{{ course.term }}</span>
            <span v-if="course.status !== 'active'" class="kt-status kt-status--neutral">{{ course.status_label }}</span>
            <h2>{{ course.name }}</h2>
            <p>{{ course.code }}<template v-if="course.description"> · {{ course.description }}</template></p>
            <RouterLink class="kt-button" :to="course.detail_url">进入课程 →</RouterLink>
          </article>
        </div>
        <section v-else class="kt-card kt-teacher-course-empty">
          <h2>尚未创建课程</h2>
          <p>创建课程后，再添加班级、题库和实验。</p>
          <RouterLink class="kt-button" :to="createUrl">创建第一门课程</RouterLink>
        </section>
      </section>
    </WorkspaceShell>
  </div>
</template>
