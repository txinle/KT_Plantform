<script setup lang="ts">
import { useBankPageUrl } from './bank-page-navigation'
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { ApiError } from '@/api/client'
import Alert from '@/design-system/components/Alert.vue'
import FormField from '@/design-system/components/FormField.vue'
import PageHeader from '@/design-system/components/PageHeader.vue'
import KcMappingEditor from './KcMappingEditor.vue'
import Icon from '@/design-system/Icon.vue'
import QuestionGroupEditor from './QuestionGroupEditor.vue'
import type { GroupEditorDocument } from './group-api'
import './question-editor.css'
import { getTeacherStandaloneQuestionBankApi } from './standalone-api'

import { getTeacherBankManagementApi, type QuestionEditor } from './api'

type MappingNode = { kc_code: string; parent_kc_code: string | null; order: number }

const props = defineProps<{ standalone?: boolean }>()
const route = useRoute()
const bankPageUrl = useBankPageUrl()
const router = useRouter()
const standaloneApi = props.standalone ? getTeacherStandaloneQuestionBankApi() : undefined
const api = props.standalone ? {
  questionEditor: async (_courseId: number, bankId: number, questionId: number) => {
    const data = await standaloneApi!.questionEditor(bankId, questionId)
    return { ...data, question: { ...data.question, kc_mapping: { schema_version: 'question-kc-tree-v2', root_kc_code: null, nodes: [] } } } as QuestionEditor
  },
  updateQuestion: async (_courseId: number, bankId: number, questionId: number, body: Record<string, unknown>) => standaloneApi!.updateQuestion(bankId, questionId, body),
} : getTeacherBankManagementApi()
const courseId = Number(route.params.courseId)
const bankId = Number(route.params.bankId)
const questionId = Number(route.params.questionId)
const activeGroupId = ref(Number(route.query?.group) || undefined)
const groupKind = ref('')
const groupEditor = ref<InstanceType<typeof QuestionGroupEditor>>()
const initialGroup = ref<GroupEditorDocument>()
const converted = ref(false)
const confirmConversion = ref(false)
const originalType = ref('')
const editor = ref<QuestionEditor>()
const error = ref('')
const fieldErrors = ref<Record<string, string>>({})
const saving = ref(false)
const moreOptionsOpen = ref(false)
const previewAnswer = ref('')
const previewChoices = ref<string[]>([])
function previewChoice(key: string, checked: boolean) {
  if (draft.question_type === 'multiple_choice') previewChoices.value = checked ? [...new Set([...previewChoices.value, key])] : previewChoices.value.filter(value => value !== key)
  else previewAnswer.value = key
}
const answersOpen = ref(false)
const draft = reactive({
  title: '', instructions: '', educational_type: 'standard', max_score: '1.00', content_language: 'und', text_match_case_sensitive: false,
  text: '', question_type: '', status: '', teacher_difficulty: '', absolute_tolerance: '', relative_tolerance: '', explanation: '', correct_answer_text: '', accepted_answers_text: '', option_a: '', option_b: '', option_c: '', option_d: '', option_e: '', option_f: '',
})
const mapping = reactive<{ root_kc_code: string; nodes: MappingNode[] }>({ root_kc_code: '', nodes: [] })

const choiceQuestion = computed(() => editor.value?.question_types.some((item) => item.value === draft.question_type && ['single_choice', 'multiple_choice'].includes(item.value)) ?? false)
const typeValue = computed(() => draft.educational_type === 'standard' ? draft.question_type : draft.educational_type)
const saveAsNew = computed(() => converted.value || Boolean(questionId && originalType.value && originalType.value !== typeValue.value))
function changeType(event: Event) {
  const kind = (event.target as HTMLSelectElement).value
  if (['reading', 'words', 'cloze'].includes(kind)) {
    if (questionId && !window.confirm('转换将另存为新题，原题与已发布快照保留。继续编辑新题？')) return
    groupKind.value = kind
    initialGroup.value = { id: 0, revision: 0, kind: kind as GroupEditorDocument['kind'], title: draft.title, instructions: draft.instructions, material: draft.text, material_segments: [], word_bank: [], content_language: draft.content_language, members: [{ item_key: crypto.randomUUID(), group_order: 1, blank_id: kind === 'reading' ? '' : 'b01', question_type: kind === 'reading' ? 'single_choice' : 'fill_blank', text: draft.text, options: 'abcdef'.split('').map(key => ({ key: key.toUpperCase(), text: draft[`option_${key}` as keyof typeof draft] as string })), answer: draft.correct_answer_text, accepted_answers: draft.accepted_answers_text.split(/[;；]/).filter(Boolean), explanation: draft.explanation, max_score: draft.max_score, teacher_difficulty: draft.teacher_difficulty, text_match_case_sensitive: draft.text_match_case_sensitive, kc_mapping: JSON.parse(JSON.stringify(mapping)) }] }
    return
  }
  draft.educational_type = ['translation', 'essay'].includes(kind) ? kind : 'standard'
  draft.question_type = draft.educational_type === 'standard' ? kind : 'short_answer'
}
function fromGroup(kind: string, document: GroupEditorDocument) {
  if (!window.confirm('转换将另存为新题，原题与已发布快照保留。继续编辑新题？')) return
  const member = document.members[0]
  Object.assign(draft, { title: document.title, instructions: document.instructions, text: document.material, content_language: document.content_language, max_score: member?.max_score || '1.00', correct_answer_text: String(member?.answer ?? ''), accepted_answers_text: member?.accepted_answers.join('；') || '', explanation: member?.explanation || '', teacher_difficulty: String(member?.teacher_difficulty ?? ''), text_match_case_sensitive: member?.text_match_case_sensitive || false })
  if (member?.kc_mapping) Object.assign(mapping, JSON.parse(JSON.stringify(member.kc_mapping)))
  member?.options.forEach(option => { draft[`option_${option.key.toLowerCase()}` as 'option_a'] = option.text })
  groupKind.value = ''; activeGroupId.value = undefined; converted.value = true; confirmConversion.value = true
  changeType({ target: { value: kind } } as unknown as Event)
}
const originallyArchived = computed(() => editor.value?.question.status === 'archived')

