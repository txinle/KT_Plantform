<script setup lang="ts">
import { onMounted, ref } from 'vue'

import type { StudentCourse } from './api'
import { getStudentCourseApi } from './api'
import './student-courses.css'

const courses = ref<StudentCourse[]>([])
const loading = ref(true)
const error = ref('')

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
  <section class="kt-student-dashboard">
    <div class="kt-page-header">
      <div class="kt-page-header__copy">
        <p class="kt-kicker">Student workspace</p>
        <h1>我的课程</h1>
        <p>进入课程查看教师正式发布给你的实验任务与学习记录。</p>
      </div>
    </div>
    <p v-if="loading" role="status">正在加载课程…</p>
    <p v-else-if="error" class="kt-alert kt-alert--danger" role="alert">{{ error }}</p>
    <div v-else-if="courses.length" class="kt-student-course-grid">
      <article v-for="course in courses" :key="course.course_id" class="kt-card kt-student-course-card">
        <div>
          <span class="kt-status kt-status--neutral">{{ course.course_status }}</span>
          <h2>{{ course.course_name }}</h2>
          <p>{{ course.course_code }} · {{ course.course_term }}</p>
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
