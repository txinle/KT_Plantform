<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { createApiClient } from '@/api/client'
import { getBootstrapContext } from '@/app/bootstrap'
import QuestionGroupContent from '@/question-content/QuestionGroupContent.vue'
import QuestionContent from '@/question-content/QuestionContent.vue'
import type { QuestionGroupView } from '@/question-content/question-group-content'
import type { QuestionEditor } from '@/features/teacher-bank-management/api'
import { getQuestionGroupApi } from '@/features/teacher-bank-management/group-api'
const props = defineProps<{ courseId: number; bankId: number; questionId: number }>()
const question = ref<QuestionEditor['question']>()
const groupView = ref<QuestionGroupView>()
const error = ref('')
onMounted(async () => {
  try {
    const client = createApiClient({ getCsrfToken: () => getBootstrapContext().csrf_token })
    const result = await client.get<QuestionEditor>(`/api/v1/teacher/courses/${props.courseId}/banks/${props.bankId}/questions/${props.questionId}/editor/?readonly=1`)
    if (result.question.group_id) {
      const group = await getQuestionGroupApi(props.bankId, props.courseId).get(result.question.group_id)
      groupView.value = { ...group, group_id: String(group.id) }
    } else { question.value = result.question }
  } catch (caught) { error.value = caught instanceof Error ? caught.message : '题目加载失败。' }
})
</script>

<template>
  <div class="kt-experiment-inline-preview">
    <p v-if="error" role="alert">{{ error }}</p>
    <QuestionGroupContent v-else-if="groupView" :group="groupView" :responses-by-item-key="{}" readonly />
    <article v-else-if="question"><h3>{{ question.title }}</h3><p><QuestionContent :text="question.instructions || ''" :images="question.images" /></p><p class="kt-preview-material"><QuestionContent :text="question.text" :images="question.images" /></p><p v-for="(text, key) in question.options" v-show="text" :key="key" class="kt-preview-option">{{ String(key).toUpperCase() }}. <QuestionContent :text="text" :images="question.images" option /></p><p>分值 {{ question.max_score }} · 难度 {{ question.teacher_difficulty || '未设置' }}</p></article>
    <p v-else role="status">正在加载完整题目…</p>
  </div>
</template>

<style scoped>
.kt-experiment-inline-preview{margin-top:14px;background:#fff;border:1px solid #e3e9f0;border-radius:7px;overflow:hidden}
.kt-experiment-inline-preview>article,.kt-experiment-inline-preview>p{padding:16px}
.kt-preview-material{white-space:pre-wrap;overflow-wrap:anywhere;font:18px/1.85 Georgia,"Times New Roman",serif;color:#2c394d}
.kt-preview-option{border:1px solid #e3e9f0;padding:12px;border-radius:6px;font-size:15px}
.kt-experiment-inline-preview :deep(.kt-question-group){border:0}
</style>
