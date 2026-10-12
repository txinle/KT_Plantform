<script setup lang="ts">
import { ref, watch } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'

import { getBootstrapContext } from '@/app/bootstrap'
import Alert from '@/design-system/components/Alert.vue'
import WorkspaceShell from '@/design-system/components/WorkspaceShell.vue'

import '@/features/teacher-question-banks/teacher-question-banks.css'
import {
  getTeacherStandaloneQuestionBankApi,
  isStandaloneBankRedirect,
  type StandaloneBankDestination,
} from './standalone-api'

const bootstrap = getBootstrapContext()
const route = useRoute()
const router = useRouter()
const api = getTeacherStandaloneQuestionBankApi()
const resolving = ref(true)
const resolutionError = ref('')

function destinationForRoute(): { destination: StandaloneBankDestination; questionId?: number } {
  if (route.name === 'teacher-standalone-question-import') {
    return { destination: 'question_import' }
  }
  if (route.name === 'teacher-standalone-kc-import') {
    return { destination: 'kc_import' }
  }
  if (route.name === 'teacher-standalone-question-editor') {
    return { destination: 'question_editor', questionId: Number(route.params.questionId) }
  }
  return { destination: 'detail' }
}

async function resolveBankRoute() {
  const bankId = Number(route.params.bankId)
  if (!Number.isInteger(bankId) || bankId <= 0) {
    resolving.value = false
    return
  }
  resolving.value = true
  resolutionError.value = ''
  try {
    const response = await api.detail(bankId, destinationForRoute())
    if (isStandaloneBankRedirect(response)) {
      await router.replace(response.redirect_url)
    }
  } catch (caught) {
    resolutionError.value = caught instanceof Error ? caught.message : '题库入口解析失败。'
  } finally {
    resolving.value = false
  }
}

watch(() => route.params.bankId, resolveBankRoute, { immediate: true })
</script>

<template>
  <div class="kt-teacher-question-banks">
    <WorkspaceShell
      :user-name="bootstrap.user.display_name"
      :user-meta="bootstrap.user.username"
      :csrf-token="bootstrap.csrf_token"
      main-id="question-bank-main"
    >
      <template #navigation>
        <a href="/teacher/">教师工作台</a>
        <RouterLink to="/teacher/banks/" aria-current="page">题库</RouterLink>
        <RouterLink to="/teacher/concepts/">知识点</RouterLink>
      </template>
      <p v-if="resolving" role="status">正在解析题库入口…</p>
      <Alert v-else-if="resolutionError" tone="danger">{{ resolutionError }}</Alert>
      <RouterView v-else />
    </WorkspaceShell>
  </div>
</template>