function setError(caught: unknown, fallback: string) {
  error.value = caught instanceof Error ? caught.message : fallback
  fieldErrors.value = caught instanceof ApiError
    ? Object.fromEntries(Object.entries(caught.fields).map(([key, value]) => [key, Array.isArray(value) ? String(value[0]) : String(value)]))
    : {}
  if (['correct_answer_text', 'accepted_answers_text', 'teacher_difficulty', 'explanation', 'absolute_tolerance', 'relative_tolerance'].some(key => fieldErrors.value[key])) answersOpen.value = true
  if (fieldErrors.value.option_e || fieldErrors.value.option_f || fieldErrors.value.options) moreOptionsOpen.value = true
}

onMounted(async () => {
  try {
    editor.value = await api.questionEditor(courseId, bankId, questionId)
    const question = editor.value.question
    if (question.group_id) activeGroupId.value = question.group_id
    Object.assign(draft, {
      title: question.title || '', instructions: question.instructions || '', educational_type: question.educational_type || 'standard', max_score: question.max_score || '1.00', content_language: question.content_language || 'und', text_match_case_sensitive: question.text_match_case_sensitive || false,
      text: question.text,
      question_type: question.question_type,
      status: question.status === 'archived' ? 'archived' : 'active',
      teacher_difficulty: question.teacher_difficulty,
      absolute_tolerance: question.absolute_tolerance,
      relative_tolerance: question.relative_tolerance,
      explanation: question.explanation,
      correct_answer_text: question.correct_answer_text,
      accepted_answers_text: question.accepted_answers_text,
      ...Object.fromEntries(Object.entries(question.options).map(([key, value]) => [`option_${key}`, value])),
    })
    originalType.value = typeValue.value
    if (activeGroupId.value) groupKind.value = 'reading'
    moreOptionsOpen.value = Boolean(draft.option_e || draft.option_f)
    mapping.root_kc_code = question.kc_mapping.root_kc_code || ''
    mapping.nodes = question.kc_mapping.nodes.map((node) => ({ ...node }))
  } catch (caught) {
    setError(caught, '题目编辑信息加载失败。')
  }
})

