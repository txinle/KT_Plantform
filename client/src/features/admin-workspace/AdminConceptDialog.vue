<script setup lang="ts">
import {ref,watch} from 'vue'
import SiteDialog from '@/features/management-shared/SiteDialog.vue'
import {getTeacherBankManagementApi} from '@/features/teacher-bank-management/api'
import type {AdminRow} from './api'
const props=defineProps<{row?:AdminRow}>(),emit=defineEmits<{close:[];changed:[]}>(),api=getTeacherBankManagementApi(),name=ref(''),description=ref(''),confirm=ref(false),busy=ref(false),error=ref('')
watch(()=>props.row,row=>{name.value=row?.name||'';description.value=row?.description||'';confirm.value=false;error.value=''},{immediate:true})
async function save(){if(!props.row?.course_id)return;busy.value=true;error.value='';try{await api.updateCourseConcept(props.row.course_id,Number(props.row.id),{name:name.value,description:description.value});emit('changed');emit('close')}catch(e){error.value=(e as Error).message}finally{busy.value=false}}
</script>
<template><SiteDialog :open="Boolean(row)" :title="confirm?'确认知识点信息变更':'课程知识点'" :subtitle="row?.course_name" @close="emit('close')"><form id="concept-edit" class="form-grid" @submit.prevent="confirm?save():confirm=true"><template v-if="!confirm"><label class="field">知识点名称<input v-model="name" required :disabled="!row?.course_id"></label><label class="field">说明<textarea v-model="description" rows="3" :disabled="!row?.course_id" /></label><p>知识点编号 {{ row?.code }} · 关联 {{ row?.question_count ?? 0 }} 个作答项</p></template><dl v-else class="detail-list"><div><dt>原名称</dt><dd>{{ row?.name }}</dd></div><div><dt>新名称</dt><dd>{{ name }}</dd></div><div><dt>变更范围</dt><dd>修改当前课程知识点展示信息；编号、映射关系和已发布快照保持不变。</dd></div></dl></form><p v-if="error" role="alert" class="error-block">{{ error }}</p><template #footer><button @click="emit('close')">关闭</button><button v-if="row?.course_id" class="primary" form="concept-edit" type="submit" :disabled="busy">{{ confirm?'确认变更':'预览变更' }}</button></template></SiteDialog></template>
