<script setup lang="ts">
import { computed, onMounted, provide, ref } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'

import { createApiClient } from '@/api/client'
import type { components } from '@/api/generated/schema'
import { getBootstrapContext } from '@/app/bootstrap'
import Icon from '@/design-system/Icon.vue'

import './teacher-experiments.css'

const route = useRoute()
const courseId = Number(route.params.courseId)
const bootstrap = getBootstrapContext()
const course = ref<components['schemas']['CourseContext']>()
const userInitial = computed(() => Array.from(bootstrap.user.display_name || bootstrap.user.username || '用户')[0] || '用')

provide('teacher-experiments-course', course)

onMounted(async () => {
  course.value = await createApiClient({
    getCsrfToken: () => bootstrap.csrf_token,
  }).get<components['schemas']['CourseContext']>(`/api/v1/courses/${courseId}/context/`)
})
</script>

<template>
  <div class="kt-teacher-experiments">
    <a class="kt-skip-link" href="#experiment-main">跳到主要内容</a>
    <header class="kt-app-header kt-experiment-shell-header">
      <div class="kt-shell kt-experiment-shell-header__inner">
        <a class="kt-wordmark" href="/" aria-label="KT Data Lab 首页">
          <span class="kt-brand-mark" aria-hidden="true">KT</span>
          <span class="kt-wordmark__name">KT Data Lab</span>
        </a>
        <nav aria-label="全局导航" class="kt-global-nav kt-experiment-global-nav">
          <a href="/teacher/courses/">我的课程</a>
          <a :href="`/teacher/courses/${courseId}/`">概览</a>
          <a :href="`/teacher/courses/${courseId}/banks/`">题库</a>
          <a :href="`/teacher/courses/${courseId}/classes/`">班级</a>
          <RouterLink :to="`/teacher/courses/${courseId}/experiments/`" aria-current="page">实验</RouterLink>
          <a :href="`/teacher/courses/${courseId}/data/`">数据</a>
          <a :href="`/teacher/courses/${courseId}/settings/`">设置</a>
        </nav>
        <div class="kt-experiment-header-tools">
          <span class="kt-experiment-header-notification" aria-label="通知">
            <Icon name="bell" :size="20" />
          </span>
          <span class="kt-experiment-header-user" :aria-label="bootstrap.user.display_name">
            <span class="kt-experiment-header-user__initial" aria-hidden="true">{{ userInitial }}</span>
            <Icon name="chevron-down" :size="17" />
          </span>
        </div>
      </div>
    </header>
    <main id="experiment-main" class="kt-shell kt-page-main kt-experiment-shell-main" tabindex="-1">
      <RouterView />
    </main>
  </div>
</template>
