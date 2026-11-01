<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import { getBootstrapContext } from '@/app/bootstrap'
import WorkspaceShell from '@/design-system/components/WorkspaceShell.vue'

import type { TeacherCourseCard } from './api'
import { getTeacherCourseApi } from './api'
import './teacher-courses.css'

const route = useRoute()
const archived = computed(() => route.query.view === 'archived')
const pageTitle = computed(() => archived.value ? '课程归档' : '我的课程')
const bootstrap = getBootstrapContext()
const courses = ref<TeacherCourseCard[]>([])
const visibleCourses = computed(() => courses.value.filter(course => archived.value ? course.status === 'archived' : course.status !== 'archived'))
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
      <section class="kt-teacher-courses-page kt-teacher-course-directory">
        <header class="kt-teacher-courses-heading">
          <div>
            <nav v-if="archived" class="kt-course-list-breadcrumb" aria-label="层级目录"><RouterLink v-if="archived" to="/teacher/courses/">我的课程</RouterLink><span v-if="archived" aria-hidden="true"> / </span><span aria-current="page">{{ pageTitle }}</span></nav>
            <h1>{{ pageTitle }}</h1>
            <span>{{ archived ? '已归档课程集中保存在这里，可进入查看。' : '进入课程后管理题库、班级、实验与数据。' }}</span>
          </div>
          <div class="kt-teacher-courses-actions">
            <RouterLink class="kt-button kt-button--secondary" :to="archived ? '/teacher/courses/' : '/teacher/courses/?view=archived'">{{ archived ? '返回我的课程' : '课程归档' }}</RouterLink>
            <RouterLink v-if="!archived" class="kt-button" :to="createUrl">新建课程</RouterLink>
          </div>
        </header>

        <p v-if="loading" role="status">正在加载课程…</p>
        <p v-else-if="error" class="kt-alert kt-alert--danger" role="alert">{{ error }}</p>
        <div v-else-if="visibleCourses.length" class="kt-teacher-course-grid">
          <article v-for="course in visibleCourses" :key="course.id" :data-course-card="course.id" class="kt-teacher-course-card">
            <div class="kt-teacher-course-card__body">
              <div class="kt-teacher-course-card__title">
                <h2>{{ course.name }}</h2>
                <span v-if="course.status !== 'active'" class="kt-status kt-status--neutral">{{ course.status_label }}</span>
              </div>
              <p>{{ course.code }}<template v-if="course.description"> · {{ course.description }}</template></p>
            </div>
            <span v-if="course.term" class="kt-teacher-course-card__term">{{ course.term }}</span>
            <RouterLink class="kt-button kt-button--secondary" :to="course.detail_url">{{ archived ? '查看课程' : '进入课程' }} →</RouterLink>
          </article>
        </div>
        <section v-else class="kt-card kt-teacher-course-empty">
          <h2>{{ archived ? '暂无归档课程' : courses.length ? '暂无进行中的课程' : '尚未创建课程' }}</h2>
          <p>{{ archived ? '在课程设置中归档后，课程会显示在这里。' : courses.length ? '已有课程均已归档，可通过右上角“课程归档”查看。' : '创建课程后，再添加班级、题库和实验。' }}</p>
          <RouterLink v-if="!archived" class="kt-button" :to="createUrl">{{ courses.length ? '新建课程' : '创建第一门课程' }}</RouterLink>
          <RouterLink v-else class="kt-button kt-button--secondary" to="/teacher/courses/">返回我的课程</RouterLink>
        </section>
      </section>
    </WorkspaceShell>
  </div>
</template>
