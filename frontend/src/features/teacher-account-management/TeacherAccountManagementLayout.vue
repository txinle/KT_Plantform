<script setup lang="ts">
import { RouterLink, RouterView } from 'vue-router'

import { getBootstrapContext } from '@/app/bootstrap'
import WorkspaceShell from '@/design-system/components/WorkspaceShell.vue'

const bootstrap = getBootstrapContext()
</script>

<template>
  <WorkspaceShell
    :user-name="bootstrap.user.display_name"
    :user-meta="bootstrap.user.username"
    :csrf-token="bootstrap.csrf_token"
    main-id="teacher-account-management-main"
  >
    <template #navigation>
      <a href="/teacher/courses/">我的课程</a>
      <RouterLink to="/teacher/accounts/classes/">班级管理</RouterLink>
      <RouterLink to="/teacher/accounts/students/">学生账号</RouterLink>
    </template>
    <RouterView />
  </WorkspaceShell>
</template>
