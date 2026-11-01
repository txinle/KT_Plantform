<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import SiteWorkspaceShell from '@/features/management-shared/SiteWorkspaceShell.vue'
import { getAdminApi } from './api'
defineProps<{ active: 'bank' | 'data' | 'course' }>()
const route = useRoute(), allowed = ref(false), error = ref('')
onMounted(async () => {
  try { allowed.value = (await getAdminApi().get<{ allowed: boolean }>('access/')).allowed }
  catch (caught) { error.value = caught instanceof Error ? caught.message : '无法进入管理员工作区。' }
})
</script>
<template>
  <SiteWorkspaceShell mode="admin" :active="active">
    <p v-if="error" class="error-block" role="alert">{{ error }}</p>
    <p v-else-if="!allowed" role="status">正在核验管理员权限…</p>
    <div v-else class="admin-domain-content"><slot><RouterView :key="route.path" /></slot></div>
  </SiteWorkspaceShell>
</template>
<style scoped>
.admin-domain-content :deep(.kt-button) { color: white; background: var(--kt-brand-900); border-color: var(--kt-brand-900); }
.admin-domain-content :deep(.kt-button:hover) { background: var(--kt-brand-800); }
.admin-domain-content :deep(.kt-button--secondary) { color: var(--kt-ink); background: var(--kt-surface); border-color: #d4ddeb; }
.admin-domain-content :deep(.kt-button--secondary:hover) { background: #f0f4fa; }
.admin-domain-content :deep(.kt-button--danger) { background: var(--kt-color-danger); border-color: var(--kt-color-danger); }
</style>
