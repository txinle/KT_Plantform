<script setup lang="ts">
import { onMounted, provide, ref } from 'vue'
import { RouterView, useRoute } from 'vue-router'
import AdminDomainShell from '@/features/admin-workspace/AdminDomainShell.vue'
import { createApiClient } from '@/api/client'
import type { components } from '@/api/generated/schema'
import { getBootstrapContext } from '@/app/bootstrap'
import { experimentPageUrl, experimentPageUrlKey, type PageUrl } from './experiment-page-url'
import './teacher-experiments.css'

const route = useRoute()
const bootstrap = getBootstrapContext()
const course = ref<components['schemas']['CourseContext']>()
const error = ref('')
provide('teacher-experiments-course', course)
provide(experimentPageUrlKey, ((url: string | undefined | null) => experimentPageUrl(url, true)) as PageUrl)
onMounted(async () => {
  if (!route.params.courseId) return
  try {
    course.value = await createApiClient({ getCsrfToken: () => bootstrap.csrf_token }).get(`/api/v1/courses/${route.params.courseId}/context/`)
  } catch (caught) { error.value = caught instanceof Error ? caught.message : '课程信息加载失败。' }
})
</script>

<template>
  <AdminDomainShell active="data">
    <div class="kt-teacher-experiments">
      <p v-if="error" role="alert" class="kt-alert kt-alert--danger">{{ error }}</p>
      <RouterView v-else :key="String(route.params.courseId || 'standalone')" />
    </div>
  </AdminDomainShell>
</template>