async function save() {
  if (saveAsNew.value && !confirmConversion.value) return
  saving.value = true
  error.value = ''
  fieldErrors.value = {}
  try {
    const saved = await api.updateQuestion(courseId, bankId, saveAsNew.value ? 0 : questionId, {
      expected_updated_at: editor.value?.question.updated_at,
      ...draft,
      kc_mapping_json: JSON.stringify({
        schema_version: 'question-kc-tree-v2',
        root_kc_code: mapping.root_kc_code || null,
        nodes: mapping.nodes,
      }),
    })
    await router.push(bankPageUrl(saved.detail_url))
  } catch (caught) {
    setError(caught, '题目保存失败。')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <Alert v-if="error && !editor" tone="danger">{{ error }}</Alert>
  <template v-else-if="editor">
    <PageHeader class="kt-question-editor-page-header" :title="activeGroupId ? '编辑题目' : questionId && !saveAsNew ? `编辑第 ${editor.question.sequence} 题` : '新建题目'"><template #kicker>{{ editor.bank.name }}</template><p>内容可不完整保存；服务器会在每次保存后自动判断题目是否可用。</p><template #actions><RouterLink class="kt-button kt-button--secondary" :to="bankPageUrl(editor.detail_url)">返回题库</RouterLink><button class="kt-button" type="submit" form="question-document-form" :disabled="groupKind || activeGroupId ? groupEditor?.saveDisabled : saving || (saveAsNew && !confirmConversion)">保存题目</button></template></PageHeader>
    <Alert v-if="error" tone="danger">{{ error }}</Alert>
    <Alert v-if="editor.question.draft_reasons.length" tone="warning"><strong>当前保存为草稿。</strong><ul><li v-for="reason in editor.question.draft_reasons" :key="reason">{{ reason }}</li></ul></Alert>

    <QuestionGroupEditor v-if="groupKind || activeGroupId" ref="groupEditor" :bank-id="bankId" :course-id="standalone ? undefined : courseId" :group-id="activeGroupId" :initial-document="initialGroup" @convert="fromGroup" @saved="router.push(bankPageUrl(editor.detail_url))" @close="router.push(bankPageUrl(editor.detail_url))" />
    <div v-else class="kt-question-document">
      <form id="question-document-form" class="kt-form-stack kt-question-editor" novalidate @submit.prevent="save">
        <section class="kt-card"><div class="kt-form-grid"><FormField for-id="question-max-score" label="分值" :error="fieldErrors.max_score"><input id="question-max-score" v-model="draft.max_score" data-question-max-score inputmode="decimal"></FormField><FormField for-id="question-language" label="内容语言"><select id="question-language" v-model="draft.content_language"><option value="und">未声明</option><option value="en">英语</option><option value="zh-CN">中文</option></select></FormField></div><label v-if="['fill_blank', 'short_answer'].includes(draft.question_type)"><input v-model="draft.text_match_case_sensitive" type="checkbox">区分大小写</label></section>
        <section class="kt-card"><label>标题<input v-model="draft.title" maxlength="240"></label><label>作答说明<textarea v-model="draft.instructions" rows="2" /></label></section>
        <section v-if="draft.question_type === 'short_answer'" class="kt-card"><label>教学题型<select v-model="draft.educational_type"><option value="standard">简答题</option><option value="translation">翻译题</option><option value="essay">作文题</option></select></label><p>翻译和作文沿用实验的简答题评阅规则；参考答案和评阅说明分别填写在答案、解析中。</p></section>
        <section class="kt-card" aria-labelledby="question-basics-title">
          <div class="kt-question-editor-heading"><Icon name="definition" :size="24" /><div><h2 id="question-basics-title">基础信息</h2><p>设置题目的基本属性。</p></div></div>
          <div class="kt-form-grid">
            <FormField for-id="question-type" label="题型" :error="fieldErrors.question_type"><select id="question-type" :value="typeValue" required @change="changeType"><option v-for="item in editor.question_types" :key="item.value" :value="item.value">{{ item.label }}</option><option value="translation">翻译题</option><option value="essay">作文题</option><option value="reading">阅读理解</option><option value="words">选词填空</option><option value="cloze">短文填空</option></select></FormField>
            <FormField v-if="originallyArchived" for-id="question-status" label="题目状态" :error="fieldErrors.status"><select id="question-status" v-model="draft.status"><option value="archived">保持归档</option><option value="active">恢复并自动判断</option></select></FormField>
            <FormField v-else for-id="question-status-mode" label="题目状态" :error="fieldErrors.status"><p id="question-status-mode">自动模式：保存后自动判断；内容完整则可用，不完整则保存为草稿并说明原因。</p></FormField>
          </div>
        </section>
        <section class="kt-card" aria-labelledby="question-content-title">
          <div class="kt-question-editor-heading"><Icon name="definition" :size="24" /><div><h2 id="question-content-title">题目内容</h2><p>输入题目题干及选项。</p></div></div>
          <FormField for-id="question-text" label="题干" :error="fieldErrors.text"><textarea id="question-text" v-model="draft.text" rows="9" /></FormField>
          <template v-if="choiceQuestion">
            <div class="kt-form-grid"><FormField v-for="key in ['a', 'b', 'c', 'd']" :key="key" :for-id="`option-${key}`" :label="`选项 ${key.toUpperCase()}`" :error="fieldErrors[`option_${key}`]"><input :id="`option-${key}`" v-model="draft[`option_${key}` as keyof typeof draft]"></FormField></div>
            <details class="kt-question-editor-more" :open="moreOptionsOpen" @toggle="moreOptionsOpen = ($event.target as HTMLDetailsElement).open"><summary>显示更多选项（E、F）</summary><div class="kt-form-grid"><FormField v-for="key in ['e', 'f']" :key="key" :for-id="`option-${key}`" :label="`选项 ${key.toUpperCase()}`" :error="fieldErrors[`option_${key}`]"><input :id="`option-${key}`" v-model="draft[`option_${key}` as keyof typeof draft]"></FormField></div></details>
          </template>
        </section>
        <section class="kt-card" aria-labelledby="question-answer-title">
          <button type="button" class="kt-editor-section-toggle" data-answer-toggle :aria-expanded="answersOpen" aria-controls="question-answer-fields" @click="answersOpen = !answersOpen"><h2 id="question-answer-title">答案与解析</h2><span class="kt-editor-section-summary">正确答案 {{ draft.correct_answer_text || '未设置' }} · 难度 {{ draft.teacher_difficulty || '未设置' }} · {{ draft.explanation ? '已填写解析' : '暂无解析' }}</span><span>{{ answersOpen ? '收起' : '点击展开' }}</span><Icon name="chevron-down" :size="16" :class="{ 'is-expanded': answersOpen }" /></button>
          <div v-show="answersOpen" id="question-answer-fields" class="kt-editor-section-body">
            <div class="kt-form-grid">
              <FormField for-id="question-answer" label="正确答案" :error="fieldErrors.correct_answer_text"><input id="question-answer" v-model="draft.correct_answer_text"></FormField>
              <FormField for-id="question-difficulty" label="教师设定难度" :error="fieldErrors.teacher_difficulty"><input id="question-difficulty" v-model="draft.teacher_difficulty" inputmode="decimal"></FormField>
            </div>
            <FormField v-if="!choiceQuestion" for-id="question-accepted-answers" label="其他可接受答案（分号分隔）" :error="fieldErrors.accepted_answers_text"><input id="question-accepted-answers" v-model="draft.accepted_answers_text"></FormField>
            <FormField for-id="question-explanation" label="解析" :error="fieldErrors.explanation"><textarea id="question-explanation" v-model="draft.explanation" rows="2" /></FormField>
            <details class="kt-question-editor-more" :open="Boolean(draft.absolute_tolerance || draft.relative_tolerance || fieldErrors.absolute_tolerance || fieldErrors.relative_tolerance)">
              <summary>误差容限设置</summary><div class="kt-form-grid">
                <FormField for-id="question-absolute-tolerance" label="绝对误差容限" :error="fieldErrors.absolute_tolerance"><input id="question-absolute-tolerance" v-model="draft.absolute_tolerance" inputmode="decimal"></FormField>
                <FormField for-id="question-relative-tolerance" label="相对误差容限" :error="fieldErrors.relative_tolerance"><input id="question-relative-tolerance" v-model="draft.relative_tolerance" inputmode="decimal"></FormField>
              </div>
            </details>
          </div>
        </section>
        <KcMappingEditor v-if="!standalone" :model-value="mapping" :concepts="editor.concepts" :error="fieldErrors.kc_mapping_json" @update:model-value="Object.assign(mapping, $event)" />
        <label v-if="saveAsNew" class="kt-alert kt-alert--warning"><input v-model="confirmConversion" type="checkbox">确认将题型转换结果另存为新题；原题和已发布快照保留。</label><div class="kt-inline-actions"><button class="kt-button" type="submit" :disabled="saving || (saveAsNew && !confirmConversion)">{{ saving ? '正在保存…' : saveAsNew ? '另存为新题' : '保存题目' }}</button><RouterLink class="kt-button kt-button--secondary" :to="bankPageUrl(editor.detail_url)">取消</RouterLink></div>
      </form>
      <aside class="kt-document-preview" aria-label="当前未保存内容预览"><header><span>学生视角预览</span><span>{{ draft.max_score }} 分</span></header><section><h2>{{ draft.title }}</h2><p class="kt-document-instructions">{{ draft.instructions }}</p><p class="kt-document-material" :lang="draft.content_language">{{ draft.text }}</p><template v-if="choiceQuestion"><label v-for="key in 'abcdef'.split('').filter(key => draft[`option_${key}` as keyof typeof draft])" :key="key" class="kt-document-option"><input :type="draft.question_type === 'multiple_choice' ? 'checkbox' : 'radio'" :checked="draft.question_type === 'multiple_choice' ? previewChoices.includes(key) : previewAnswer === key" @change="previewChoice(key, ($event.target as HTMLInputElement).checked)"> {{ key.toUpperCase() }} {{ draft[`option_${key}` as keyof typeof draft] }}</label></template><template v-else-if="draft.question_type === 'true_false'"><label class="kt-document-option"><input v-model="previewAnswer" type="radio" value="true">正确</label><label class="kt-document-option"><input v-model="previewAnswer" type="radio" value="false">错误</label></template><textarea v-else-if="draft.question_type === 'short_answer'" v-model="previewAnswer" aria-label="预览作答区" rows="5" /><input v-else v-model="previewAnswer" aria-label="预览作答区"></section><footer>此处仅预览当前编辑内容。正确答案与评分要点不向学生展示。</footer></aside>
    </div>
  </template>
  <p v-else role="status">正在加载题目编辑信息…</p>
</template>
