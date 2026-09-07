<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { ApiError } from '@/api/client'
import Alert from '@/design-system/components/Alert.vue'
import FormField from '@/design-system/components/FormField.vue'
import PageHeader from '@/design-system/components/PageHeader.vue'
import KcMappingEditor from './KcMappingEditor.vue'
import Icon from '@/design-system/Icon.vue'

import { getTeacherBankManagementApi, type QuestionEditor } from './api'

type MappingNode = { kc_code: string; parent_kc_code: string | null; order: number }

const route = useRoute()
const router = useRouter()
const api = getTeacherBankManagementApi()
const courseId = Number(route.params.courseId)
const bankId = Number(route.params.bankId)
const questionId = Number(route.params.questionId)
const editor = ref<QuestionEditor>()
const error = ref('')
const fieldErrors = ref<Record<string, string>>({})
const saving = ref(false)
const moreOptionsOpen = ref(false)
const answersOpen = ref(false)
const draft = reactive({
  text: '', question_type: '', status: '', teacher_difficulty: '', absolute_tolerance: '', relative_tolerance: '', explanation: '', correct_answer_text: '', accepted_answers_text: '', option_a: '', option_b: '', option_c: '', option_d: '', option_e: '', option_f: '',
})
const mapping = reactive<{ root_kc_code: string; nodes: MappingNode[] }>({ root_kc_code: '', nodes: [] })

const choiceQuestion = computed(() => editor.value?.question_types.some((item) => item.value === draft.question_type && ['single_choice', 'multiple_choice'].includes(item.value)) ?? false)

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
    Object.assign(draft, {
      text: question.text,
      question_type: question.question_type,
      status: question.status,
      teacher_difficulty: question.teacher_difficulty,
      absolute_tolerance: question.absolute_tolerance,
      relative_tolerance: question.relative_tolerance,
      explanation: question.explanation,
      correct_answer_text: question.correct_answer_text,
      accepted_answers_text: question.accepted_answers_text,
      ...Object.fromEntries(Object.entries(question.options).map(([key, value]) => [`option_${key}`, value])),
    })
    moreOptionsOpen.value = Boolean(draft.option_e || draft.option_f)
    mapping.root_kc_code = question.kc_mapping.root_kc_code || ''
    mapping.nodes = question.kc_mapping.nodes.map((node) => ({ ...node }))
  } catch (caught) {
    setError(caught, '题目编辑信息加载失败。')
  }
})

async function save() {
  saving.value = true
  error.value = ''
  fieldErrors.value = {}
  try {
    const saved = await api.updateQuestion(courseId, bankId, questionId, {
      ...draft,
      kc_mapping_json: JSON.stringify({
        schema_version: 'question-kc-tree-v2',
        root_kc_code: mapping.root_kc_code || null,
        nodes: mapping.nodes,
      }),
    })
    await router.push(saved.detail_url)
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
    <PageHeader class="kt-question-editor-page-header" :title="`编辑 ${editor.question.code || `题目 ${editor.question.sequence}`}`"><template #kicker>{{ editor.bank.name }}</template><p>编辑题目、答案与知识点映射，检查后保存。</p><template #actions><RouterLink class="kt-button kt-button--secondary" :to="editor.detail_url">返回题库</RouterLink></template></PageHeader>
    <Alert v-if="error" tone="danger">{{ error }}</Alert>
    <form class="kt-form-stack kt-question-editor" novalidate @submit.prevent="save">
      <section class="kt-card" aria-labelledby="question-basics-title">
        <div class="kt-question-editor-heading"><Icon name="definition" :size="24" /><div><h2 id="question-basics-title">基础信息</h2><p>设置题目的基本属性。</p></div></div>
        <div class="kt-form-grid">
          <FormField for-id="question-type" label="题型" :error="fieldErrors.question_type"><select id="question-type" v-model="draft.question_type"><option v-for="item in editor.question_types" :key="item.value" :value="item.value">{{ item.label }}</option></select></FormField>
          <FormField for-id="question-status" label="题目状态" :error="fieldErrors.status"><select id="question-status" v-model="draft.status"><option v-for="item in editor.statuses" :key="item.value" :value="item.value">{{ item.label }}</option></select></FormField>
        </div>
      </section>
      <section class="kt-card" aria-labelledby="question-content-title">
        <div class="kt-question-editor-heading"><Icon name="definition" :size="24" /><div><h2 id="question-content-title">题目内容</h2><p>输入题目题干及选项。</p></div></div>
        <FormField for-id="question-text" label="题干" :error="fieldErrors.text"><textarea id="question-text" v-model="draft.text" rows="3" required /></FormField>
        <template v-if="choiceQuestion">
          <div class="kt-form-grid"><FormField v-for="key in ['a', 'b', 'c', 'd']" :key="key" :for-id="`option-${key}`" :label="`选项 ${key.toUpperCase()}`" :error="fieldErrors[`option_${key}`]"><input :id="`option-${key}`" v-model="draft[`option_${key}` as keyof typeof draft]"></FormField></div>
          <details class="kt-question-editor-more" :open="moreOptionsOpen" @toggle="moreOptionsOpen = ($event.target as HTMLDetailsElement).open"><summary>显示更多选项（E、F）</summary><div class="kt-form-grid"><FormField v-for="key in ['e', 'f']" :key="key" :for-id="`option-${key}`" :label="`选项 ${key.toUpperCase()}`" :error="fieldErrors[`option_${key}`]"><input :id="`option-${key}`" v-model="draft[`option_${key}` as keyof typeof draft]"></FormField></div></details>
        </template>
      </section>
      <section class="kt-card" aria-labelledby="question-answer-title">
        <button type="button" class="kt-editor-section-toggle" data-answer-toggle :aria-expanded="answersOpen" aria-controls="question-answer-fields" @click="answersOpen = !answersOpen"><h2 id="question-answer-title">答案与解析</h2><span class="kt-editor-section-summary">正确答案 {{ draft.correct_answer_text || '未设置' }} · 难度 {{ draft.teacher_difficulty || '未设置' }} · {{ draft.explanation ? '已填写解析' : '暂无解析' }}</span><span>{{ answersOpen ? '收起' : '点击展开' }}</span><Icon name="chevron-down" :size="16" :class="{ 'is-expanded': answersOpen }" /></button>
        <div v-show="answersOpen" id="question-answer-fields" class="kt-editor-section-body">
          <div class="kt-form-grid">
            <FormField for-id="question-answer" label="正确答案" :error="fieldErrors.correct_answer_text"><input id="question-answer" v-model="draft.correct_answer_text" required></FormField>
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
      <KcMappingEditor :model-value="mapping" :concepts="editor.concepts" :error="fieldErrors.kc_mapping_json" @update:model-value="Object.assign(mapping, $event)" />
      <div class="kt-inline-actions"><button class="kt-button" type="submit" :disabled="saving">{{ saving ? '正在保存…' : '保存题目' }}</button><RouterLink class="kt-button kt-button--secondary" :to="editor.detail_url">取消</RouterLink></div>
    </form>
  </template>
  <p v-else role="status">正在加载题目编辑信息…</p>
</template>
