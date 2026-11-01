<script setup lang="ts">
import {ref,watch} from 'vue'
import SiteDialog from '@/features/management-shared/SiteDialog.vue'
import {createApiClient} from '@/api/client'
import {getBootstrapContext} from '@/app/bootstrap'
import QuestionGroupContent from '@/question-content/QuestionGroupContent.vue'
import type {QuestionGroupView} from '@/question-content/question-group-content'
import {getQuestionGroupApi} from '@/features/teacher-bank-management/group-api'
import type {QuestionEditor} from '@/features/teacher-bank-management/api'
import type {AdminRow} from './api'
const props=defineProps<{row?:AdminRow}>(),emit=defineEmits<{close:[]}>(),question=ref<QuestionEditor['question']>(),group=ref<QuestionGroupView>(),error=ref('')
watch(()=>props.row,async row=>{question.value=undefined;group.value=undefined;error.value='';if(!row)return;try{if(row.group_id){const g=await getQuestionGroupApi(Number(row.bank_id),row.course_id).get(Number(row.group_id));group.value={...g,group_id:String(g.id)}}else{const client=createApiClient({getCsrfToken:()=>getBootstrapContext().csrf_token});const prefix=row.course_id?`/api/v1/teacher/courses/${row.course_id}/banks/`:'/api/v1/teacher/banks/';question.value=(await client.get<QuestionEditor>(`${prefix}${row.bank_id}/questions/${row.id}/editor/?readonly=1`)).question}}catch(e){error.value=(e as Error).message}},{immediate:true})
</script>
<template><SiteDialog :open="Boolean(row)" title="完整题目预览" :subtitle="String(row?.bank_name||'')" wide @close="emit('close')"><QuestionGroupContent v-if="group" :group="group" :responses-by-item-key="{}" readonly /><article v-else-if="question"><h3>{{ question.title }}</h3><p>{{ question.instructions }}</p><div class="resource-material"><p>{{ question.text }}</p></div><div v-for="(text,key) in question.options" :key="key" class="resource-answer"><p v-if="text">{{ String(key).toUpperCase() }}. {{ text }}</p></div><details><summary>参考答案与解析</summary><p>{{ question.correct_answer_text || question.accepted_answers_text }}</p><p>{{ question.explanation }}</p></details></article><p v-else-if="!error" role="status">正在读取完整题目…</p><p v-if="error" class="error-block" role="alert">{{ error }}</p><template #footer><button @click="emit('close')">关闭</button><a v-if="row?.detail_url" class="management-action-link" :href="row.detail_url">编辑题目 →</a></template></SiteDialog></template>
<style scoped>.resource-material p{white-space:pre-wrap}.resource-answer{padding-top:12px}</style>
