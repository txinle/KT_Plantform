<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { ApiError } from '@/api/client'
import type { components } from '@/api/generated/schema'
import Alert from '@/design-system/components/Alert.vue'
import Button from '@/design-system/components/Button.vue'
import FormField from '@/design-system/components/FormField.vue'
import ResearchToolsEditor from './ResearchToolsEditor.vue'
import { emptyResearchTools, type ResearchTools } from './research-tools'

import {
  getTeacherExperimentApi,
  type ExperimentDefinitionInput,
  type ExperimentQuestionType,
  type SamplingInventory,
  type TeacherExperimentReadiness,
} from './api'

const QUESTION_TYPES: Array<{ value: ExperimentQuestionType; label: string }> = [
  { value: 'single_choice', label: '单选题' },
  { value: 'multiple_choice', label: '多选题' },
  { value: 'numeric', label: '数值题' },
  { value: 'short_answer', label: '简答题' },
  { value: 'fill_blank', label: '填空题' },
  { value: 'true_false', label: '判断题' },
]

const route = useRoute()
const router = useRouter()
const api = getTeacherExperimentApi()
const courseId = Number(route.params.courseId)
const experimentId = computed(() => Number(route.params.experimentId || 0))
const editing = computed(() => route.name === 'teacher-experiment-edit')
const loading = ref(true)
const researchTools = ref<ResearchTools>(emptyResearchTools())
const saving = ref(false)
const error = ref('')
const fieldErrors = ref<Record<string, string>>({})
const questionBanks = ref<Array<{ id: number; name: string }>>([])
const concepts = ref<components['schemas']['CourseConceptSummary'][]>([])
const readiness = ref<TeacherExperimentReadiness>()
const legacySampling = ref(false)
const legacyQuotaTotal = ref(0)
const conversionWarning = ref('')
const samplingInventory = ref<SamplingInventory>()
const inventoryLoading = ref(false)
const inventoryError = ref('')
const savedSamplingConfiguration = ref('')
let inventoryRequest = 0

function convertLegacySampling() {
  legacySampling.value = false
  form.sampling_mode = 'total_count'
  form.sampling_total_count = legacyQuotaTotal.value || readiness.value?.candidate_count || 1
  for (const concept of concepts.value) {
    form.selected_kcs[concept.id] = concept.status === 'active'
  }
  conversionWarning.value = '已转为“总题数 + 全部有效 KC”。旧版题型配额不会保留；保存前请核对总题数和匹配层级。'
}

const form = reactive({
  name: '',
  description: '',
  question_bank_id: 0,
  sampling_mode: 'total_count' as 'total_count' | 'kc_quota',
  sampling_total_count: 1 as number | null,
  sampling_match_scope: 'any_level' as 'primary' | 'any_level',
  selected_kcs: {} as Record<number, boolean>,
  kc_quotas: {} as Record<number, number>,
  kc_match_scopes: {} as Record<number, 'primary' | 'any_level'>,
  selected_question_types: {} as Record<ExperimentQuestionType, boolean>,
  type_quotas: {} as Record<ExperimentQuestionType, number>,
  ordering_mode: 'fixed' as 'fixed' | 'random' | 'stratified_random',
  feedback_mode: 'full' as 'none' | 'correctness' | 'full',
  feedback_release_policy: 'after_personal_submission' as 'after_personal_submission' | 'after_assignment_deadline' | 'manual_release',
  time_mode: 'none' as 'none' | 'total' | 'per_question',
  total_time_limit_minutes: null as number | null,
  per_question_time_limit_seconds: null as number | null,
  resume_mode: 'resumable' as 'single_session' | 'resumable',
  confidence_mode: 'off' as 'off' | 'all' | 'sampled',
  confidence_sampling_unit: 'attempt' as 'student' | 'session' | 'attempt',
  confidence_probability: null as number | null,
})

