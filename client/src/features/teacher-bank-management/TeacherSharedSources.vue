<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import Alert from '@/design-system/components/Alert.vue'
import EmptyState from '@/design-system/components/EmptyState.vue'
import PageHeader from '@/design-system/components/PageHeader.vue'

import { getTeacherBankManagementApi, type CourseReference } from './api'

const route = useRoute()
const api = getTeacherBankManagementApi()
const courseId = Number(route.params.courseId)
const data = ref<{ course: CourseReference; items: Array<{ id: number; name: string; code: string; term: string; shared_bank_count: number; shared_creator_count: number }> }>()
const error = ref('')

onMounted(async () => {
  try {
    data.value = await api.sharedSources(courseId)
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '共享来源课程加载失败。'
  }
})
</script>

<template>
  <Alert v-if="error" tone="danger">{{ error }}</Alert>
  <template v-else-if="data">
    <PageHeader title="浏览共享题库"><template #kicker>{{ data.course.code }} · {{ data.course.term }}</template><p>共享题库只可复制为本课程的独立私有副本。</p><template #actions><RouterLink class="kt-button kt-button--secondary" :to="`/teacher/courses/${courseId}/banks/`">返回题库</RouterLink></template></PageHeader>
    <EmptyState v-if="!data.items.length" title="暂无可复制的共享题库"><p>其他课程的教师发布共享题库后，会按来源课程显示在这里。</p></EmptyState>
    <section v-else class="kt-card kt-table-card" aria-label="共享来源课程"><div class="kt-table-scroll"><table class="kt-table"><thead><tr><th>来源课程</th><th>共享创建者</th><th>共享题库</th><th>操作</th></tr></thead><tbody><tr v-for="source in data.items" :key="source.id"><td><strong>{{ source.name }}</strong><small>{{ source.code }} · {{ source.term }}</small></td><td>{{ source.shared_creator_count }}</td><td>{{ source.shared_bank_count }}</td><td><RouterLink class="kt-button kt-button--secondary" :to="`/teacher/courses/${courseId}/banks/shared/${source.id}/`">选择创建者</RouterLink></td></tr></tbody></table></div></section>
  </template>
  <p v-else role="status">正在加载共享来源课程…</p>
</template>
