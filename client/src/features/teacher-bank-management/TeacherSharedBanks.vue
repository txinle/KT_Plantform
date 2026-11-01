<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import Alert from '@/design-system/components/Alert.vue'
import EmptyState from '@/design-system/components/EmptyState.vue'
import PageHeader from '@/design-system/components/PageHeader.vue'

import { getTeacherBankManagementApi, type CourseReference } from './api'

const route = useRoute()
const router = useRouter()
const api = getTeacherBankManagementApi()
const courseId = Number(route.params.courseId)
const sourceCourseId = Number(route.params.sourceCourseId)
const creatorId = Number(route.params.creatorId)
const data = ref<{ course: CourseReference; source_course: CourseReference; creator: { id: number; name: string; username: string }; items: Array<{ id: number; name: string; description: string; complete_question_count?: number; question_count: number }> }>()
const error = ref('')
const copyingId = ref<number>()

async function copy(bankId: number) {
  copyingId.value = bankId
  error.value = ''
  try {
    const copied = await api.copySharedBank(courseId, sourceCourseId, creatorId, bankId)
    await router.push(copied.detail_url)
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '复制共享题库失败。'
  } finally {
    copyingId.value = undefined
  }
}

onMounted(async () => {
  try {
    data.value = await api.sharedBanks(courseId, sourceCourseId, creatorId)
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '共享题库加载失败。'
  }
})
</script>

<template>
  <Alert v-if="error" tone="danger">{{ error }}</Alert>
  <template v-else-if="data"><PageHeader title="共享题库"><template #kicker>题库 / 共享题库</template><p>{{ data.source_course.name }} / {{ data.creator.name }} · 复制后生成内容独立的私有题库。</p><template #actions><RouterLink class="kt-button kt-button--secondary" :to="`/teacher/courses/${courseId}/banks/shared/${sourceCourseId}/`">返回创建者</RouterLink></template></PageHeader><nav class="kt-bank-module-nav" aria-label="题库模块"><RouterLink :to="`/teacher/courses/${courseId}/banks/`">题库管理</RouterLink><RouterLink :to="`/teacher/courses/${courseId}/banks/?tab=course-kcs`">课程 KC 总库</RouterLink><RouterLink :to="`/teacher/courses/${courseId}/banks/shared/`" aria-current="page">共享题库</RouterLink><RouterLink :to="`/teacher/courses/${courseId}/banks/archived/`">已归档</RouterLink></nav><EmptyState v-if="!data.items.length" title="没有可复制的题库"><p>来源课程的共享题库已不再可用。</p></EmptyState><section v-else class="kt-card kt-table-card kt-bank-secondary-table"><div class="kt-table-scroll"><table class="kt-table"><thead><tr><th>题库</th><th>题目数</th><th>操作</th></tr></thead><tbody><tr v-for="bank in data.items" :key="bank.id"><td><strong>{{ bank.name }}</strong><small>{{ bank.description || '未填写说明' }}</small></td><td>{{ bank.complete_question_count ?? bank.question_count }}</td><td><button class="kt-button" type="button" :disabled="copyingId === bank.id" @click="copy(bank.id)">{{ copyingId === bank.id ? '正在复制…' : '复制到当前课程' }}</button></td></tr></tbody></table></div></section></template><p v-else role="status">正在加载共享题库…</p>
</template>