const quotaTotal = computed(() => concepts.value.reduce((total, concept) => (
  form.selected_kcs[concept.id] ? total + Number(form.kc_quotas[concept.id] || 0) : total
), 0))
const selectedConcepts = computed(() => concepts.value.filter(
  (concept) => form.selected_kcs[concept.id],
))
const samplingConfiguration = computed(() => JSON.stringify({
  question_bank_id: Number(form.question_bank_id),
  sampling: definitionInput().sampling,
}))
const samplingChanged = computed(() => Boolean(savedSamplingConfiguration.value)
  && samplingConfiguration.value !== savedSamplingConfiguration.value)
const selectedQuestionTypes = computed(() => QUESTION_TYPES.filter(
  (questionType) => form.selected_question_types[questionType.value],
))
const typeQuotaTotal = computed(() => selectedQuestionTypes.value.reduce(
  (total, questionType) => total + Number(form.type_quotas[questionType.value] || 0),
  0,
))
const typeQuotaError = computed(() => {
  if (form.sampling_mode !== 'total_count' || !selectedQuestionTypes.value.length) return ''
  const totalCount = Number(form.sampling_total_count || 0)
  if (typeQuotaTotal.value !== totalCount) return '题型数量合计必须等于总题数。'
  if (selectedQuestionTypes.value.some((item) => {
    const amount = Number(form.type_quotas[item.value] || 0)
    return !Number.isInteger(amount) || amount < 1
  })) {
    return '已选题型的数量必须为正整数。'
  }
  if (inventoryLoading.value) return '正在校验题型库存…'
  if (inventoryError.value) return inventoryError.value
  const inventory = new Map(
    (samplingInventory.value?.question_types || []).map((item) => [item.question_type, item.available]),
  )
  const shortage = selectedQuestionTypes.value.find(
    (item) => Number(form.type_quotas[item.value] || 0) > Number(inventory.get(item.value) || 0),
  )
  return shortage
    ? `${shortage.label}库存不足：需要 ${form.type_quotas[shortage.value]}，可用 ${inventory.get(shortage.value) || 0}。`
    : ''
})
const typeQuotaInvalid = computed(() => Boolean(typeQuotaError.value))

function questionTypeAvailable(questionType: ExperimentQuestionType) {
  return samplingInventory.value?.question_types.find(
    (item) => item.question_type === questionType,
  )?.available ?? 0
}

function setQuestionTypeSelected(questionType: ExperimentQuestionType, selected: boolean) {
  form.selected_question_types[questionType] = selected
  if (selected) {
    if (!Number.isInteger(form.type_quotas[questionType]) || form.type_quotas[questionType] < 1) {
      form.type_quotas[questionType] = 1
    }
  } else {
    delete form.type_quotas[questionType]
  }
}

async function refreshSamplingInventory() {
  const requestId = ++inventoryRequest
  inventoryError.value = ''
  samplingInventory.value = undefined
  const kcCodes = selectedConcepts.value.map((concept) => concept.kc_code)
  if (!form.question_bank_id) {
    inventoryLoading.value = false
    return
  }
  inventoryLoading.value = true
  try {
    const result = await api.samplingInventory(courseId, {
      question_bank_id: Number(form.question_bank_id),
      kc_codes: kcCodes,
      match_scope: form.sampling_match_scope,
    })
    if (requestId === inventoryRequest) samplingInventory.value = result
  } catch (caught) {
    if (requestId === inventoryRequest) {
      inventoryError.value = caught instanceof Error ? caught.message : '题库库存加载失败。'
    }
  } finally {
    if (requestId === inventoryRequest) inventoryLoading.value = false
  }
}

