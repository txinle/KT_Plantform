<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import Alert from '@/design-system/components/Alert.vue'
import QuestionGroupContent from '@/question-content/QuestionGroupContent.vue'
import type { MaterialSegment, QuestionGroupView } from '@/question-content/question-group-content'
import KcMappingEditor from './KcMappingEditor.vue'
import { getTeacherQuestionBankApi } from '../teacher-question-banks/api'
import { getQuestionGroupApi, type GroupEditorDocument, type GroupEditorMember } from './group-api'

const props = defineProps<{ bankId: number; courseId?: number; groupId?: number; initialDocument?: GroupEditorDocument }>()
const emit = defineEmits<{ saved: []; close: []; convert: [kind: string, document: GroupEditorDocument] }>()
const api = getQuestionGroupApi(props.bankId, props.courseId)
const draft = ref<GroupEditorDocument>({ id: 0, revision: 0, kind: 'reading', title: '', instructions: '', material: '', word_bank: [], material_segments: [], content_language: 'en', members: [] })
const kindLabel = computed(() => ({ reading: '阅读理解', words: '选词填空', cloze: '短文填空' })[draft.value.kind])
const previewScore = computed(() => draft.value.members.reduce((sum, member) => sum + (Number(member.max_score) || 0), 0).toFixed(2))
const previewResponses = ref<Record<string, unknown>>({})
const preview = computed<QuestionGroupView>(() => {
  const segments: MaterialSegment[] = []
  let cursor = 0
  for (const match of draft.value.material.matchAll(/\{\{(b[0-9]{2,})\}\}/g)) {
    const start = match.index || 0
    if (start > cursor) segments.push({ kind: 'text', text: draft.value.material.slice(cursor, start) })
    const member = draft.value.members.find(item => item.blank_id === match[1])
    if (member) segments.push({ kind: 'blank', blank_id: match[1]!, item_key: member.item_key })
    else segments.push({ kind: 'text', text: match[0] })
    cursor = start + match[0].length
  }
  if (cursor < draft.value.material.length) segments.push({ kind: 'text', text: draft.value.material.slice(cursor) })
  return { ...draft.value, group_id: String(draft.value.id), material_segments: segments, members: draft.value.members.map(member => ({ ...member, options: member.options.filter(option => option.text.trim()) })) }
})
const originalKind = ref('')
const saveAsNew = computed(() => Boolean(props.groupId && originalKind.value && originalKind.value !== draft.value.kind))
const confirmConversion = ref(false)
function changeKind(event: Event) {
  const kind = (event.target as HTMLSelectElement).value
  if (!['reading', 'words', 'cloze'].includes(kind)) { emit('convert', kind, draft.value); return }
  draft.value.kind = kind as GroupEditorDocument['kind']
  draft.value.members.forEach((member, index) => {
    member.question_type = kind === 'reading' ? 'single_choice' : 'fill_blank'
    member.blank_id = kind === 'reading' ? '' : `b${String(index + 1).padStart(2, '0')}`
    if (kind === 'reading' && !member.options.length) member.options = ['A', 'B', 'C', 'D', 'E', 'F'].map(key => ({ key, text: '' }))
  })
}
const originalIds = ref<number[]>([])
const confirmDeletion = ref(false)
const deleteOpen = ref(false)
const busy = ref(false)
const error = ref('')
const concepts = ref<Array<{ id: number; kc_code: string; name: string; description: string }>>([])
const removedIds = computed(() => originalIds.value.filter(id => !draft.value.members.some(member => member.id === id)))

