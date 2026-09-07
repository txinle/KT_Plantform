<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { ApiError } from '@/api/client'
import Alert from '@/design-system/components/Alert.vue'
import FormField from '@/design-system/components/FormField.vue'
import PageHeader from '@/design-system/components/PageHeader.vue'

import { getTeacherStandaloneQuestionBankApi, type StandaloneQuestionEditor } from './standalone-api'

const route = useRoute()
const router = useRouter()
const api = getTeacherStandaloneQuestionBankApi()
const bankId = Number(route.params.bankId)
const questionId = Number(route.params.questionId)
const editor = ref<StandaloneQuestionEditor>()
const draft = reactive({
  text: '',
  question_type: '',
  status: '',
  teacher_difficulty: '',
  absolute_tolerance: '',
  relative_tolerance: '',
  explanation: '',
  correct_answer_text: '',
  accepted_answers_text: '',
})
const options = reactive({ a: '', b: '', c: '', d: '', e: '', f: '' })
const fieldErrors = ref<Record<string, string>>({})
const error = ref('')
const saving = ref(false)
const choiceQuestion = computed(() => ['single_choice', 'multiple_choice'].includes(draft.question_type))

function setError(caught: unknown, fallback: string) {
  error.value = caught instanceof Error ? caught.message : fallback
  fieldErrors.value = caught instanceof ApiError
    ? Object.fromEntries(Object.entries(caught.fields).map(([key, value]) => [key, Array.isArray(value) ? String(value[0]) : String(value)]))
    : {}
}

onMounted(async () => {
  try {
    editor.value = await api.questionEditor(bankId, questionId)
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
    })
    Object.assign(options, question.options)
  } catch (caught) {
    setError(caught, '题目编辑信息加载失败。')
  }
})

async function save() {
  saving.value = true
  error.value = ''
  fieldErrors.value = {}
  try {
    const saved = await api.updateQuestion(bankId, questionId, {
      ...draft,
      option_a: options.a,
      option_b: options.b,
      option_c: options.c,
      option_d: options.d,
      option_e: options.e,
      option_f: options.f,
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
    <PageHeader :title="`编辑 ${editor.question.code || `题目 ${editor.question.sequence}`}`">
      <template #kicker>{{ editor.bank.name }} · 独立题库</template>
      <p>编辑题目内容、答案与选项，检查后保存。</p>
      <template #actions><RouterLink class="kt-button kt-button--secondary" :to="editor.detail_url">取消</RouterLink></template>
    </PageHeader>
    <Alert v-if="error" tone="danger">{{ error }}</Alert>
    <form class="kt-form-stack" novalidate @submit.prevent="save">
      <section class="kt-card">
        <h2>题目内容</h2>
        <FormField for-id="standalone-question-text" label="题干" :error="fieldErrors.text">
          <textarea id="standalone-question-text" v-model="draft.text" rows="5" required />
        </FormField>
        <div class="kt-form-grid">
          <FormField for-id="standalone-question-type" label="题型" :error="fieldErrors.question_type">
            <select id="standalone-question-type" v-model="draft.question_type"><option v-for="item in editor.question_types" :key="item.value" :value="item.value">{{ item.label }}</option></select>
          </FormField>
          <FormField for-id="standalone-question-status" label="题目状态" :error="fieldErrors.status">
            <select id="standalone-question-status" v-model="draft.status"><option v-for="item in editor.statuses" :key="item.value" :value="item.value">{{ item.label }}</option></select>
          </FormField>
        </div>
        <div class="kt-form-grid">
          <FormField for-id="standalone-question-difficulty" label="教师设定难度" :error="fieldErrors.teacher_difficulty"><input id="standalone-question-difficulty" v-model="draft.teacher_difficulty" inputmode="decimal"></FormField>
          <FormField for-id="standalone-question-absolute-tolerance" label="绝对误差容限" :error="fieldErrors.absolute_tolerance"><input id="standalone-question-absolute-tolerance" v-model="draft.absolute_tolerance" inputmode="decimal"></FormField>
          <FormField for-id="standalone-question-relative-tolerance" label="相对误差容限" :error="fieldErrors.relative_tolerance"><input id="standalone-question-relative-tolerance" v-model="draft.relative_tolerance" inputmode="decimal"></FormField>
        </div>
      </section>
      <section class="kt-card">
        <h2>答案与解析</h2>
        <FormField for-id="standalone-question-answer" label="正确答案" :error="fieldErrors.correct_answer_text"><input id="standalone-question-answer" v-model="draft.correct_answer_text" required></FormField>
        <FormField v-if="!choiceQuestion" for-id="standalone-question-accepted-answers" label="其他可接受答案（分号分隔）" :error="fieldErrors.accepted_answers_text"><input id="standalone-question-accepted-answers" v-model="draft.accepted_answers_text"></FormField>
        <div v-if="choiceQuestion" class="kt-form-grid">
          <FormField for-id="standalone-option-a" label="选项 A" :error="fieldErrors.option_a"><input id="standalone-option-a" v-model="options.a"></FormField>
          <FormField for-id="standalone-option-b" label="选项 B" :error="fieldErrors.option_b"><input id="standalone-option-b" v-model="options.b"></FormField>
          <FormField for-id="standalone-option-c" label="选项 C" :error="fieldErrors.option_c"><input id="standalone-option-c" v-model="options.c"></FormField>
          <FormField for-id="standalone-option-d" label="选项 D" :error="fieldErrors.option_d"><input id="standalone-option-d" v-model="options.d"></FormField>
          <FormField for-id="standalone-option-e" label="选项 E" :error="fieldErrors.option_e"><input id="standalone-option-e" v-model="options.e"></FormField>
          <FormField for-id="standalone-option-f" label="选项 F" :error="fieldErrors.option_f"><input id="standalone-option-f" v-model="options.f"></FormField>
        </div>
        <FormField for-id="standalone-question-explanation" label="解析" :error="fieldErrors.explanation"><textarea id="standalone-question-explanation" v-model="draft.explanation" rows="4" /></FormField>
      </section>
      <div class="kt-inline-actions"><button class="kt-button" type="submit" :disabled="saving">{{ saving ? '正在保存…' : '保存题目' }}</button><RouterLink class="kt-button kt-button--secondary" :to="editor.detail_url">取消</RouterLink></div>
    </form>
  </template>
  <p v-else role="status">正在加载题目编辑信息…</p>
</template>
