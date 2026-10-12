<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { ApiError } from '@/api/client'
import Alert from '@/design-system/components/Alert.vue'
import Button from '@/design-system/components/Button.vue'
import FormField from '@/design-system/components/FormField.vue'

import {
  getStandaloneExperimentApi,
  type StandaloneChoices,
  type StandaloneExperiment,
  type StandaloneExperimentInput,
} from './standalone-api'

const route = useRoute()
const router = useRouter()
const api = getStandaloneExperimentApi()
const experimentId = computed(() => Number(route.params.experimentId || 0))
const editing = computed(() => route.name === 'teacher-standalone-experiment-edit')
const loading = ref(true)
const initialized = ref(false)
const saving = ref(false)
const error = ref('')
const fieldErrors = ref<Record<string, string>>({})
const questionBanks = ref<Array<{ id: number; name: string }>>([])
const choices = ref<StandaloneChoices>()

const form = reactive({
  name: '',
  description: '',
  question_bank_id: 0,
  selection_mode: 'all' as 'all' | 'quota',
  question_quotas: {} as Record<string, number>,
  ordering_mode: 'fixed' as 'fixed' | 'random' | 'stratified_random',
  feedback_mode: 'none' as 'none' | 'correctness' | 'full',
  feedback_release_policy: 'after_personal_submission' as 'after_personal_submission' | 'after_assignment_deadline' | 'manual_release',
  time_mode: 'none' as 'none' | 'total' | 'per_question',
  total_time_limit_minutes: null as number | null,
  per_question_time_limit_seconds: null as number | null,
  resume_mode: 'resumable' as 'single_session' | 'resumable',
  confidence_mode: 'off' as 'off' | 'all' | 'sampled',
  confidence_sampling_unit: 'attempt' as 'student' | 'session' | 'attempt',
  confidence_probability: null as number | null,
  confidence_randomization_seed: '',
  confidence_assignment_version: 'v1',
  confidence_scale_version: 'v2',
  confidence_prompt_version: 'v1',
  heartbeat_interval_seconds: 30,
})

const quotaTotal = computed(() => Object.values(form.question_quotas).reduce(
  (sum, amount) => sum + Number(amount || 0),
  0,
))

function firstError(value: unknown): string {
  if (Array.isArray(value)) return String(value[0] || '')
  return typeof value === 'string' ? value : ''
}

function populate(experiment: StandaloneExperiment, quotaRows: Array<{ question_type: string; amount: number }>) {
  form.name = experiment.name
  form.description = experiment.description
  form.question_bank_id = experiment.question_bank.id
  form.selection_mode = experiment.selection_mode
  form.ordering_mode = experiment.ordering_mode as typeof form.ordering_mode
  form.feedback_mode = experiment.feedback_mode as typeof form.feedback_mode
  form.feedback_release_policy = experiment.feedback_release_policy as typeof form.feedback_release_policy
  form.time_mode = experiment.time_mode as typeof form.time_mode
  form.total_time_limit_minutes = experiment.total_time_limit_minutes
  form.per_question_time_limit_seconds = experiment.per_question_time_limit_seconds
  form.resume_mode = experiment.resume_mode as typeof form.resume_mode
  form.confidence_mode = experiment.confidence_mode as typeof form.confidence_mode
  form.confidence_sampling_unit = experiment.confidence_sampling_unit as typeof form.confidence_sampling_unit
  form.confidence_probability = experiment.confidence_probability === null
    ? null
    : Number(experiment.confidence_probability)
  form.confidence_randomization_seed = experiment.confidence_randomization_seed
  form.confidence_assignment_version = experiment.confidence_assignment_version
  form.confidence_scale_version = experiment.confidence_scale_version
  form.confidence_prompt_version = experiment.confidence_prompt_version
  form.heartbeat_interval_seconds = experiment.heartbeat_interval_seconds
  form.question_quotas = Object.fromEntries(
    quotaRows.map((row) => [row.question_type, row.amount]),
  )
}

