<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import Alert from '@/design-system/components/Alert.vue'
import QuestionGroupContent from '@/question-content/QuestionGroupContent.vue'
import type { QuestionGroupView } from '@/question-content/question-group-content'
import KcMappingEditor from './KcMappingEditor.vue'
import { getTeacherQuestionBankApi } from '../teacher-question-banks/api'
import { getQuestionGroupApi, type GroupEditorDocument, type GroupEditorMember } from './group-api'

const props = defineProps<{ bankId: number; courseId?: number; groupId?: number }>()
const emit = defineEmits<{ saved: []; close: [] }>()
const api = getQuestionGroupApi(props.bankId, props.courseId)
const draft = ref<GroupEditorDocument>({ id: 0, revision: 0, kind: 'reading', title: '', instructions: '', material: '', word_bank: [], material_segments: [], content_language: 'en', members: [] })
const savedView = ref<QuestionGroupView>()
const originalIds = ref<number[]>([])
const confirmDeletion = ref(false)
const deleteOpen = ref(false)
const studentView = ref(false)
const busy = ref(false)
const error = ref('')
const concepts = ref<Array<{ id: number; kc_code: string; name: string; description: string }>>([])
const removedIds = computed(() => originalIds.value.filter(id => !draft.value.members.some(member => member.id === id)))

function adopt(document: GroupEditorDocument) {
  draft.value = JSON.parse(JSON.stringify(document)) as GroupEditorDocument
  originalIds.value = document.members.flatMap(member => member.id ? [member.id] : [])
  savedView.value = { ...document, group_id: String(document.id) }
  confirmDeletion.value = false
}
async function reload() {
  busy.value = true
  error.value = ''
  try {
    if (props.groupId) adopt(await api.get(props.groupId))
    if (props.courseId) {
      const response = await getTeacherQuestionBankApi().courseConcepts(props.courseId, { all: true })
      concepts.value = response.items.map(item => ({ id: item.id, kc_code: item.kc_code, name: item.name, description: item.description || '' }))
    }
  } catch (caught) { error.value = caught instanceof Error ? caught.message : '题组加载失败。' }
  finally { busy.value = false }
}
function addMember() {
  const order = draft.value.members.length + 1
  draft.value.members.push({ item_key: crypto.randomUUID(), group_order: order, blank_id: draft.value.kind !== 'reading' ? `b${String(order).padStart(2, '0')}` : '',
    question_type: draft.value.kind === 'reading' ? 'single_choice' : 'fill_blank', text: '', answer: '', accepted_answers: [], explanation: '', max_score: '1.00', text_match_case_sensitive: false,
    options: draft.value.kind === 'reading' ? ['A', 'B', 'C', 'D'].map(key => ({ key, text: '' })) : [],
    ...(props.courseId ? { kc_mapping: { root_kc_code: '', nodes: [] } } : {}),
  })
}
function removeMember(index: number) {
  draft.value.members.splice(index, 1)
  draft.value.members.forEach((member, position) => { member.group_order = position + 1 })
}
function memberPayload(member: GroupEditorMember) {
  return { id: member.id ?? null, group_order: member.group_order, blank_id: member.blank_id,
    question_type: member.question_type, text: member.text, options: member.options, answer: member.answer,
    teacher_difficulty: member.teacher_difficulty === '' ? null : member.teacher_difficulty ?? null,
    accepted_answers: member.accepted_answers, explanation: member.explanation, max_score: member.max_score || '1.00',
    content_language: draft.value.content_language, text_match_case_sensitive: member.text_match_case_sensitive,
    text_match_policy_version: 'text-exact-v1',
    ...(props.courseId && member.kc_mapping ? { kc_mapping: { root_kc_code: member.kc_mapping.root_kc_code,
      nodes: member.kc_mapping.nodes.map(node => ({ kc_code: node.kc_code, parent_kc_code: node.parent_kc_code, order: node.order })) } } : {}),
    ...(!props.courseId && member.primary_concept_id ? { primary_concept_id: member.primary_concept_id } : {}),
  }
}
async function save() {
  busy.value = true
  error.value = ''
  try {
    const document = await api.save(draft.value.id || undefined, { kind: draft.value.kind, title: draft.value.title, instructions: draft.value.instructions,
      material: draft.value.material, word_bank: draft.value.word_bank || [], content_language: draft.value.content_language, expected_revision: draft.value.revision,
      confirm_member_deletion: confirmDeletion.value, members: draft.value.members.map(memberPayload) })
    adopt(document)
    emit('saved')
  } catch (caught) { error.value = caught instanceof Error ? caught.message : '题组保存失败。' }
  finally { busy.value = false }
}
async function remove() {
  if (!props.groupId || !deleteOpen.value) return
  busy.value = true
  try { await api.remove(props.groupId); emit('saved'); emit('close') }
  catch (caught) { error.value = caught instanceof Error ? caught.message : '题组删除失败。' }
  finally { busy.value = false }
}
onMounted(reload)
</script>

