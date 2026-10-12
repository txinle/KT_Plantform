<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { createApiClient } from '@/api/client'
import { getBootstrapContext } from '@/app/bootstrap'
import WorkspaceShell from '@/design-system/components/WorkspaceShell.vue'

interface Summary {
  session_id: number
  experiment_name: string
  status: string
  status_label: string
  submission_count: number
  attempt_count: number
  return_url: string
}
const route = useRoute()
const bootstrap = getBootstrapContext()
const summary = ref<Summary>()
const error = ref('')
onMounted(async () => {
  try { summary.value = await createApiClient().get<Summary>(`/api/v1/student/sessions/${Number(route.params.sessionId)}/summary/`) }
  catch (caught) { error.value = caught instanceof Error ? caught.message : '无法加载会话状态。' }
})
</script>

<template>
  <WorkspaceShell
    :user-name="bootstrap.user.display_name"
    :csrf-token="bootstrap.csrf_token"
    password-change-url="/password/change/"
    :initial-password-reminder-pending="bootstrap.user.initial_password_reminder_pending"
    main-id="session-summary"
  >
    <template #navigation><a href="/student/">我的课程</a></template>
    <p v-if="error" role="alert" class="kt-alert kt-alert--danger">{{ error }}</p>
    <section v-else-if="summary" class="kt-card kt-session-summary" aria-labelledby="session-title">
      <h1 id="session-title">{{ summary.experiment_name }}</h1>
      <p>当前状态：<strong>{{ summary.status_label }}</strong></p>
      <dl><div><dt>已正式提交</dt><dd>{{ summary.submission_count }}</dd></div><div><dt>分配题目</dt><dd>{{ summary.attempt_count }}</dd></div></dl>
      <a class="kt-button" :href="summary.return_url">返回作业列表</a>
    </section>
    <p v-else role="status">正在加载会话状态…</p>
  </WorkspaceShell>
</template>

<style scoped>
.kt-session-summary { max-width: 720px; padding: 24px; }
dl { display: flex; flex-wrap: wrap; gap: 32px; margin-block: 24px; }
dt { color: var(--kt-muted); }
dd { margin: 8px 0 0; font-size: 24px; font-weight: 650; font-variant-numeric: tabular-nums; }
</style>
