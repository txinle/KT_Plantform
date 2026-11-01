<script setup lang="ts">
import { computed, getCurrentInstance, onMounted, ref } from 'vue'
import type { Router } from 'vue-router'
import { createApiClient } from '@/api/client'

const props = defineProps<{ courseName: string; csrfToken: string }>()
const router = getCurrentInstance()?.appContext.config.globalProperties.$router as Router | undefined
const path = computed(() => router?.currentRoute.value.path || window.location.pathname)
const currentId = computed(() => /^\/teacher\/courses\/(\d+)\//.exec(path.value)?.[1] || '')
const courses = ref<Array<{ id: number; name: string; detail_url: string }>>([])
const loading = ref(false)
const error = ref('')
const api = createApiClient({ getCsrfToken: () => props.csrfToken })

async function load() {
  if (!currentId.value) return
  loading.value = true
  error.value = ''
  try {
    courses.value = (await api.get<{ items: typeof courses.value }>('/api/v1/teacher/courses/')).items
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '课程列表加载失败。'
  } finally { loading.value = false }
}
async function change(event: Event) {
  const select = event.target as HTMLSelectElement
  const target = courses.value.find(course => String(course.id) === select.value)
  if (!target || select.value === currentId.value || !router) return
  if (/\/(?:create|edit|import|kc-import|publish-plan)(?:\/|$)/.test(path.value)
    && !window.confirm('切换课程将离开当前页面，尚未保存的修改不会带入其他课程。确认切换？')) {
    select.value = currentId.value
    return
  }
  const section = /^\/teacher\/courses\/\d+\/(banks|experiments|classes|data|settings)(?:\/|$)/.exec(path.value)?.[1]
  await router.push(`${target.detail_url}${section ? `${section}/` : ''}`)
}
onMounted(load)
</script>
<template>
  <div v-if="currentId" class="kt-course-switcher">
    <label for="workspace-course">当前课程</label>
    <select id="workspace-course" aria-label="切换课程" :value="currentId" :disabled="loading || !courses.length" @change="change">
      <option v-if="!courses.some(course => String(course.id) === currentId)" :value="currentId">{{ courseName }}</option>
      <option v-for="course in courses" :key="course.id" :value="String(course.id)">{{ course.name }}</option>
    </select>
    <span v-if="error" role="alert">{{ error }} <button type="button" @click="load">重试</button></span>
  </div>
</template>
