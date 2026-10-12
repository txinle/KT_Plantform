<script setup lang="ts">
import { RouterLink, RouterView } from 'vue-router'

import { getBootstrapContext } from '@/app/bootstrap'
import WorkspaceShell from '@/design-system/components/WorkspaceShell.vue'

import './teacher-experiments.css'

const bootstrap = getBootstrapContext()
</script>

<template>
  <div class="kt-teacher-experiments">
    <WorkspaceShell
      :user-name="bootstrap.user.display_name"
      :user-meta="bootstrap.user.username"
      :csrf-token="bootstrap.csrf_token"
      course-name="独立实验"
      course-meta="教师研究工作区"
      main-id="standalone-experiment-main"
    >
      <template #navigation>
        <a href="/teacher/">教师工作台</a>
        <RouterLink to="/teacher/experiments/" aria-current="page">实验</RouterLink>
      </template>
      <RouterView />
    </WorkspaceShell>
  </div>
</template>
