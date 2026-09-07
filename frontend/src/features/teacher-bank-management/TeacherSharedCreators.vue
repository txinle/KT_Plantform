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
const sourceCourseId = Number(route.params.sourceCourseId)
const data = ref<{ course: CourseReference; source_course: CourseReference; items: Array<{ id: number; name: string; username: string; shared_bank_count: number }> }>()
const error = ref('')

onMounted(async () => {
  try {
    data.value = await api.sharedCreators(courseId, sourceCourseId)
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '共享创建者加载失败。'
  }
})
</script>

<template>
  <Alert v-if="error" tone="danger">{{ error }}</Alert>
  <template v-else-if="data"><PageHeader title="选择创建者"><template #kicker>{{ data.source_course.code }} · {{ data.source_course.term }}</template><p>来源课程：{{ data.source_course.name }}</p><template #actions><RouterLink class="kt-button kt-button--secondary" :to="`/teacher/courses/${courseId}/banks/shared/`">返回来源课程</RouterLink></template></PageHeader><EmptyState v-if="!data.items.length" title="没有可选创建者"><p>来源课程目前没有共享题库。</p></EmptyState><section v-else class="kt-card kt-table-card"><div class="kt-table-scroll"><table class="kt-table"><thead><tr><th>创建者</th><th>共享题库</th><th>操作</th></tr></thead><tbody><tr v-for="creator in data.items" :key="creator.id"><td><strong>{{ creator.name }}</strong><small>{{ creator.username }}</small></td><td>{{ creator.shared_bank_count }}</td><td><RouterLink class="kt-button kt-button--secondary" :to="`/teacher/courses/${courseId}/banks/shared/${sourceCourseId}/creators/${creator.id}/`">查看题库</RouterLink></td></tr></tbody></table></div></section></template><p v-else role="status">正在加载共享创建者…</p>
</template>
