<script setup lang="ts">
import { computed, inject, onMounted, reactive, ref, type Ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { ApiError } from '@/api/client'
import type { components } from '@/api/generated/schema'
import Alert from '@/design-system/components/Alert.vue'
import Button from '@/design-system/components/Button.vue'
import FormField from '@/design-system/components/FormField.vue'

import { getTeacherExperimentApi, type ExperimentDefinitionInput } from './api'

const questionTypes = [
  ['single_choice', '单选题'],
  ['multiple_choice', '多选题'],
  ['numeric', '数值题'],
  ['short_answer', '简答题'],
  ['fill_blank', '填空题'],
  ['true_false', '判断题'],
] as const

const route = useRoute()
const router = useRouter()
const api = getTeacherExperimentApi()
const courseId = Number(route.params.courseId)
const experimentId = computed(() => Number(route.params.experimentId || 0))
const editing = computed(() => route.name === 'teacher-experiment-edit')
const course = inject<Ref<components['schemas']['CourseContext'] | undefined>>(
  'teacher-experiments-course',
  ref<components['schemas']['CourseContext']>(),
)
const loading = ref(true)
const saving = ref(false)
const error = ref('')
const fieldErrors = ref<Record<string, string>>({})
const questionBanks = ref<Array<{ id: number; name: string }>>([])
const courseName = computed(() => course?.value?.name || '课程')

const form = reactive({
  name: '',
  description: '',
  question_bank_id: 0,
  selection_mode: 'all' as 'all' | 'quota',
  ordering_mode: 'fixed' as 'fixed' | 'random' | 'stratified_random',
  feedback_mode: 'none' as 'none' | 'correctness' | 'full',
  time_mode: 'none' as 'none' | 'total' | 'per_question',
  total_time_limit_minutes: null as number | null,
  per_question_time_limit_seconds: null as number | null,
  resume_mode: 'resumable' as 'single_session' | 'resumable',
  confidence_mode: 'off' as 'off' | 'all' | 'sampled',
  confidence_sampling_unit: 'attempt' as 'student' | 'session' | 'attempt',
  confidence_probability: null as number | null,
  quotas: Object.fromEntries(questionTypes.map(([value]) => [value, 0])) as Record<string, number>,
})

function firstError(value: unknown): string {
  if (Array.isArray(value)) return String(value[0] || '')
  return typeof value === 'string' ? value : ''
}

function normalizeNumber(value: number | null): number | null {
  return value === null || value === undefined || value === ('' as unknown as number)
    ? null
    : Number(value)
}

function definitionInput(): ExperimentDefinitionInput {
  return {
    name: form.name.trim(),
    description: form.description.trim(),
    question_bank_id: Number(form.question_bank_id),
    selection_mode: form.selection_mode,
    question_quotas: questionTypes.map(([question_type]) => ({
      question_type,
      amount: Number(form.quotas[question_type] || 0),
    })),
    ordering_mode: form.ordering_mode,
    feedback_mode: form.feedback_mode,
    time_mode: form.time_mode,
    total_time_limit_minutes: form.time_mode === 'total'
      ? normalizeNumber(form.total_time_limit_minutes)
      : null,
    per_question_time_limit_seconds: form.time_mode === 'per_question'
      ? normalizeNumber(form.per_question_time_limit_seconds)
      : null,
    resume_mode: form.resume_mode,
    confidence_mode: form.confidence_mode,
    confidence_sampling_unit: form.confidence_sampling_unit,
    confidence_probability: form.confidence_mode === 'sampled'
      ? String(normalizeNumber(form.confidence_probability) ?? '')
      : null,
  }
}

async function load() {
  try {
    const collection = await api.list(courseId, 1)
    questionBanks.value = collection.summary.question_banks
    if (!editing.value && questionBanks.value.length === 1) {
      form.question_bank_id = questionBanks.value[0]?.id || 0
    }
    if (editing.value) {
      const experiment = await api.detail(courseId, experimentId.value)
      form.name = experiment.name
      form.description = experiment.description || ''
      form.question_bank_id = experiment.question_bank.id
      form.selection_mode = experiment.selection_mode || 'all'
      form.ordering_mode = experiment.ordering_mode || 'fixed'
      form.feedback_mode = experiment.feedback_mode || 'none'
      form.time_mode = experiment.time_mode || 'none'
      form.total_time_limit_minutes = experiment.total_time_limit_minutes || null
      form.per_question_time_limit_seconds = experiment.per_question_time_limit_seconds || null
      form.resume_mode = experiment.resume_mode || 'resumable'
      form.confidence_mode = experiment.confidence_mode || 'off'
      form.confidence_sampling_unit = experiment.confidence_sampling_unit || 'attempt'
      form.confidence_probability = experiment.confidence_probability
        ? Number(experiment.confidence_probability)
        : null
      for (const quota of experiment.question_quotas || []) {
        form.quotas[quota.question_type] = quota.amount
      }
    }
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '实验配置加载失败。'
  } finally {
    loading.value = false
  }
}

async function submit() {
  saving.value = true
  error.value = ''
  fieldErrors.value = {}
  try {
    const saved = editing.value
      ? await api.update(courseId, experimentId.value, definitionInput())
      : await api.create(courseId, definitionInput())
    await router.push(`/teacher/courses/${courseId}/experiments/${saved.id}/`)
  } catch (caught) {
    if (caught instanceof ApiError) {
      fieldErrors.value = Object.fromEntries(
        Object.entries(caught.fields).map(([key, value]) => [key, firstError(value)]),
      )
    }
    error.value = caught instanceof Error ? caught.message : '实验保存失败。'
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <header class="kt-experiment-page-header">
    <div class="kt-experiment-page-header__copy">
      <nav class="kt-experiment-breadcrumb" aria-label="层级目录">
        <a href="/teacher/courses/">我的课程</a><span aria-hidden="true">/</span>
        <a :href="`/teacher/courses/${courseId}/`">{{ courseName }}</a><span aria-hidden="true">/</span>
        <a :href="`/teacher/courses/${courseId}/experiments/`">实验</a><span aria-hidden="true">/</span>
        <span aria-current="page">{{ editing ? (form.name || '编辑实验定义') : '新建实验' }}</span>
      </nav>
      <div class="kt-experiment-title-row"><h1>{{ editing ? '编辑实验定义' : '创建实验' }}</h1></div>
      <p>定义题目选择、顺序、反馈、时限与置信度采集策略。首次正式发布后这些设置将被冻结。</p>
    </div>
  </header>

  <nav class="kt-experiment-tabs kt-experiment-tabs--static" aria-label="实验流程导航">
    <span aria-current="page" class="is-active">实验定义</span><span aria-disabled="true">班级发布</span><span aria-disabled="true">采集状态</span>
  </nav>

  <p v-if="loading" role="status">正在加载实验配置…</p>
  <Alert v-else-if="error" tone="danger">{{ error }}</Alert>

  <form v-if="!loading" class="kt-form-stack" novalidate @submit.prevent="submit">
    <section class="kt-card kt-numbered-form-section" aria-labelledby="definition-basic">
      <div class="kt-numbered-form-section__heading"><span aria-hidden="true">1</span><h2 id="definition-basic">基本信息</h2></div>
      <div class="kt-numbered-form-section__content">
        <FormField class="kt-compact-form-row" for-id="experiment-name" label="实验名称" :error="fieldErrors.name"><input id="experiment-name" v-model="form.name" required maxlength="180"></FormField>
        <FormField class="kt-compact-form-row" for-id="experiment-description" label="实验说明" :error="fieldErrors.description"><textarea id="experiment-description" v-model="form.description" rows="3" /></FormField>
        <FormField class="kt-compact-form-row" for-id="experiment-bank" label="题库" :error="fieldErrors.question_bank_id"><select id="experiment-bank" v-model.number="form.question_bank_id" required><option :value="0" disabled>请选择题库</option><option v-for="bank in questionBanks" :key="bank.id" :value="bank.id">{{ bank.name }}</option></select></FormField>
      </div>
    </section>

    <section class="kt-card kt-numbered-form-section" aria-labelledby="definition-selection">
      <div class="kt-numbered-form-section__heading"><span aria-hidden="true">2</span><h2 id="definition-selection">题目与呈现</h2></div>
      <div class="kt-numbered-form-section__content">
        <fieldset class="kt-radio-group kt-compact-form-row"><legend>抽题方式</legend><div class="kt-radio-options"><label><input v-model="form.selection_mode" name="selection_mode" type="radio" value="all"><span>全量题池<small>每位参与者使用题库中的全部可用题目</small></span></label><label><input v-model="form.selection_mode" name="selection_mode" type="radio" value="quota"><span>按题型配额抽题<small>由下方题型配额确定每位参与者的题目数量</small></span></label></div><p v-if="fieldErrors.selection_mode" class="kt-field__error" role="alert">{{ fieldErrors.selection_mode }}</p></fieldset>
        <fieldset class="kt-radio-group kt-compact-form-row"><legend>题目顺序</legend><div class="kt-radio-options"><label><input v-model="form.ordering_mode" name="ordering_mode" type="radio" value="fixed"><span>固定顺序</span></label><label><input v-model="form.ordering_mode" name="ordering_mode" type="radio" value="random"><span>随机顺序</span></label><label><input v-model="form.ordering_mode" name="ordering_mode" type="radio" value="stratified_random"><span>分层随机</span></label></div><p v-if="fieldErrors.ordering_mode" class="kt-field__error" role="alert">{{ fieldErrors.ordering_mode }}</p></fieldset>
        <fieldset class="kt-radio-group kt-compact-form-row"><legend>反馈</legend><div class="kt-radio-options"><label><input v-model="form.feedback_mode" name="feedback_mode" type="radio" value="none"><span>仅显示已提交</span></label><label><input v-model="form.feedback_mode" name="feedback_mode" type="radio" value="correctness"><span>显示正误</span></label><label><input v-model="form.feedback_mode" name="feedback_mode" type="radio" value="full"><span>显示正误、答案与解析</span></label></div><p v-if="fieldErrors.feedback_mode" class="kt-field__error" role="alert">{{ fieldErrors.feedback_mode }}</p></fieldset>
        <fieldset v-if="form.selection_mode === 'quota'" class="kt-fieldset kt-compact-form-row"><legend>题型配额</legend><div class="kt-quota-grid"><label v-for="([value, label]) in questionTypes" :key="value"><span>{{ label }}</span><input v-model.number="form.quotas[value]" type="number" min="0"></label></div><p v-if="fieldErrors.question_quotas" class="kt-field__error" role="alert">{{ fieldErrors.question_quotas }}</p></fieldset>
      </div>
    </section>

    <section class="kt-card kt-numbered-form-section" aria-labelledby="definition-runtime">
      <div class="kt-numbered-form-section__heading"><span aria-hidden="true">3</span><h2 id="definition-runtime">作答规则</h2></div>
      <div class="kt-numbered-form-section__content">
        <fieldset class="kt-radio-group kt-compact-form-row"><legend>时限</legend><div class="kt-radio-options"><label><input v-model="form.time_mode" name="time_mode" type="radio" value="none"><span>无时限</span></label><label><input v-model="form.time_mode" name="time_mode" type="radio" value="total"><span>实验总时限</span></label><label><input v-model="form.time_mode" name="time_mode" type="radio" value="per_question"><span>单题时限</span></label></div><p v-if="fieldErrors.time_mode" class="kt-field__error" role="alert">{{ fieldErrors.time_mode }}</p></fieldset>
        <div class="kt-form-grid"><FormField class="kt-compact-form-row" for-id="experiment-total-time" label="总时限（分钟）" :error="fieldErrors.total_time_limit_minutes"><input id="experiment-total-time" v-model.number="form.total_time_limit_minutes" type="number" min="1" :disabled="form.time_mode !== 'total'"></FormField><FormField class="kt-compact-form-row" for-id="experiment-question-time" label="单题限时（秒）" :error="fieldErrors.per_question_time_limit_seconds"><input id="experiment-question-time" v-model.number="form.per_question_time_limit_seconds" type="number" min="1" :disabled="form.time_mode !== 'per_question'"></FormField></div>
        <fieldset class="kt-radio-group kt-compact-form-row"><legend>中断后继续</legend><div class="kt-radio-options"><label><input v-model="form.resume_mode" name="resume_mode" type="radio" value="resumable"><span>允许断点续答</span></label><label><input v-model="form.resume_mode" name="resume_mode" type="radio" value="single_session"><span>必须一次完成</span></label></div><p v-if="fieldErrors.resume_mode" class="kt-field__error" role="alert">{{ fieldErrors.resume_mode }}</p></fieldset>
      </div>
    </section>

    <section class="kt-card kt-numbered-form-section" aria-labelledby="definition-collection">
      <div class="kt-numbered-form-section__heading"><span aria-hidden="true">4</span><h2 id="definition-collection">研究采集</h2></div>
      <div class="kt-numbered-form-section__content">
        <fieldset class="kt-radio-group kt-compact-form-row"><legend>作答信心采集</legend><div class="kt-radio-options"><label><input v-model="form.confidence_mode" name="confidence_mode" type="radio" value="off"><span>不采集</span></label><label><input v-model="form.confidence_mode" name="confidence_mode" type="radio" value="all"><span>全部采集</span></label><label><input v-model="form.confidence_mode" name="confidence_mode" type="radio" value="sampled"><span>抽样采集</span></label></div><p v-if="fieldErrors.confidence_mode" class="kt-field__error" role="alert">{{ fieldErrors.confidence_mode }}</p></fieldset>
        <div v-if="form.confidence_mode !== 'off'" class="kt-form-grid"><fieldset class="kt-radio-group kt-compact-form-row"><legend>采集单元</legend><div class="kt-radio-options"><label><input v-model="form.confidence_sampling_unit" name="confidence_sampling_unit" type="radio" value="student"><span>学生</span></label><label><input v-model="form.confidence_sampling_unit" name="confidence_sampling_unit" type="radio" value="session"><span>会话</span></label><label><input v-model="form.confidence_sampling_unit" name="confidence_sampling_unit" type="radio" value="attempt"><span>作答</span></label></div><p v-if="fieldErrors.confidence_sampling_unit" class="kt-field__error" role="alert">{{ fieldErrors.confidence_sampling_unit }}</p></fieldset><FormField v-if="form.confidence_mode === 'sampled'" class="kt-compact-form-row" for-id="experiment-confidence-probability" label="抽样概率（0–1）" :error="fieldErrors.confidence_probability"><input id="experiment-confidence-probability" v-model.number="form.confidence_probability" type="number" min="0" max="1" step="0.001"></FormField></div>
        <Alert tone="info">系统会在正式发布时自动生成版本快照与随机化标识，用于审计与复现。</Alert>
      </div>
    </section>

    <div class="kt-form-actions kt-experiment-form-actions">
      <a class="kt-button kt-button--secondary" :href="editing ? `/teacher/courses/${courseId}/experiments/${experimentId}/` : `/teacher/courses/${courseId}/experiments/`">{{ editing ? '返回详情' : '取消' }}</a>
      <Button type="submit" :disabled="saving">{{ saving ? '正在保存…' : (editing ? '保存更改' : '保存实验') }}</Button>
    </div>
  </form>
</template>