function definitionInput(): StandaloneExperimentInput {
  return {
    name: form.name,
    description: form.description,
    question_bank_id: Number(form.question_bank_id),
    selection_mode: form.selection_mode,
    question_quotas: (choices.value?.question_types || []).map((type) => ({
      question_type: type.value,
      amount: Number(form.question_quotas[type.value] || 0),
    })),
    ordering_mode: form.ordering_mode,
    feedback_mode: form.feedback_mode,
    feedback_release_policy: form.feedback_release_policy,
    time_mode: form.time_mode,
    total_time_limit_minutes: form.time_mode === 'total' ? form.total_time_limit_minutes : null,
    per_question_time_limit_seconds: form.time_mode === 'per_question'
      ? form.per_question_time_limit_seconds
      : null,
    resume_mode: form.resume_mode,
    confidence_mode: form.confidence_mode,
    confidence_sampling_unit: form.confidence_sampling_unit,
    confidence_probability: form.confidence_mode === 'sampled' ? form.confidence_probability : null,
    confidence_randomization_seed: form.confidence_randomization_seed,
    confidence_assignment_version: form.confidence_assignment_version,
    confidence_scale_version: form.confidence_scale_version,
    confidence_prompt_version: form.confidence_prompt_version,
    heartbeat_interval_seconds: Number(form.heartbeat_interval_seconds),
  }
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    if (editing.value) {
      const detail = await api.detail(experimentId.value, 'edit')
      if (detail.kind === 'canonical') {
        await router.replace(detail.canonical_url)
        return
      }
      questionBanks.value = detail.question_banks
      choices.value = detail.choices
      populate(detail.experiment, detail.selection.quota_rows)
    } else {
      const collection = await api.collection()
      if (collection.kind === 'canonical') {
        await router.replace(collection.canonical_url)
        return
      }
      questionBanks.value = collection.question_banks
      choices.value = collection.choices
    }
    initialized.value = true
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
    const response = editing.value
      ? await api.update(experimentId.value, definitionInput())
      : await api.create(definitionInput())
    if (response.kind === 'canonical') {
      await router.replace(response.canonical_url)
      return
    }
    await router.push(`/teacher/experiments/${response.experiment.id}/`)
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
      <div class="kt-experiment-title-row"><h1>{{ editing ? '编辑实验配置' : '创建实验' }}</h1></div>
      <p>发布前可调整配置；发布后，题目、配置和参与者将冻结为研究快照。</p>
    </div>
  </header>

  <p v-if="loading" role="status">正在加载实验配置…</p>
  <Alert v-else-if="error && !initialized" tone="danger">{{ error }}</Alert>

  <form v-else-if="initialized" class="kt-form-stack" novalidate @submit.prevent="submit">
    <Alert v-if="error" tone="danger">{{ error }}</Alert>
    <section class="kt-numbered-form-section" aria-labelledby="standalone-definition-basic">
      <div class="kt-numbered-form-section__heading"><span aria-hidden="true">1</span><h2 id="standalone-definition-basic">内容</h2></div>
      <div class="kt-card kt-numbered-form-section__content kt-step-one-grid">
        <FormField for-id="standalone-experiment-name" label="实验名称" :error="fieldErrors.name"><input id="standalone-experiment-name" v-model="form.name" required maxlength="180"></FormField>
        <FormField for-id="standalone-experiment-bank" label="题库" :error="fieldErrors.question_bank_id"><select id="standalone-experiment-bank" v-model.number="form.question_bank_id" required><option :value="0" disabled>请选择题库</option><option v-for="bank in questionBanks" :key="bank.id" :value="bank.id">{{ bank.name }}</option></select></FormField>
        <FormField for-id="standalone-experiment-description" label="实验说明（可选）" :error="fieldErrors.description"><textarea id="standalone-experiment-description" v-model="form.description" rows="3" /></FormField>
      </div>
    </section>

    <section class="kt-numbered-form-section" aria-labelledby="standalone-definition-selection">
      <div class="kt-numbered-form-section__heading"><span aria-hidden="true">2</span><h2 id="standalone-definition-selection">选题与反馈</h2></div>
      <div class="kt-card kt-numbered-form-section__content">
        <fieldset class="kt-radio-group kt-compact-form-row"><legend>选题方式</legend><div class="kt-radio-options"><label v-for="choice in choices?.selection_modes || []" :key="choice.value"><input v-model="form.selection_mode" name="selection_mode" type="radio" :value="choice.value"><span>{{ choice.label }}</span></label></div><p v-if="fieldErrors.selection_mode" class="kt-field__error" role="alert">{{ fieldErrors.selection_mode }}</p></fieldset>
        <div v-if="form.selection_mode === 'quota'" class="kt-quota-panel kt-compact-form-row">
          <strong class="kt-config-label">题型配额</strong>
          <p>每位学生按题型从发布时冻结的候选题池中独立、无放回抽题。</p>
          <table class="kt-quota-table"><thead><tr><th>题型</th><th>每生题数</th></tr></thead><tbody><tr v-for="type in choices?.question_types || []" :key="type.value"><td>{{ type.label }}</td><td><input v-model.number="form.question_quotas[type.value]" :aria-label="`${type.label}数量`" type="number" min="0"></td></tr></tbody><tfoot><tr><td>合计</td><td><strong>{{ quotaTotal }}</strong></td></tr></tfoot></table>
          <p v-if="fieldErrors.question_quotas" class="kt-field__error" role="alert">{{ fieldErrors.question_quotas }}</p>
        </div>
        <fieldset class="kt-radio-group kt-compact-form-row"><legend>题目顺序</legend><div class="kt-radio-options"><label v-for="choice in choices?.ordering_modes || []" :key="choice.value"><input v-model="form.ordering_mode" name="ordering_mode" type="radio" :value="choice.value"><span>{{ choice.label }}</span></label></div></fieldset>
        <fieldset class="kt-radio-group kt-compact-form-row"><legend>反馈</legend><div class="kt-radio-options"><label v-for="choice in choices?.feedback_modes || []" :key="choice.value"><input v-model="form.feedback_mode" name="feedback_mode" type="radio" :value="choice.value"><span>{{ choice.label }}</span></label></div></fieldset>
        <fieldset class="kt-radio-group kt-compact-form-row"><legend>反馈发布时间</legend><div class="kt-radio-options"><label v-for="choice in choices?.feedback_release_policies || []" :key="choice.value"><input v-model="form.feedback_release_policy" name="feedback_release_policy" type="radio" :value="choice.value"><span>{{ choice.label }}</span></label></div></fieldset>
      </div>
    </section>

    <section class="kt-numbered-form-section" aria-labelledby="standalone-definition-runtime">
      <div class="kt-numbered-form-section__heading"><span aria-hidden="true">3</span><h2 id="standalone-definition-runtime">时间与续答</h2></div>
      <div class="kt-card kt-numbered-form-section__content">
        <fieldset class="kt-radio-group kt-compact-form-row"><legend>时间模式</legend><div class="kt-radio-options"><label v-for="choice in choices?.time_modes || []" :key="choice.value"><input v-model="form.time_mode" name="time_mode" type="radio" :value="choice.value"><span>{{ choice.label }}</span></label></div></fieldset>
        <div class="kt-form-grid"><FormField for-id="standalone-total-time" label="实验总时限（分钟）" :error="fieldErrors.total_time_limit_minutes"><input id="standalone-total-time" v-model.number="form.total_time_limit_minutes" type="number" min="1" :disabled="form.time_mode !== 'total'"></FormField><FormField for-id="standalone-question-time" label="单题时限（秒）" :error="fieldErrors.per_question_time_limit_seconds"><input id="standalone-question-time" v-model.number="form.per_question_time_limit_seconds" type="number" min="1" :disabled="form.time_mode !== 'per_question'"></FormField></div>
        <fieldset class="kt-radio-group kt-compact-form-row"><legend>断点续答</legend><div class="kt-radio-options"><label v-for="choice in choices?.resume_modes || []" :key="choice.value"><input v-model="form.resume_mode" name="resume_mode" type="radio" :value="choice.value"><span>{{ choice.label }}</span></label></div></fieldset>
      </div>
    </section>

    <section class="kt-numbered-form-section" aria-labelledby="standalone-definition-research">
      <div class="kt-numbered-form-section__heading"><span aria-hidden="true">4</span><h2 id="standalone-definition-research">研究过程采集</h2></div>
      <div class="kt-card kt-numbered-form-section__content">
        <fieldset class="kt-radio-group kt-compact-form-row"><legend>作答信心采集</legend><div class="kt-radio-options"><label v-for="choice in choices?.confidence_modes || []" :key="choice.value"><input v-model="form.confidence_mode" name="confidence_mode" type="radio" :value="choice.value"><span>{{ choice.label }}</span></label></div></fieldset>
        <div v-if="form.confidence_mode !== 'off'" class="kt-form-grid"><fieldset class="kt-radio-group"><legend>抽样单位</legend><div class="kt-radio-options"><label v-for="choice in choices?.confidence_sampling_units || []" :key="choice.value"><input v-model="form.confidence_sampling_unit" name="confidence_sampling_unit" type="radio" :value="choice.value"><span>{{ choice.label }}</span></label></div></fieldset><FormField v-if="form.confidence_mode === 'sampled'" for-id="standalone-confidence-probability" label="抽样概率（0–1）" :error="fieldErrors.confidence_probability"><input id="standalone-confidence-probability" v-model.number="form.confidence_probability" type="number" min="0" max="1" step="0.001"></FormField></div>
        <FormField for-id="standalone-heartbeat" label="活跃心跳间隔（秒）" :error="fieldErrors.heartbeat_interval_seconds"><input id="standalone-heartbeat" v-model.number="form.heartbeat_interval_seconds" type="number" min="15" max="120"></FormField>
        <details class="kt-collapsible-step"><summary>版本与可复现配置</summary><div class="kt-collapsible-step__body kt-form-grid"><FormField for-id="standalone-randomization-seed" label="随机分配种子（可选）"><input id="standalone-randomization-seed" v-model="form.confidence_randomization_seed" maxlength="128"></FormField><FormField for-id="standalone-assignment-version" label="分配规则版本"><input id="standalone-assignment-version" v-model="form.confidence_assignment_version" maxlength="64"></FormField><FormField for-id="standalone-scale-version" label="量表版本"><select id="standalone-scale-version" v-model="form.confidence_scale_version"><option value="v1">v1（10% 步进）</option><option value="v2">v2（1% 步进）</option></select></FormField><FormField for-id="standalone-prompt-version" label="提示版本"><input id="standalone-prompt-version" v-model="form.confidence_prompt_version" maxlength="64"></FormField></div></details>
      </div>
    </section>

    <div class="kt-form-actions kt-experiment-form-actions">
      <RouterLink class="kt-button kt-button--secondary" :to="editing ? `/teacher/experiments/${experimentId}/` : '/teacher/experiments/'">取消</RouterLink>
      <Button type="submit" :disabled="saving">{{ saving ? '正在保存…' : (editing ? '保存实验配置' : '创建并载入题库题目') }}</Button>
    </div>
  </form>
</template>