<template>
  <section class="kt-group-editor" aria-label="整组编辑">
    <header class="kt-section-heading"><h2>{{ groupId ? '编辑题组' : '新建题组' }}</h2><button class="kt-button kt-button--secondary" type="button" @click="emit('close')">关闭编辑</button></header>
    <Alert v-if="error" tone="danger">{{ error }}<button v-if="groupId" type="button" class="kt-button kt-button--secondary" @click="reload">重新加载已保存版本</button></Alert>
    <button v-if="savedView" class="kt-button kt-button--secondary" type="button" @click="studentView = !studentView">{{ studentView ? '收起学生效果' : '学生效果（已保存版本）' }}</button>
    <div class="kt-group-editor__workspace" :class="{ 'has-preview': studentView && savedView }">
      <form class="kt-form-stack" @submit.prevent="save">
        <div class="kt-group-editor__grid">
          <label>题组形式<select v-model="draft.kind" :disabled="Boolean(groupId) || Boolean(draft.members.length)"><option value="reading">阅读理解</option><option value="words">选词填空</option><option value="cloze">短文填空</option></select></label>
          <label>内容语言<select v-model="draft.content_language"><option value="en">英语</option><option value="zh-CN">中文</option><option value="und">未声明</option></select></label>
        </div>
        <label>材料标题<input v-model="draft.title"></label>
        <label>作答说明<textarea v-model="draft.instructions" rows="2" /></label>
        <label v-if="draft.kind === 'words'">共享词库（分号分隔）<textarea :value="draft.word_bank?.join('；')" rows="2" @input="draft.word_bank = ($event.target as HTMLTextAreaElement).value.split(/[;；\n]/).map(value => value.trim()).filter(Boolean)" /></label>
        <label>共享材料<textarea v-model="draft.material" rows="8" class="kt-group-editor__material" :lang="draft.content_language" /></label>
        <p v-if="draft.kind !== 'reading'">在材料中使用 <code v-pre>{{b01}}</code> 等标记，与下方空位编号逐一对应。</p>
        <section v-for="(member, index) in draft.members" :key="member.item_key" class="kt-card">
          <header class="kt-section-heading"><h3>小题 {{ member.group_order }}</h3><button type="button" class="kt-button kt-button--secondary" @click="removeMember(index)">移除小题</button></header>
          <div class="kt-group-editor__grid"><label v-if="draft.kind !== 'reading'">空位编号<input v-model="member.blank_id"></label><label>分值<input v-model="member.max_score" data-group-max-score inputmode="decimal" required></label></div>
          <label>教师设定难度（0–1，可留空）<input v-model="member.teacher_difficulty" inputmode="decimal"></label>
          <label>题干<textarea v-model="member.text" rows="2" /></label>
          <label v-for="option in member.options" :key="option.key">选项 {{ option.key }}<input v-model="option.text"></label>
          <label>标准答案<input v-model="member.answer"></label>
          <label v-if="draft.kind !== 'reading'">可接受答案（分号分隔）<input :value="member.accepted_answers.join('；')" @input="member.accepted_answers = ($event.target as HTMLInputElement).value.split(/[;；]/).map(value => value.trim()).filter(Boolean)"></label>
          <label v-if="draft.kind !== 'reading'"><input v-model="member.text_match_case_sensitive" type="checkbox">区分大小写</label>
          <label>解析<textarea v-model="member.explanation" rows="2" /></label>
          <KcMappingEditor v-if="courseId && member.kc_mapping" :model-value="{ root_kc_code: member.kc_mapping.root_kc_code || '', nodes: member.kc_mapping.nodes }" :concepts="concepts" @update:model-value="member.kc_mapping = $event" />
          <label v-if="!courseId && draft.available_concepts?.length">主知识点<select v-model="member.primary_concept_id"><option :value="null">未设置</option><option v-for="concept in draft.available_concepts" :key="concept.id" :value="concept.id">{{ concept.name }}</option></select></label>
        </section>
        <button class="kt-button kt-button--secondary" type="button" @click="addMember">添加{{ draft.kind !== 'reading' ? '空位' : '小题' }}</button>
        <label v-if="removedIds.length" class="kt-alert kt-alert--warning"><input v-model="confirmDeletion" type="checkbox">确认移除 {{ removedIds.length }} 个成员；服务器将检查发布引用。</label>
        <div class="kt-inline-actions"><button class="kt-button" type="submit" :disabled="busy || Boolean(removedIds.length && !confirmDeletion)">{{ busy ? '正在保存…' : '保存整个题组' }}</button><button v-if="groupId" class="kt-button kt-button--secondary" type="button" @click="deleteOpen = !deleteOpen">删除题组</button></div>
        <div v-if="deleteOpen" class="kt-alert kt-alert--warning"><p>将删除共享材料和全部成员；已有发布引用时服务器会拒绝。</p><button type="button" class="kt-button kt-button--danger" :disabled="busy" @click="remove">确认删除整个题组</button></div>
      </form>
      <aside v-if="studentView && savedView" class="kt-group-editor__preview"><h3>学生视角 · 已保存内容</h3><QuestionGroupContent :group="savedView" :responses-by-item-key="{}" readonly /></aside>
    </div>
  </section>
</template>

<style scoped>
.kt-group-editor { padding: 1.25rem; border: 1px solid var(--kt-border); border-radius: 10px; background: var(--kt-surface); margin-block: 1rem; }
.kt-group-editor__workspace { display: grid; grid-template-columns: minmax(0, 1fr); gap: 1.5rem; margin-top: 1rem; }
.kt-group-editor__workspace.has-preview { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); align-items: start; }
.kt-group-editor__preview { position: sticky; top: 1rem; min-width: 0; padding: 1.25rem; border: 1px solid var(--kt-border); background: var(--kt-surface-subtle); border-radius: 8px; }
.kt-group-editor__preview h3 { margin: 0 0 1rem; font-size: .875rem; color: var(--kt-muted); }
.kt-group-editor__grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1rem; }
.kt-group-editor label { display: block; margin-bottom: .75rem; }
.kt-group-editor__material { font: 1.25rem/1.8 var(--kt-font-article); }
@media (max-width: 70rem) { .kt-group-editor__workspace.has-preview { grid-template-columns: 1fr; } .kt-group-editor__preview { position: static; } }
@media (max-width: 48rem) { .kt-group-editor { padding: 1rem; } .kt-group-editor__grid { grid-template-columns: 1fr; } }
</style>
