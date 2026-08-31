<script setup lang="ts">
import { RouterLink, RouterView, useRoute } from 'vue-router'
import { getBootstrapContext } from '@/app/bootstrap'
import AppHeader from '@/design-system/components/AppHeader.vue'

import './teacher-question-banks.css'

const route = useRoute()
const courseId = Number(route.params.courseId)
const bootstrap = getBootstrapContext()
</script>

<template>
  <div class="kt-teacher-question-banks">
    <a class="kt-skip-link" href="#question-bank-main">跳到主要内容</a>
    <AppHeader
      :user-name="bootstrap.user.display_name"
      :user-meta="bootstrap.user.username"
      :csrf-token="bootstrap.csrf_token"
      show-logout
    >
      <a href="/teacher/courses/">我的课程</a>
      <a :href="`/teacher/courses/${courseId}/`">概览</a>
      <RouterLink :to="`/teacher/courses/${courseId}/banks/`" aria-current="page">题库</RouterLink>
      <a :href="`/teacher/courses/${courseId}/classes/`">班级</a>
      <a :href="`/teacher/courses/${courseId}/experiments/`">实验</a>
      <a :href="`/teacher/courses/${courseId}/data/`">数据</a>
      <a :href="`/teacher/courses/${courseId}/settings/`">设置</a>
    </AppHeader>
    <main id="question-bank-main" class="kt-shell kt-page-main" tabindex="-1">
      <RouterView />
    </main>
  </div>
</template>