function availableCount(concept: components['schemas']['CourseConceptSummary']) {
  if (!samplingInventory.value) return '—'
  const scope = form.sampling_mode === 'total_count'
    ? form.sampling_match_scope
    : (form.kc_match_scopes[concept.id] || 'primary')
  const inventory = samplingInventory.value.kc_counts.find((item) => item.kc_code === concept.kc_code)
  return scope === 'primary' ? (inventory?.primary_available ?? 0) : (inventory?.any_level_available ?? 0)
}

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
  const input: ExperimentDefinitionInput = {
    name: form.name.trim(),
    description: form.description.trim(),
    question_bank_id: Number(form.question_bank_id),
    ordering_mode: form.ordering_mode,
    feedback_mode: form.feedback_mode,
    feedback_release_policy: form.feedback_release_policy,
    research_tools: researchTools.value,
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
  if (!legacySampling.value) {
    input.sampling = {
      mode: form.sampling_mode,
      total_count: form.sampling_mode === 'total_count'
        ? normalizeNumber(form.sampling_total_count)
        : null,
      match_scope: form.sampling_match_scope,
      nodes: concepts.value
        .filter((concept) => form.selected_kcs[concept.id])
        .map((concept) => ({
          kc_code: concept.kc_code,
          quota: form.sampling_mode === 'kc_quota'
            ? Number(form.kc_quotas[concept.id] || 0)
            : null,
          match_scope: form.sampling_mode === 'kc_quota'
            ? (form.kc_match_scopes[concept.id] || 'primary')
            : form.sampling_match_scope,
        })),
    }
    if (form.sampling_mode === 'total_count') {
      input.sampling.type_quotas = selectedQuestionTypes.value.map((questionType) => ({
        question_type: questionType.value,
        amount: Number(form.type_quotas[questionType.value]),
      }))
    }
  }
  return input
}

async function load() {
  try {
    const [collection, conceptCollection] = await Promise.all([
      api.list(courseId, 1),
      api.concepts(courseId),
    ])
    questionBanks.value = collection.summary.question_banks
    concepts.value = conceptCollection.items
    for (const concept of concepts.value) form.kc_match_scopes[concept.id] = 'primary'
    if (!editing.value && questionBanks.value.length === 1) {
      form.question_bank_id = questionBanks.value[0]?.id || 0
    }
    if (editing.value) {
      const experiment = await api.detail(courseId, experimentId.value)
      form.name = experiment.name
      form.description = experiment.description || ''
      form.question_bank_id = experiment.question_bank?.id || 0
      legacySampling.value = !experiment.sampling_mode
      legacyQuotaTotal.value = (experiment.question_quotas || []).reduce(
        (total, quota) => total + Number(quota.amount || 0),
        0,
      )
      if (experiment.sampling_mode) form.sampling_mode = experiment.sampling_mode
      form.sampling_total_count = experiment.sampling_total_count || 1
      form.sampling_match_scope = experiment.sampling_match_scope || 'any_level'
      form.ordering_mode = experiment.ordering_mode || 'fixed'
      form.feedback_mode = experiment.feedback_mode || 'none'
      form.feedback_release_policy = experiment.feedback_release_policy || 'after_personal_submission'
      researchTools.value = { ...emptyResearchTools(), ...(experiment.research_tools as Partial<ResearchTools> || {}) }
      form.time_mode = experiment.time_mode || 'none'
      form.total_time_limit_minutes = experiment.total_time_limit_minutes || null
      form.per_question_time_limit_seconds = experiment.per_question_time_limit_seconds || null
      form.resume_mode = experiment.resume_mode || 'resumable'
      form.confidence_mode = experiment.confidence_mode || 'off'
      form.confidence_sampling_unit = experiment.confidence_sampling_unit || 'attempt'
      form.confidence_probability = experiment.confidence_probability
        ? Number(experiment.confidence_probability)
        : null
      for (const rule of experiment.sampling_kcs || []) {
        const concept = concepts.value.find((item) => item.kc_code === rule.kc_code)
        if (!concept) continue
        form.selected_kcs[concept.id] = true
        form.kc_quotas[concept.id] = rule.quota || 1
        form.kc_match_scopes[concept.id] = rule.match_scope || 'primary'
      }
      for (const quota of experiment.sampling?.type_quotas || []) {
        const questionType = quota.question_type as ExperimentQuestionType
        form.selected_question_types[questionType] = true
        form.type_quotas[questionType] = quota.amount
      }
      readiness.value = await api.readiness(courseId, experimentId.value)
      savedSamplingConfiguration.value = samplingConfiguration.value
    }
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '实验配置加载失败。'
  } finally {
    loading.value = false
  }
}