function adopt(document: GroupEditorDocument) {
  draft.value = JSON.parse(JSON.stringify(document)) as GroupEditorDocument
  if (draft.value.kind === 'reading') draft.value.members.forEach(member => {
    member.options = 'ABCDEF'.split('').map(key => ({ key, text: member.options.find(option => option.key === key)?.text || '' }))
  })
  originalIds.value = document.members.flatMap(member => member.id ? [member.id] : [])
  originalKind.value = document.kind
  confirmDeletion.value = false
}
async function reload() {
  busy.value = true
  error.value = ''
  try {
    if (props.groupId) adopt(await api.get(props.groupId))
    else if (props.initialDocument) adopt(props.initialDocument)
    if (props.courseId) {
      const response = await getTeacherQuestionBankApi().courseConcepts(props.courseId, { all: true })
      concepts.value = response.items.map(item => ({ id: item.id, kc_code: item.kc_code, name: item.name, description: item.description || '' }))
    }
  } catch (caught) { error.value = caught instanceof Error ? caught.message : '题目加载失败。' }
  finally { busy.value = false }
}
function addMember() {
  const order = draft.value.members.length + 1
  draft.value.members.push({ item_key: crypto.randomUUID(), group_order: order, blank_id: draft.value.kind !== 'reading' ? `b${String(order).padStart(2, '0')}` : '',
    question_type: draft.value.kind === 'reading' ? 'single_choice' : 'fill_blank', text: '', answer: '', accepted_answers: [], explanation: '', max_score: '1.00', text_match_case_sensitive: false,
    options: draft.value.kind === 'reading' ? ['A', 'B', 'C', 'D', 'E', 'F'].map(key => ({ key, text: '' })) : [],
    ...(props.courseId ? { kc_mapping: { root_kc_code: '', nodes: [] } } : {}),
  })
}
function removeMember(index: number) {
  draft.value.members.splice(index, 1)
  draft.value.members.forEach((member, position) => { member.group_order = position + 1 })
}
function memberPayload(member: GroupEditorMember) {
  return { id: saveAsNew.value ? null : member.id ?? null, group_order: member.group_order, blank_id: member.blank_id,
    question_type: member.question_type, text: member.text, options: member.options.filter(option => option.text.trim()), answer: member.answer,
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
  if (saveAsNew.value && !confirmConversion.value) return
  busy.value = true
  error.value = ''
  try {
    const document = await api.save(saveAsNew.value ? undefined : draft.value.id || undefined, { kind: draft.value.kind, title: draft.value.title, instructions: draft.value.instructions,
      material: draft.value.material, word_bank: draft.value.word_bank || [], content_language: draft.value.content_language, expected_revision: draft.value.revision,
      confirm_member_deletion: confirmDeletion.value, members: draft.value.members.map(memberPayload) })
    adopt(document)
    emit('saved')
  } catch (caught) { error.value = caught instanceof Error ? caught.message : '题目保存失败。' }
  finally { busy.value = false }
}
async function remove() {
  if (!props.groupId || !deleteOpen.value) return
  busy.value = true
  try { await api.remove(props.groupId); emit('saved'); emit('close') }
  catch (caught) { error.value = caught instanceof Error ? caught.message : '题目删除失败。' }
  finally { busy.value = false }
}
defineExpose({ busy, saveDisabled: computed(() => busy.value || (saveAsNew.value && !confirmConversion.value) || Boolean(removedIds.value.length && !confirmDeletion.value)) })
onMounted(reload)
</script>

<template>
  <section class="kt-group-editor" aria-label="完整题目编辑">
    <Alert v-if="error" tone="danger">{{ error }}<button v-if="groupId" type="button" class="kt-button kt-button--secondary" @click="reload">重新加载已保存版本</button></Alert>

    <div class="kt-group-editor__workspace has-preview">
      <form id="question-document-form" class="kt-form-stack" @submit.prevent="save">
        <div class="kt-group-editor__grid">
          <label>题型<select :value="draft.kind" @change="changeKind"><option value="single_choice">单选题</option><option value="multiple_choice">多选题</option><option value="true_false">判断题</option><option value="numeric">数值题</option><option value="fill_blank">填空题</option><option value="short_answer">简答题</option><option value="translation">翻译题</option><option value="essay">作文题</option><option value="reading">阅读理解</option><option value="words">选词填空</option><option value="cloze">短文填空</option></select></label>
          <label>内容语言<select v-model="draft.content_language"><option value="en">英语</option><option value="zh-CN">中文</option><option value="und">未声明</option></select></label>
        </div>
        <label>题目标题<input v-model="draft.title"></label>
        <label>作答说明<textarea v-model="draft.instructions" rows="2" /></label>
        <label v-if="draft.kind === 'words'">共享词库（分号分隔）<textarea :value="draft.word_bank?.join('；')" rows="2" @input="draft.word_bank = ($event.target as HTMLTextAreaElement).value.split(/[;；\n]/).map(value => value.trim()).filter(Boolean)" /></label>
        <label>文章材料<textarea v-model="draft.material" rows="9" class="kt-group-editor__material" :lang="draft.content_language" /></label>
        <p v-if="draft.kind !== 'reading'">在材料中使用 <code v-pre>{{b01}}</code> 等标记，与下方空位编号逐一对应。</p>
        <section v-for="(member, index) in draft.members" :key="member.item_key" class="kt-group-editor__member">
          <header class="kt-section-heading"><h3>小题 {{ member.group_order }}</h3><button type="button" class="kt-button kt-button--secondary" @click="removeMember(index)">移除小题</button></header>
          <div class="kt-group-editor__grid"><label v-if="draft.kind !== 'reading'">空位编号<input v-model="member.blank_id"></label><label>分值<input v-model="member.max_score" data-group-max-score inputmode="decimal" required></label></div>
          <label>教师设定难度（0–1，可留空）<input v-model="member.teacher_difficulty" inputmode="decimal"></label>
          <label>题干<textarea v-model="member.text" rows="2" /></label>
          <label v-for="option in member.options" :key="option.key" class="kt-group-editor__option"><span>{{ option.key }}</span><input v-model="option.text" :aria-label="`小题 ${member.group_order} 选项 ${option.key}`"></label>
          <label>标准答案<input v-model="member.answer"></label>
          <label v-if="draft.kind !== 'reading'">可接受答案（分号分隔）<input :value="member.accepted_answers.join('；')" @input="member.accepted_answers = ($event.target as HTMLInputElement).value.split(/[;；]/).map(value => value.trim()).filter(Boolean)"></label>
          <label v-if="draft.kind !== 'reading'"><input v-model="member.text_match_case_sensitive" type="checkbox">区分大小写</label>
          <label>解析<textarea v-model="member.explanation" rows="2" /></label>
          <KcMappingEditor v-if="courseId && member.kc_mapping" :model-value="{ root_kc_code: member.kc_mapping.root_kc_code || '', nodes: member.kc_mapping.nodes }" :concepts="concepts" @update:model-value="member.kc_mapping = $event" />
          <label v-if="!courseId && draft.available_concepts?.length">主知识点<select v-model="member.primary_concept_id"><option :value="null">未设置</option><option v-for="concept in draft.available_concepts" :key="concept.id" :value="concept.id">{{ concept.name }}</option></select></label>
        </section>
        <button class="kt-button kt-button--secondary" type="button" @click="addMember">添加{{ draft.kind !== 'reading' ? '空位' : '小题' }}</button>
        <label v-if="removedIds.length" class="kt-alert kt-alert--warning"><input v-model="confirmDeletion" type="checkbox">确认移除 {{ removedIds.length }} 个成员；服务器将检查发布引用。</label>
        <label v-if="saveAsNew" class="kt-alert kt-alert--warning"><input v-model="confirmConversion" type="checkbox">题型转换将另存为新题，原题及已发布快照保留。确认另存。</label><div class="kt-inline-actions"><button class="kt-button" type="submit" :disabled="busy || (saveAsNew && !confirmConversion) || Boolean(removedIds.length && !confirmDeletion)">{{ busy ? '正在保存…' : saveAsNew ? '另存为新题' : '保存题目' }}</button><button v-if="groupId" class="kt-button kt-button--secondary" type="button" @click="deleteOpen = !deleteOpen">删除题目</button></div>
        <div v-if="deleteOpen" class="kt-alert kt-alert--warning"><p>将删除共享材料和全部成员；已有发布引用时服务器会拒绝。</p><button type="button" class="kt-button kt-button--danger" :disabled="busy" @click="remove">确认删除题目</button></div>
      </form>
      <aside class="kt-group-editor__preview"><header class="kt-group-editor__preview-label"><span>学生视角预览</span><span><span class="kt-group-editor__type">{{ kindLabel }}</span> {{ previewScore }} 分</span></header><QuestionGroupContent :group="preview" :responses-by-item-key="previewResponses" @request-activate="intent => { if (intent.hasAnswer) previewResponses[intent.itemKey] = intent.answer }" @answer-change="change => previewResponses[change.itemKey] = change.answer" /><footer class="kt-group-editor__preview-note">此处仅预览。正确答案与评分要点不向学生展示。</footer></aside>
    </div>
  </section>
</template>

<style scoped>
.kt-group-editor { padding:0; margin:0; }
.kt-group-editor__workspace { display:grid; grid-template-columns:minmax(0,1.1fr) minmax(0,1fr); gap:24px; align-items:start; }
.kt-group-editor__preview { position:sticky; top:91px; min-width:0; padding:0; border:1px solid #e3e9f0; background:#fff; border-radius:10px; overflow:hidden; }
.kt-group-editor__preview-label { border-bottom:1px solid #e3e9f0; padding:14px 20px; display:flex; align-items:center; justify-content:space-between; gap:12px; color:#637388; font-size:14px; }
.kt-group-editor__preview-note { padding:13px 24px; background:#fafbfd; border-top:1px solid #e3e9f0; font-size:14px; color:#637388; line-height:1.7; }
.kt-group-editor__type { border-radius:4px; padding:4px 8px; font-size:12px; background:#f0f3f8; color:#637388; }
.kt-group-editor__grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:20px; }
.kt-group-editor form { display:grid; gap:20px; border:1px solid #e3e9f0; border-radius:10px; background:#fff; padding:24px; }
.kt-group-editor label { display:grid; gap:9px; margin:0; font-size:14px; font-weight:600; }
.kt-group-editor__material { font:inherit; }
.kt-group-editor__member { border:1px solid #e3e9f0; border-radius:7px; padding:18px; margin-top:16px; }
.kt-group-editor__member>label, .kt-group-editor__member>.kt-group-editor__grid { margin-top:12px; }
.kt-group-editor__member label { color:#637388; }
.kt-group-editor__member h3 { font-size:16px; font-weight:600; }
.kt-group-editor__member .kt-group-editor__option { display:flex; align-items:center; gap:9px; margin-top:8px; }
.kt-group-editor__option>span { flex:none; }
.kt-group-editor input:not([type=checkbox]), .kt-group-editor textarea, .kt-group-editor select { width:100%; border:1px solid #cdd7e3; border-radius:6px; background:white; min-height:44px; padding:10px 12px; font:400 16px/1.5 "Segoe UI","Microsoft YaHei","PingFang SC",sans-serif; color:#26364c; }
.kt-group-editor__option input { font-size:14px; }
@media(max-width:1000px) { .kt-group-editor__workspace { grid-template-columns:1fr; } .kt-group-editor__preview { position:static; } }
@media(max-width:760px) { .kt-group-editor__grid { grid-template-columns:1fr; } .kt-group-editor form { padding:20px; } .kt-group-editor__preview-note { padding:13px 16px; } .kt-group-editor__preview :deep(.kt-question-group__material) { font-size:18px; } }
</style>
