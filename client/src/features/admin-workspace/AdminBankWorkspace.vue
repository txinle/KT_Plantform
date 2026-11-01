<script setup lang="ts">
import { ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AdminDomainShell from './AdminDomainShell.vue'
import { getTeacherStandaloneQuestionBankApi, isStandaloneBankRedirect, type StandaloneBankDestination } from '@/features/teacher-bank-management/standalone-api'
import { useBankPageUrl } from '@/features/teacher-bank-management/bank-page-navigation'
import '@/features/teacher-question-banks/teacher-question-banks.css'
defineProps<{ active: 'bank' }>()
const route = useRoute(), router = useRouter(), pageUrl = useBankPageUrl()
const ready = ref(false), error = ref('')
watch(() => route.path, async () => {
  ready.value = false; error.value = ''
  const path = route.path
  if (!route.params.courseId) {
    const destination: StandaloneBankDestination = route.params.questionId ? 'question_editor' : path.endsWith('/kc-import/') ? 'kc_import' : path.endsWith('/import/') ? 'question_import' : 'detail'
    try {
      const result = await getTeacherStandaloneQuestionBankApi().detail(Number(route.params.bankId), { destination, ...(route.params.questionId ? { questionId: Number(route.params.questionId) } : {}) })
      if (route.path !== path) return
      if (isStandaloneBankRedirect(result)) { await router.replace(pageUrl(result.redirect_url)); return }
    } catch (caught) { if (route.path === path) error.value = (caught as Error).message; return }
  }
  ready.value = true
}, { immediate: true })
</script>
<template>
  <AdminDomainShell :active="active">
    <div class="kt-teacher-question-banks admin-bank-content">
      <p v-if="error" role="alert" class="error-block">{{ error }}</p>
      <RouterView v-else-if="ready" :key="route.path" />
      <p v-else role="status">正在读取题库…</p>
    </div>
  </AdminDomainShell>
</template>
<style scoped>
.admin-bank-content { min-height: 0; background: transparent; }
</style>
