<script setup lang="ts">
import { onMounted, ref } from 'vue'

import type { StudentCourse } from './api'
import { getStudentCourseApi } from './api'
import './student-courses.css'

const courses = ref<StudentCourse[]>([])
const loading = ref(true)
const error = ref('')
const courseStatusLabels: Record<string, string> = { active: '进行中', archived: '已归档' }

onMounted(async () => {
  try {
    courses.value = (await getStudentCourseApi().courses()).items
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '课程加载失败。'
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <section class="kt-student-dashboard kt-student-courses-page">
    <header class="kt-student-page-heading">
      <div>
        <p class="kt-student-page-heading__eyebrow">课程工作区</p>
        <h1>我的课程</h1>
        <p>选择课程后可查看作业和班级中心。</p>
      </div>
    </header>
    <p v-if="loading" role="status">正在加载课程…</p>
    <p v-else-if="error" class="kt-alert kt-alert--danger" role="alert">{{ error }}</p>
    <div v-else-if="courses.length" class="kt-student-course-grid">
      <article v-for="course in courses" :key="course.course_id" class="kt-card kt-student-course-card">
        <div class="kt-student-course-card__cover" aria-hidden="true">
          <span>{{ course.course_code }}</span>
        </div>
        <div class="kt-student-course-card__body">
          <div class="kt-student-course-card__meta">
            <span class="kt-status kt-status--neutral">{{ courseStatusLabels[course.course_status] || course.course_status }}</span>
            <span>{{ course.course_term }}</span>
          </div>
          <h2>{{ course.course_name }}</h2>
          <p>{{ course.class_name }}</p>
        </div>
        <a class="kt-button" :href="`/student/courses/${course.course_id}/`">进入课程</a>
      </article>
    </div>
    <div v-else class="kt-card kt-empty-state">
      <h2>暂无可进入课程</h2>
      <p>只有已为你的正式参与者快照发布任务的课程会显示在这里。</p>
    </div>
  </section>
</template>