async function submit() {
  if (typeQuotaInvalid.value) {
    fieldErrors.value = { sampling_type_quotas: typeQuotaError.value }
    return
  }
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

watch(
  () => [
    form.question_bank_id,
    form.sampling_mode,
    form.sampling_match_scope,
    selectedConcepts.value.map((concept) => concept.kc_code).join('|'),
  ],
  refreshSamplingInventory,
)

onMounted(load)
</script>

<template>
  <header class="kt-experiment-page-header">
    <div class="kt-experiment-page-header__copy">
      <div class="kt-experiment-title-row"><h1>{{ editing ? '编辑实验定义' : '创建实验' }}</h1></div>
      <p>定义题目选择、顺序、反馈、时限与置信度采集策略。首次正式发布后这些设置将被冻结。</p>
    </div>
  </header>

  <p v-if="loading" role="status">正在加载实验配置…</p>
  <Alert v-else-if="error" tone="danger">{{ error }}</Alert>

  <form v-if="!loading" class="kt-form-stack" novalidate @submit.prevent="submit">
    <Alert v-if="legacySampling" tone="warning">该实验是只读旧版草稿。只有点击“显式转换”并保存完整的新抽题配置后，才能修改其他定义。 <button class="kt-button kt-button--secondary" type="button" @click="convertLegacySampling">显式转换</button></Alert>
    <section :inert="legacySampling" class="kt-numbered-form-section" aria-labelledby="definition-basic">
      <div class="kt-numbered-form-section__heading"><span aria-hidden="true">1</span><h2 id="definition-basic">基本信息</h2></div>
      <div class="kt-card kt-numbered-form-section__content kt-step-one-grid">
        <FormField class="kt-compact-form-row" for-id="experiment-name" label="实验名称" :error="fieldErrors.name"><input id="experiment-name" v-model="form.name" required maxlength="180"></FormField>
        <FormField class="kt-compact-form-row" for-id="experiment-bank" label="题库" :error="fieldErrors.question_bank_id"><select id="experiment-bank" v-model.number="form.question_bank_id" required><option :value="0" disabled>请选择题库</option><option v-for="bank in questionBanks" :key="bank.id" :value="bank.id">{{ bank.name }}</option></select></FormField>
        <FormField class="kt-compact-form-row" for-id="experiment-description" label="实验描述（可选）" :error="fieldErrors.description"><textarea id="experiment-description" v-model="form.description" rows="3" placeholder="请输入实验描述" /></FormField>
      </div>
    </section>

    <section :inert="legacySampling" class="kt-numbered-form-section" aria-labelledby="definition-selection">
      <div class="kt-numbered-form-section__heading"><span aria-hidden="true">2</span><h2 id="definition-selection">题目与呈现</h2></div>
      <div class="kt-card kt-numbered-form-section__content">
        <Alert v-if="conversionWarning" tone="warning">{{ conversionWarning }}</Alert>
        <fieldset v-else class="kt-radio-group kt-compact-form-row"><legend>抽题方式</legend><div class="kt-radio-options"><label><input v-model="form.sampling_mode" name="sampling_mode" type="radio" value="total_count"><span>设置总题数<small>从所选知识点覆盖的可用题目中抽取指定数量</small></span></label><label><input v-model="form.sampling_mode" name="sampling_mode" type="radio" value="kc_quota"><span>按知识点配额<small>每行可按根 KC 或任意层级匹配并设置题数</small></span></label></div><p v-if="fieldErrors.sampling_mode" class="kt-field__error" role="alert">{{ fieldErrors.sampling_mode }}</p></fieldset>
        <template v-if="!legacySampling && form.sampling_mode === 'total_count'">
          <FormField class="kt-compact-form-row kt-count-field" for-id="sampling-total-count" label="每位参与者题目数" :error="fieldErrors.sampling_total_count"><input id="sampling-total-count" v-model.number="form.sampling_total_count" type="number" min="1"></FormField>
          <div class="kt-type-config-panel kt-compact-form-row"><strong class="kt-config-label">题型构成</strong><div class="kt-kc-config-content"><div class="kt-kc-config-top"><div><span class="kt-muted">{{ selectedQuestionTypes.length ? '已选择的题型' : '不限题型' }}</span><div v-if="selectedQuestionTypes.length" class="kt-selected-kcs"><span v-for="questionType in selectedQuestionTypes" :key="questionType.value">{{ questionType.label }} {{ form.type_quotas[questionType.value] }}</span></div></div><div class="kt-selection-facts">已分配 {{ typeQuotaTotal }} / 总题数 {{ form.sampling_total_count || 0 }}</div><details class="kt-kc-picker"><summary class="kt-button kt-button--secondary" data-type-picker>选择题型</summary><div class="kt-kc-tree-selector kt-type-tree-selector"><label v-for="questionType in QUESTION_TYPES" :key="questionType.value"><input name="question_type_choice" type="checkbox" :value="questionType.value" :checked="Boolean(form.selected_question_types[questionType.value])" @change="setQuestionTypeSelected(questionType.value, ($event.target as HTMLInputElement).checked)"><span>{{ questionType.label }}</span><small>{{ questionTypeAvailable(questionType.value) }} 道可用</small><input v-model.number="form.type_quotas[questionType.value]" :aria-label="`${questionType.label}数量`" type="number" min="1" :max="questionTypeAvailable(questionType.value)" :disabled="!form.selected_question_types[questionType.value]" @click.stop></label></div></details></div><div class="kt-type-allocation-status" :class="{ 'is-error': typeQuotaError || fieldErrors.sampling_type_quotas }"><span v-if="!selectedQuestionTypes.length && !fieldErrors.sampling_type_quotas">不限制各题型数量，系统从去重后的候选题中抽取。</span><span v-else-if="typeQuotaError || fieldErrors.sampling_type_quotas" role="alert">{{ typeQuotaError || fieldErrors.sampling_type_quotas }}</span><span v-else>题型构成有效，按各题型候选桶确定性抽取。</span></div></div></div>
          <div class="kt-kc-config-panel kt-compact-form-row"><strong class="kt-config-label">知识点范围</strong><div class="kt-kc-config-content"><div class="kt-kc-config-top"><div><span class="kt-muted">已选择的 KC</span><div class="kt-selected-kcs"><span v-for="concept in selectedConcepts" :key="concept.id"><b class="kt-mono">{{ concept.kc_code }}</b> {{ concept.name }}</span><span v-if="!selectedConcepts.length" class="is-empty">尚未选择</span></div></div><div class="kt-selection-facts">已选择 {{ selectedConcepts.length }} 个 KC</div><details class="kt-kc-picker"><summary class="kt-button kt-button--secondary">选择知识点</summary><div class="kt-kc-tree-selector"><label v-for="concept in concepts" :key="concept.id"><input v-model="form.selected_kcs[concept.id]" type="checkbox" :value="concept.id" :disabled="concept.status !== 'active'"><span><b class="kt-mono">{{ concept.kc_code }}</b> {{ concept.name }}</span><small>{{ concept.status === 'active' ? `${availableCount(concept)} 道可用` : '已归档' }}</small></label></div></details></div><div class="kt-match-scope"><span>匹配层级</span><label><input v-model="form.sampling_match_scope" type="radio" value="primary">根 KC</label><small>仅匹配所选节点本身</small><label><input v-model="form.sampling_match_scope" type="radio" value="any_level">任意层级</label><small>包含其在题目树中任意层级的出现</small></div></div></div>
        </template>
        <div v-if="!legacySampling && form.sampling_mode === 'kc_quota'" class="kt-quota-panel kt-compact-form-row"><strong class="kt-config-label">知识点配额</strong><div class="kt-kc-config-content"><div class="kt-quota-toolbar"><p>按所选 KC 及匹配层级设置每位参与者题量。</p><details class="kt-kc-picker"><summary class="kt-button kt-button--secondary">＋ 添加 KC</summary><div class="kt-kc-tree-selector"><label v-for="concept in concepts" :key="concept.id"><input v-model="form.selected_kcs[concept.id]" type="checkbox" :value="concept.id" :disabled="concept.status !== 'active'"><span><b class="kt-mono">{{ concept.kc_code }}</b> {{ concept.name }}</span><small>{{ concept.status === 'active' ? `${availableCount(concept)} 道可用` : '已归档' }}</small></label></div></details></div><div class="kt-quota-table-wrap"><table class="kt-quota-table"><thead><tr><th>KC</th><th>匹配层级</th><th>可用题目</th><th>配额</th><th>操作</th></tr></thead><tbody><tr v-for="concept in selectedConcepts" :key="concept.id"><td><b class="kt-mono">{{ concept.kc_code }}</b> {{ concept.name }}</td><td><select v-model="form.kc_match_scopes[concept.id]" :aria-label="`${concept.name}匹配层级`"><option value="primary">根 KC</option><option value="any_level">任意层级</option></select></td><td>{{ availableCount(concept) }}</td><td><input v-model.number="form.kc_quotas[concept.id]" :aria-label="`${concept.name}配额`" type="number" min="1"></td><td><button class="kt-text-button kt-text-button--danger" type="button" @click="form.selected_kcs[concept.id] = false">删除</button></td></tr><tr v-if="!selectedConcepts.length"><td colspan="5" class="kt-empty-quota">请添加需要设置配额的 KC</td></tr></tbody><tfoot><tr><td colspan="5">每位参与者共抽 <strong>{{ quotaTotal }}</strong> 题</td></tr></tfoot></table></div></div></div>
        <p v-if="!legacySampling" class="kt-muted">可用题数仅统计当前所选题库中状态为“可用”的题目；同一道题可能匹配多个 KC，发布时会校验能否无重复地满足配额。</p>
        <p v-if="inventoryLoading" role="status">正在加载当前题库库存…</p>
        <Alert v-if="inventoryError" tone="danger">{{ inventoryError }}</Alert>
        <p v-if="fieldErrors.sampling_kcs" class="kt-field__error" role="alert">{{ fieldErrors.sampling_kcs }}</p>
        <Alert v-if="samplingChanged && !legacySampling" tone="warning">抽题配置尚未保存。保存后将按当前题库、匹配层级和配额重新进行发布预检。</Alert>
        <div v-if="readiness && !legacySampling && !samplingChanged" aria-label="已保存抽题配置库存预检">
          <Alert :tone="readiness.ready ? 'success' : 'danger'">已保存配置：计划抽取 {{ readiness.requested_count ?? '—' }} 题，当前候选 {{ readiness.candidate_count }} 题。<span v-if="readiness.issues.length"> {{ readiness.issues.join('；') }}</span></Alert>
          <table v-if="readiness.inventory_buckets.length" class="kt-table"><thead><tr><th>KC</th><th>匹配层级</th><th>计划题数</th><th>可用题数</th></tr></thead><tbody><tr v-for="bucket in readiness.inventory_buckets" :key="bucket.kc_code"><td><span class="kt-mono">{{ bucket.kc_code }}</span> {{ bucket.kc_name }}</td><td>{{ bucket.match_scope === 'primary' ? '仅根 KC' : '任意层级' }}</td><td>{{ bucket.requested ?? '范围' }}</td><td>{{ bucket.available }}</td></tr></tbody></table>
          <small class="kt-muted">库存数据对应当前已保存版本；保存本页更改后会重新计算。</small>
        </div>
        <fieldset class="kt-radio-group kt-compact-form-row"><legend>题目顺序</legend><div class="kt-radio-options"><label><input v-model="form.ordering_mode" name="ordering_mode" type="radio" value="fixed"><span>固定顺序</span></label><label><input v-model="form.ordering_mode" name="ordering_mode" type="radio" value="random"><span>随机顺序</span></label><label><input v-model="form.ordering_mode" name="ordering_mode" type="radio" value="stratified_random"><span>分层随机</span></label></div><p v-if="fieldErrors.ordering_mode" class="kt-field__error" role="alert">{{ fieldErrors.ordering_mode }}</p></fieldset>
        <fieldset class="kt-radio-group kt-compact-form-row"><legend>反馈</legend><div class="kt-radio-options"><label><input v-model="form.feedback_mode" name="feedback_mode" type="radio" value="none"><span>仅显示已提交</span></label><label><input v-model="form.feedback_mode" name="feedback_mode" type="radio" value="correctness"><span>显示正误</span></label><label><input v-model="form.feedback_mode" name="feedback_mode" type="radio" value="full"><span>显示正误、答案与解析</span></label></div><p v-if="fieldErrors.feedback_mode" class="kt-field__error" role="alert">{{ fieldErrors.feedback_mode }}</p></fieldset>
        <fieldset class="kt-radio-group kt-compact-form-row"><legend>反馈发布</legend><div class="kt-radio-options"><label><input v-model="form.feedback_release_policy" name="feedback_release_policy" type="radio" value="after_personal_submission"><span>学生完成后</span></label><label><input v-model="form.feedback_release_policy" name="feedback_release_policy" type="radio" value="after_assignment_deadline"><span>班级任务截止后</span></label><label><input v-model="form.feedback_release_policy" name="feedback_release_policy" type="radio" value="manual_release"><span>教师人工发布</span></label></div><p v-if="fieldErrors.feedback_release_policy" class="kt-field__error" role="alert">{{ fieldErrors.feedback_release_policy }}</p></fieldset>
      </div>
    </section>

    <section :inert="legacySampling" class="kt-numbered-form-section" aria-labelledby="definition-runtime">
      <div class="kt-numbered-form-section__heading"><span aria-hidden="true">3</span><h2 id="definition-runtime">作答规则</h2></div>
      <details class="kt-card kt-numbered-form-section__content kt-collapsible-step">
        <summary><span>时限：{{ form.time_mode === 'none' ? '无时限' : (form.time_mode === 'total' ? '实验总时限' : '单题时限') }}</span><span>中断：{{ form.resume_mode === 'resumable' ? '允许续答' : '必须一次完成' }}</span><span aria-hidden="true">›</span></summary>
        <div class="kt-collapsible-step__body">
          <fieldset class="kt-radio-group kt-compact-form-row"><legend>时限</legend><div class="kt-radio-options"><label><input v-model="form.time_mode" name="time_mode" type="radio" value="none"><span>无时限</span></label><label><input v-model="form.time_mode" name="time_mode" type="radio" value="total"><span>实验总时限</span></label><label><input v-model="form.time_mode" name="time_mode" type="radio" value="per_question"><span>单题时限</span></label></div><p v-if="fieldErrors.time_mode" class="kt-field__error" role="alert">{{ fieldErrors.time_mode }}</p></fieldset>
          <div class="kt-form-grid"><FormField class="kt-compact-form-row" for-id="experiment-total-time" label="总时限（分钟）" :error="fieldErrors.total_time_limit_minutes"><input id="experiment-total-time" v-model.number="form.total_time_limit_minutes" type="number" min="1" :disabled="form.time_mode !== 'total'"></FormField><FormField class="kt-compact-form-row" for-id="experiment-question-time" label="单题限时（秒）" :error="fieldErrors.per_question_time_limit_seconds"><input id="experiment-question-time" v-model.number="form.per_question_time_limit_seconds" type="number" min="1" :disabled="form.time_mode !== 'per_question'"></FormField></div>
          <fieldset class="kt-radio-group kt-compact-form-row"><legend>中断后继续</legend><div class="kt-radio-options"><label><input v-model="form.resume_mode" name="resume_mode" type="radio" value="resumable"><span>允许断点续答</span></label><label><input v-model="form.resume_mode" name="resume_mode" type="radio" value="single_session"><span>必须一次完成</span></label></div><p v-if="fieldErrors.resume_mode" class="kt-field__error" role="alert">{{ fieldErrors.resume_mode }}</p></fieldset>
        </div>
      </details>
    </section>

    <section :inert="legacySampling" class="kt-numbered-form-section" aria-labelledby="definition-collection">
      <div class="kt-numbered-form-section__heading"><span aria-hidden="true">4</span><h2 id="definition-collection">研究采集</h2></div>
      <details class="kt-card kt-numbered-form-section__content kt-collapsible-step">
        <summary><span>作答信息：{{ form.confidence_mode === 'off' ? '不采集' : (form.confidence_mode === 'all' ? '全部采集' : '抽样采集') }}</span><span>正式发布时冻结版本快照</span><span aria-hidden="true">›</span></summary>
        <div class="kt-collapsible-step__body">
          <fieldset class="kt-radio-group kt-compact-form-row"><legend>作答信心采集</legend><div class="kt-radio-options"><label><input v-model="form.confidence_mode" name="confidence_mode" type="radio" value="off"><span>不采集</span></label><label><input v-model="form.confidence_mode" name="confidence_mode" type="radio" value="all"><span>全部采集</span></label><label><input v-model="form.confidence_mode" name="confidence_mode" type="radio" value="sampled"><span>抽样采集</span></label></div><p v-if="fieldErrors.confidence_mode" class="kt-field__error" role="alert">{{ fieldErrors.confidence_mode }}</p></fieldset>
          <div v-if="form.confidence_mode !== 'off'" class="kt-form-grid"><fieldset class="kt-radio-group kt-compact-form-row"><legend>采集单元</legend><div class="kt-radio-options"><label><input v-model="form.confidence_sampling_unit" name="confidence_sampling_unit" type="radio" value="student"><span>学生</span></label><label><input v-model="form.confidence_sampling_unit" name="confidence_sampling_unit" type="radio" value="session"><span>会话</span></label><label><input v-model="form.confidence_sampling_unit" name="confidence_sampling_unit" type="radio" value="attempt"><span>作答</span></label></div><p v-if="fieldErrors.confidence_sampling_unit" class="kt-field__error" role="alert">{{ fieldErrors.confidence_sampling_unit }}</p></fieldset><FormField v-if="form.confidence_mode === 'sampled'" class="kt-compact-form-row" for-id="experiment-confidence-probability" label="抽样概率（0–1）" :error="fieldErrors.confidence_probability"><input id="experiment-confidence-probability" v-model.number="form.confidence_probability" type="number" min="0" max="1" step="0.001"></FormField></div>
          <Alert tone="info">系统会在正式发布时自动生成版本快照与随机化标识，用于审计与复现。</Alert>
        </div>
      </details>
    </section>

    <section :inert="legacySampling" class="kt-numbered-form-section" aria-labelledby="definition-tools">
      <div class="kt-numbered-form-section__heading"><span aria-hidden="true">5</span><h2 id="definition-tools">题目工具</h2></div>
      <ResearchToolsEditor v-model="researchTools" class="kt-numbered-form-section__content" />
    </section>

    <div class="kt-form-actions kt-experiment-form-actions">
      <a class="kt-button kt-button--secondary" :href="editing ? `/teacher/courses/${courseId}/experiments/${experimentId}/` : `/teacher/courses/${courseId}/experiments/`">{{ editing ? '返回详情' : '取消' }}</a>
      <Button type="submit" :disabled="saving || legacySampling || typeQuotaInvalid">{{ saving ? '正在保存…' : (editing ? '保存更改' : '保存实验') }}</Button>
    </div>
  </form>
</template>
