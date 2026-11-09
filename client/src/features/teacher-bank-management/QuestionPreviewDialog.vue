<script setup lang="ts">
import { onMounted, ref } from 'vue'
import ModalDialog from '@/design-system/components/ModalDialog.vue'
import { createApiClient } from '@/api/client'
import { getBootstrapContext } from '@/app/bootstrap'
import QuestionGroupContent from '@/question-content/QuestionGroupContent.vue'
import QuestionContent from '@/question-content/QuestionContent.vue'
import type { QuestionGroupView } from '@/question-content/question-group-content'
import type { QuestionEditor } from './api'
import { getQuestionGroupApi } from './group-api'

const props = defineProps<{ courseId?: number; bankId: number; id: number; group?: boolean }>()
const emit = defineEmits<{ close: [] }>()
const question = ref<QuestionEditor['question']>()
const groupView = ref<QuestionGroupView>()
const error = ref('')
onMounted(async () => {
  try {
    if (props.group) {
      const group = await getQuestionGroupApi(props.bankId, props.courseId).get(props.id)
      groupView.value = { ...group, group_id: String(group.id) }
    } else {
      const client = createApiClient({ getCsrfToken: () => getBootstrapContext().csrf_token })
      const prefix = props.courseId ? `/api/v1/teacher/courses/${props.courseId}/banks/` : '/api/v1/teacher/banks/'
      question.value = (await client.get<QuestionEditor>(`${prefix}${props.bankId}/questions/${props.id}/editor/?readonly=1`)).question
    }
  } catch (caught) { error.value = caught instanceof Error ? caught.message : '题目加载失败。' }
})
</script>

<template>
  <ModalDialog :open="true" title="完整题目预览" wide @close="emit('close')">
    <p v-if="error" role="alert">{{ error }}</p>
    <QuestionGroupContent v-else-if="groupView" :group="groupView" :responses-by-item-key="{}" readonly />
    <article v-else-if="question"><h2>{{ question.title }}</h2><p><QuestionContent :text="question.instructions || ''" :images="question.images" /></p><p class="kt-preview-material"><QuestionContent :text="question.text" :images="question.images" /></p><p v-for="(text, key) in question.options" v-show="text" :key="key" class="kt-preview-option">{{ String(key).toUpperCase() }}. <QuestionContent :text="text" :images="question.images" option /></p><p>分值 {{ question.max_score }} · 难度 {{ question.teacher_difficulty || '未设置' }}</p></article>
    <p v-else role="status">正在加载完整题目…</p>
    <template #footer><button class="kt-button kt-button--secondary" type="button" @click="emit('close')">关闭预览</button></template>
  </ModalDialog>
</template>

<style scoped>
h2 { font-size:19px; font-weight:650; margin:0; }
article { padding:28px; }
.kt-preview-material { white-space:pre-wrap; font:20px/1.85 Georgia,"Times New Roman",serif; color:#2c394d; }
.kt-preview-option { border:1px solid #e3e9f0; padding:12px; border-radius:6px; font-size:16px; }
</style>
