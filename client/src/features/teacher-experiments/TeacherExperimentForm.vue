<script setup lang="ts">
import { useExperimentPageUrl } from './experiment-page-url'
const pageUrl = useExperimentPageUrl()
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { ApiError } from '@/api/client'
import type { components } from '@/api/generated/schema'
import Icon from '@/design-system/Icon.vue'
import Alert from '@/design-system/components/Alert.vue'
import Button from '@/design-system/components/Button.vue'
import FormField from '@/design-system/components/FormField.vue'
import ModalDialog from '@/design-system/components/ModalDialog.vue'
import SamplingRangePicker from './SamplingRangePicker.vue'
import ExperimentQuestionPreview from './ExperimentQuestionPreview.vue'
import Paginator from '@/design-system/components/Paginator.vue'
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
  { value: 'reading', label: '阅读理解' },
  { value: 'words', label: '选词填空' },
  { value: 'cloze', label: '短文填空' },
  { value: 'translation', label: '英汉互译' },
  { value: 'essay', label: '作文题' },
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
const courseName = ref('课程')
const experimentId = computed(() => Number(route.params.experimentId || 0))
const editing = computed(() => route.name === 'teacher-experiment-edit' || route.name === 'admin-course-experiment-edit')
const loading = ref(true)
const researchTools = ref<ResearchTools>(emptyResearchTools())
const saving = ref(false)
const error = ref('')
const fieldErrors = ref<Record<string, string>>({})
const questionBanks = ref<Array<{ id: number; name: string }>>([])
const bankSearch = ref('')
const filteredQuestionBanks = computed(() => questionBanks.value.filter((bank) =>
  bank.name.toLocaleLowerCase().includes(bankSearch.value.trim().toLocaleLowerCase()),
))
const selectedQuestionBanks = computed(() => questionBanks.value.filter((bank) => form.question_bank_ids.includes(bank.id)))
const bankPickerOpen = ref(false)
const pendingBankIds = ref<number[]>([])
const bankCounts = ref<SamplingInventory['bank_counts']>([])
const bankCountsLoading = ref(false)
const bankCountsError = ref('')
const typePickerOpen = ref(false)
const pendingTypes = ref<ExperimentQuestionType[]>([])
const pendingPins = ref<number[]>([])
const pinError = ref('')
const previewIds = ref<number[]>([])
const knownQuestionTypes = new Map<number, ExperimentQuestionType>()

async function openBankPicker(event: MouseEvent) {
  (event.currentTarget as HTMLElement).focus()
  pendingBankIds.value = [...form.question_bank_ids]
  bankSearch.value = ''
  bankPickerOpen.value = true
  bankCountsLoading.value = true
  bankCountsError.value = ''
  try {
    const result = await api.samplingInventory(courseId, {
      question_bank_ids: questionBanks.value.map(bank => bank.id),
      kc_codes: [], match_scope: 'any_level',
    })
    bankCounts.value = result.bank_counts || []
  } catch (caught) {
    bankCountsError.value = caught instanceof Error ? caught.message : '题库库存加载失败。'
  } finally { bankCountsLoading.value = false }
}
function applyBanks() {
  form.question_bank_ids = [...pendingBankIds.value]
  bankPickerOpen.value = false
}
function openTypePicker(event: MouseEvent) {
  (event.currentTarget as HTMLElement).focus()
  pendingTypes.value = selectedQuestionTypes.value.map(item => item.value)
  typePickerOpen.value = true
}
function applyTypes() {
  for (const item of availableQuestionTypes.value) {
    // Unavailable types and their quotas require an explicit removal from the main form.
    if (Number(questionTypeAvailable(item.value) || 0) > 0) {
      setQuestionTypeSelected(item.value, pendingTypes.value.includes(item.value))
    }
  }
  typePickerOpen.value = false
}
function pinType(id: number) {
  return knownQuestionTypes.get(id)
    || samplingInventory.value?.questions?.find(item => item.question_id === id)?.question_type
}
function openQuestionPicker(event: MouseEvent, type: ExperimentQuestionType | '' = '') {
  (event.currentTarget as HTMLElement).focus()
  questionTypeFilter.value = type
  pendingPins.value = form.pinned_question_ids.filter(id => !type || pinType(id) === type || !pinType(id))
  questionSearch.value = ''
  questionPickerPage.value = 1
  previewIds.value = []
  pinError.value = ''
  questionPickerOpen.value = true
}
const pickerQuota = computed(() => Number(questionTypeFilter.value
  ? form.type_quotas[questionTypeFilter.value] : form.sampling_total_count) || 0)
const invalidPendingPins = computed(() => pendingPins.value.filter(id =>
  !samplingInventory.value?.questions?.some(item => item.question_id === id),
))
function applyPins() {
  pinError.value = ''
  if (inventoryLoading.value || inventoryError.value || !samplingInventory.value) {
    pinError.value = inventoryError.value || '请等待候选库存加载完成。'
    return
  }
  if (pendingPins.value.length > pickerQuota.value) {
    pinError.value = questionTypeFilter.value ? '指定题目数超过该题型数量。' : '指定题目数超过总题数。'
    return
  }
  if (invalidPendingPins.value.length) {
    pinError.value = '存在已失效的指定题目，请先清除或取消并调整题目范围。'
    return
  }
  if (!questionTypeFilter.value && selectedQuestionTypes.value.length) {
    const outside = pendingPins.value.some(id => !form.selected_question_types[pinType(id)!])
    const exceeds = selectedQuestionTypes.value.some(type =>
      pendingPins.value.filter(id => pinType(id) === type.value).length > form.type_quotas[type.value],
    )
    if (outside || exceeds) {
      pinError.value = outside ? '指定题目包含未选择的题型。' : '指定题目数超过该题型数量。'
      return
    }
  }
  const otherPins = questionTypeFilter.value
    ? form.pinned_question_ids.filter(id => pinType(id) && pinType(id) !== questionTypeFilter.value)
    : []
  form.pinned_question_ids = [...otherPins, ...pendingPins.value]
  questionPickerOpen.value = false
}
const concepts = ref<components['schemas']['CourseConceptSummary'][]>([])
const readiness = ref<TeacherExperimentReadiness>()
const legacySampling = ref(false)
const legacyQuotaTotal = ref(0)
const conversionWarning = ref('')
const samplingInventory = ref<SamplingInventory>()
const inventoryLoading = ref(false)
const inventoryError = ref('')
const savedSamplingConfiguration = ref('')
const runtimeEditor = ref<HTMLElement>()
const questionPickerOpen = ref(false)
const questionSearch = ref('')
const questionTypeFilter = ref<ExperimentQuestionType | ''>('')
const questionPickerPage = ref(1)
const savedPinnedQuestionIds = ref<number[]>([])
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
  question_bank_ids: [] as number[],
  sampling_mode: 'total_count' as 'total_count' | 'kc_quota' | 'all',
  sampling_total_count: 1 as number | null,
  sampling_match_scope: 'any_level' as 'primary' | 'any_level',
  selected_kcs: {} as Record<number, boolean>,
  kc_quotas: {} as Record<number, number>,
  kc_match_scopes: {} as Record<number, 'primary' | 'any_level'>,
  selected_question_types: {} as Record<ExperimentQuestionType, boolean>,
  type_quotas: {} as Record<ExperimentQuestionType, number>,
  type_ranges: {} as Partial<Record<ExperimentQuestionType, number[]>>,
  pinned_question_ids: [] as number[],
  cross_experiment_item_policy: 'exclude_previous' as 'exclude_previous' | 'mixed_review' | 'unrestricted',
  review_count: 0,
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

const rangeType = ref<ExperimentQuestionType | ''>('')
const rangesTouched = ref(false)
const scopedInventory = ref<SamplingInventory>()
const rangeInventoryError = ref('')
const typeRanges = computed(() => Object.entries(form.type_ranges).map(([question_type, question_ids]) => ({
  question_type: question_type as ExperimentQuestionType, question_ids: [...question_ids!],
})))
const rangeError = computed(() => {
  if (!typeRanges.value.length) return ''
  if (form.sampling_mode !== 'total_count') return '当前数量规则不支持自定义抽题范围，请返回总题数模式或明确清除范围。'
  if (inventoryLoading.value) return '正在校验候选范围…'
  if (inventoryError.value) return inventoryError.value
  for (const range of typeRanges.value) {
    if (!form.selected_question_types[range.question_type]) return '自定义范围包含未选择的题型，请清除该范围。'
    const label = questionTypeLabel(range.question_type)
    if (!range.question_ids.length || range.question_ids.length < Number(form.type_quotas[range.question_type])) return `${label}候选范围不足，请增加候选题目或减少题量。`
    if (range.question_ids.some(id => !samplingInventory.value?.questions?.some(q => q.question_id === id && q.question_type === range.question_type))) return `${label}候选范围包含已失效题目，请重新选择或恢复全题库随机。`
    if (form.pinned_question_ids.some(id => pinType(id) === range.question_type && !range.question_ids.includes(id))) return `${label}必出题目不在候选范围内，请调整范围或移除必出题目。`
  }
  return rangeInventoryError.value
})
function applyRange(ids: number[] | null) {
  if (!rangeType.value) return
  rangesTouched.value = true
  if (ids === null) delete form.type_ranges[rangeType.value]
  else form.type_ranges[rangeType.value] = [...ids]
  rangeType.value = ''
}
function clearRanges() {
  form.type_ranges = {}
  rangesTouched.value = true
}
function openRange(event: MouseEvent, type: ExperimentQuestionType) {
  (event.currentTarget as HTMLElement).focus()
  rangeType.value = type
}

const quotaTotal = computed(() => concepts.value.reduce((total, concept) => (
  form.selected_kcs[concept.id] ? total + Number(form.kc_quotas[concept.id] || 0) : total
), 0))
const assignmentTotal = computed(() => form.sampling_mode === 'all' ? (samplingInventory.value?.candidate_count || 0)
  : form.sampling_mode === 'total_count' ? Number(form.sampling_total_count || 0) : quotaTotal.value)
const reviewCountError = computed(() => {
  if (form.cross_experiment_item_policy !== 'mixed_review') return ''
  const count = Number(form.review_count)
  if (!Number.isInteger(count) || count < 1 || count >= assignmentTotal.value) {
    return '复测题数必须大于 0 且小于本次总题数。'
  }
  return ''
})
const selectedConcepts = computed(() => concepts.value.filter(
  (concept) => form.selected_kcs[concept.id],
))
const historyPolicyDescription = computed(() => ({
  exclude_previous: '排除范围包括同课程已正式提交题目，以及其他正式任务曾分配的题目，包括已结束任务。',
  mixed_review: '精确抽取指定数量的历史正式作答题，其余使用未正式作答题；复测会创建新的作答记录。',
  unrestricted: '不依据历史作答或其他任务题单排除原题。',
})[form.cross_experiment_item_policy])
const samplingConfiguration = computed(() => JSON.stringify({
  question_bank_ids: [...form.question_bank_ids].map(Number).sort((a, b) => a - b),
  sampling: definitionInput().sampling,
}))
const samplingChanged = computed(() => Boolean(savedSamplingConfiguration.value)
  && samplingConfiguration.value !== savedSamplingConfiguration.value)
const selectedQuestionTypes = computed(() => QUESTION_TYPES.filter(
  (questionType) => form.selected_question_types[questionType.value],
))
const availableQuestionTypes = computed(() => QUESTION_TYPES.filter(
  item => Number(questionTypeAvailable(item.value) || 0) > 0,
))
const filteredQuestions = computed(() => (samplingInventory.value?.questions || []).filter(item =>
  (!questionTypeFilter.value || item.question_type === questionTypeFilter.value)
  && `${item.code} ${item.title} ${item.text}`.toLocaleLowerCase().includes(questionSearch.value.trim().toLocaleLowerCase()),
))
const questionPickerPages = computed(() => Math.max(1, Math.ceil(filteredQuestions.value.length / 12)))
const questionPickerRows = computed(() => filteredQuestions.value.slice((questionPickerPage.value - 1) * 12, questionPickerPage.value * 12))
const pinnedQuestions = computed(() => form.pinned_question_ids.map(id => {
  const item = samplingInventory.value?.questions?.find(question => question.question_id === id)
  return { id, title: item?.title || item?.code || `题目 ${id}`, available: Boolean(item) }
}))
const incompatiblePins = computed(() => form.sampling_mode !== 'total_count' && form.pinned_question_ids.length > 0)
watch([questionSearch, questionTypeFilter], () => { questionPickerPage.value = 1 })

function questionTypeLabel(value: string) {
  return QUESTION_TYPES.find(item => item.value === value)?.label || value
}
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
  if (!samplingInventory.value) return '正在校验题型库存…'
  const inventory = new Map(
    ((typeRanges.value.length ? scopedInventory.value : samplingInventory.value)?.question_types || samplingInventory.value?.question_types || []).map((item) => [item.question_type, item.available]),
  )
  const shortage = selectedQuestionTypes.value.find(
    (item) => Number(form.type_quotas[item.value] || 0) > Number(inventory.get(item.value) || 0),
  )
  return shortage
    ? `${shortage.label}库存不足：需要 ${form.type_quotas[shortage.value]}，可用 ${inventory.get(shortage.value) || 0}。`
    : ''
})
const typeQuotaInvalid = computed(() => Boolean(typeQuotaError.value || rangeError.value))

function questionTypeAvailable(questionType: ExperimentQuestionType) {
  return samplingInventory.value?.question_types.find(
    (item) => item.question_type === questionType,
  )?.available
}

function questionTypeAvailabilityLabel(questionType: ExperimentQuestionType) {
  const available = questionTypeAvailable(questionType)
  return available === undefined ? '正在读取' : `${available} 道可用`
}

function focusRuntimeEditor() {
  runtimeEditor.value?.querySelector<HTMLInputElement>('input:not(:disabled)')?.focus()
}

function setQuestionTypeSelected(questionType: ExperimentQuestionType, selected: boolean) {
  form.selected_question_types[questionType] = selected
  if (selected) {
    if (!Number.isInteger(form.type_quotas[questionType]) || form.type_quotas[questionType] < 1) {
      form.type_quotas[questionType] = 1
    }
  } else {
    if (form.type_ranges[questionType]) rangesTouched.value = true
    delete form.type_ranges[questionType]
    delete form.type_quotas[questionType]
    form.pinned_question_ids = form.pinned_question_ids.filter(id => pinType(id) !== questionType)
  }
}

async function refreshSamplingInventory() {
  const requestId = ++inventoryRequest
  inventoryError.value = ''
  rangeInventoryError.value = ''
  scopedInventory.value = undefined
  samplingInventory.value = undefined
  const kcCodes = form.sampling_mode === 'all' ? [] : selectedConcepts.value.map((concept) => concept.kc_code)
  if (!form.question_bank_ids.length) {
    inventoryLoading.value = false
    return
  }
  inventoryLoading.value = true
  try {
    const result = await api.samplingInventory(courseId, {
      question_bank_ids: [...form.question_bank_ids].map(Number).sort((a, b) => a - b),
      kc_codes: kcCodes,
      match_scope: form.sampling_match_scope,
    })
    if (requestId === inventoryRequest) {
      samplingInventory.value = result
      for (const item of result.questions || []) knownQuestionTypes.set(item.question_id, item.question_type)
      if (typeRanges.value.length && form.sampling_mode === 'total_count') {
        try {
          const scoped = await api.samplingInventory(courseId, {
            question_bank_ids: [...form.question_bank_ids], kc_codes: kcCodes,
            match_scope: form.sampling_match_scope, type_ranges: typeRanges.value,
          })
          if (requestId === inventoryRequest) scopedInventory.value = scoped
        } catch (caught) {
          if (requestId === inventoryRequest) rangeInventoryError.value = caught instanceof Error ? caught.message : '候选范围校验失败。'
        }
      }
    }
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
    question_bank_ids: [...form.question_bank_ids].map(Number).sort((a, b) => a - b),
    cross_experiment_item_policy: form.cross_experiment_item_policy,
    review_count: form.cross_experiment_item_policy === 'mixed_review'
      ? Number(form.review_count || 0)
      : 0,
    ordering_mode: form.ordering_mode,
    feedback_mode: form.feedback_mode,
    feedback_release_policy: form.feedback_release_policy,
    research_tools: researchTools.value,
    ...(form.time_mode !== 'per_question' ? { time_mode: form.time_mode } : {}),
    total_time_limit_minutes: form.time_mode === 'total'
      ? normalizeNumber(form.total_time_limit_minutes)
      : null,
    per_question_time_limit_seconds: null,
    resume_mode: form.resume_mode,
    confidence_mode: form.confidence_mode,
    confidence_sampling_unit: form.confidence_sampling_unit,
    confidence_probability: form.confidence_mode === 'sampled'
      ? String(normalizeNumber(form.confidence_probability) ?? '')
      : null,
  }
  if (!legacySampling.value) {
    input.sampling = {
      mode: form.sampling_mode === 'all' ? null : form.sampling_mode,
      total_count: form.sampling_mode === 'total_count'
        ? normalizeNumber(form.sampling_total_count)
        : null,
      match_scope: form.sampling_match_scope,
      nodes: form.sampling_mode === 'all' ? [] : concepts.value
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
    if (typeRanges.value.length || rangesTouched.value) input.sampling.type_ranges = typeRanges.value
    if (form.pinned_question_ids.length || savedPinnedQuestionIds.value.length) {
      input.sampling.pinned_question_ids = [...form.pinned_question_ids]
    }
    if (form.sampling_mode === 'all') input.sampling.type_quotas = []
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
    courseName.value = conceptCollection.course.name
    concepts.value = conceptCollection.items
    for (const concept of concepts.value) form.kc_match_scopes[concept.id] = 'primary'
    if (!editing.value && questionBanks.value.length === 1) {
      form.question_bank_ids = questionBanks.value.length ? [questionBanks.value[0]!.id] : []
    }
    if (editing.value) {
      const experiment = await api.detail(courseId, experimentId.value)
      form.name = experiment.name
      form.description = experiment.description || ''
      form.question_bank_ids = experiment.question_banks?.map((bank) => bank.id)
        || (experiment.question_bank?.id ? [experiment.question_bank.id] : [])
      for (const bank of experiment.question_banks || []) {
        if (!questionBanks.value.some((option) => option.id === bank.id)) questionBanks.value.push(bank)
      }
      legacySampling.value = !experiment.sampling_mode
      legacyQuotaTotal.value = (experiment.question_quotas || []).reduce(
        (total, quota) => total + Number(quota.amount || 0),
        0,
      )
      if (experiment.sampling_mode) form.sampling_mode = experiment.sampling_mode
      form.sampling_total_count = experiment.sampling_total_count || 1
      form.sampling_match_scope = experiment.sampling_match_scope || 'any_level'
      form.cross_experiment_item_policy = experiment.cross_experiment_item_policy || 'unrestricted'
      form.review_count = Number(experiment.review_count || 0)
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
      for (const range of experiment.sampling?.type_ranges || []) {
        form.type_ranges[range.question_type as ExperimentQuestionType] = [...range.question_ids]
      }
      rangesTouched.value = typeRanges.value.length > 0
      form.pinned_question_ids = [...(experiment.sampling?.pinned_question_ids || [])]
      savedPinnedQuestionIds.value = [...form.pinned_question_ids]
      readiness.value = await api.readiness(courseId, experimentId.value)
      if (!experiment.sampling_mode && readiness.value.question_group_pool) {
        legacySampling.value = false
        form.sampling_mode = 'all'
      }
      savedSamplingConfiguration.value = samplingConfiguration.value
    }
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '实验配置加载失败。'
  } finally {
    loading.value = false
  }
}

async function submit(destination: 'detail' | 'publish' = 'detail') {
  if (incompatiblePins.value || rangeError.value) return
  if (form.time_mode === 'per_question') {
    fieldErrors.value = { time_mode: '历史草稿使用单题计时，请明确选择无时限或实验总时限。' }
    return
  }
  if (!form.question_bank_ids.length || typeQuotaInvalid.value || reviewCountError.value) {
    fieldErrors.value = {
      ...(!form.question_bank_ids.length ? { question_bank_ids: '至少选择一个题库。' } : {}),
      ...(typeQuotaInvalid.value ? { sampling_type_quotas: typeQuotaError.value } : {}),
      ...(reviewCountError.value ? { review_count: reviewCountError.value } : {}),
    }
    return
  }
  saving.value = true
  error.value = ''
  fieldErrors.value = {}
  try {
    const saved = editing.value
      ? await api.update(courseId, experimentId.value, definitionInput())
      : await api.create(courseId, definitionInput())
    if (destination === 'publish') {
      const plan = await api.startPlan(courseId, saved.id)
      await router.push(pageUrl(`/teacher/courses/${courseId}/experiments/${saved.id}/publish-plan/${plan.id}/step/${plan.current_step || 1}/`)!)
    } else {
      await router.push(pageUrl(`/teacher/courses/${courseId}/experiments/${saved.id}/`)!)
    }
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
    JSON.stringify(typeRanges.value),
    form.question_bank_ids.join('|'),
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
      <nav class="kt-experiment-breadcrumb" aria-label="层级目录">
        <a :href="pageUrl('/teacher/courses/')">我的课程</a><span aria-hidden="true">/</span>
        <a :href="pageUrl(`/teacher/courses/${courseId}/`)">{{ courseName }}</a><span aria-hidden="true">/</span>
        <a :href="pageUrl(`/teacher/courses/${courseId}/experiments/`)">实验</a><span aria-hidden="true">/</span>
        <template v-if="editing"><a :href="pageUrl(`/teacher/courses/${courseId}/experiments/${experimentId}/`)">{{ form.name || '实验' }}</a><span aria-hidden="true">/</span></template>
        <span aria-current="page">{{ editing ? '编辑实验定义' : '创建实验' }}</span>
      </nav>
      <div class="kt-experiment-title-row"><h1>{{ editing ? '编辑实验定义' : '创建实验' }}</h1></div>
      <p>定义题目范围、抽样规则和研究采集策略，生成一次新的实验任务。</p>
    </div>
    <div class="kt-experiment-page-header__actions">
      <button class="kt-button kt-button--secondary" type="submit" form="experiment-definition-form" :disabled="loading || saving || legacySampling || typeQuotaInvalid">{{ saving ? '正在保存…' : '保存草稿' }}</button>
      <a class="kt-button kt-button--secondary" :href="pageUrl(editing ? `/teacher/courses/${courseId}/experiments/${experimentId}/` : `/teacher/courses/${courseId}/experiments/`)">返回实验</a>
    </div>
  </header>

  <p v-if="loading" role="status">正在加载实验配置…</p>
  <Alert v-else-if="error" tone="danger">{{ error }}</Alert>

  <ol v-if="!loading" class="kt-sitev2-steps" aria-label="发布步骤"><li class="current"><span>01</span>实验定义</li><li><span>02</span>班级与排期</li><li><span>03</span>发布前检查</li><li><span>04</span>确认发布</li></ol>
  <div v-if="!loading" class="kt-experiment-config-layout">
    <form id="experiment-definition-form" class="kt-form-stack" novalidate @submit.prevent="submit('detail')">
      <Alert v-if="legacySampling" tone="warning">该实验是只读旧版草稿。只有点击“显式转换”并保存完整的新抽题配置后，才能修改其他定义。 <button class="kt-button kt-button--secondary" type="button" @click="convertLegacySampling">显式转换</button></Alert>
      <section :inert="legacySampling" class="kt-numbered-form-section" aria-labelledby="definition-basic">
        <div class="kt-numbered-form-section__heading">
          <span aria-hidden="true">01</span><h2 id="definition-basic">基本信息</h2>
          <button id="experiment-bank" class="kt-text-button kt-experiment-bank-trigger" type="button" aria-haspopup="dialog" :title="selectedQuestionBanks.map(bank => bank.name).join('、')" @click="openBankPicker">选择题库<span class="kt-visually-hidden">已选择 {{ form.question_bank_ids.length }} 个题库</span></button>
        </div>
        <div class="kt-card kt-numbered-form-section__content kt-step-one-grid">
          <FormField class="kt-compact-form-row" for-id="experiment-name" label="实验名称" :error="fieldErrors.name"><input id="experiment-name" v-model="form.name" required maxlength="180"></FormField>
          <div class="kt-field kt-compact-form-row kt-experiment-bank-field">
            <strong id="experiment-bank-label">题库</strong>
            <div class="kt-experiment-bank-control">
              <div class="kt-selected-bank-tags" aria-label="已选题库">
                <span v-for="bank in selectedQuestionBanks" :key="bank.id" :data-selected-bank="bank.id">{{ bank.name }}</span>
                <span v-if="!selectedQuestionBanks.length" class="kt-muted">请选择题库</span>
              </div>
              <p v-if="fieldErrors.question_bank_ids || fieldErrors.question_bank_id" class="kt-field__error" role="alert">{{ fieldErrors.question_bank_ids || fieldErrors.question_bank_id }}</p>
            </div>
          </div>
          <FormField class="kt-compact-form-row" for-id="experiment-description" label="实验描述（可选）" :error="fieldErrors.description"><textarea id="experiment-description" v-model="form.description" rows="3" placeholder="请输入实验描述" /></FormField>
        </div>
      </section>

      <section :inert="legacySampling" class="kt-numbered-form-section" aria-labelledby="definition-selection">
        <div class="kt-numbered-form-section__heading"><span aria-hidden="true">02</span><h2 id="definition-selection">抽题设置</h2></div>
        <div class="kt-card kt-numbered-form-section__content">
          <Alert v-if="rangeError || fieldErrors.sampling_type_ranges" tone="danger">{{ rangeError || fieldErrors.sampling_type_ranges }} <button v-if="form.sampling_mode !== 'total_count'" data-clear-incompatible-ranges type="button" class="kt-text-button" @click="clearRanges">清除自定义范围</button></Alert>
          <Alert v-if="conversionWarning" tone="warning">{{ conversionWarning }}</Alert>
          <Alert v-if="incompatiblePins" tone="danger">当前数量规则不支持指定题目。已保留 {{ form.pinned_question_ids.length }} 道指定题目，请返回总题数模式或明确移除后保存。 <button data-clear-incompatible-pins type="button" class="kt-text-button" @click="form.pinned_question_ids = []">移除指定题目</button></Alert>
          <template v-if="!legacySampling && form.sampling_mode === 'total_count'">
            <FormField class="kt-compact-form-row kt-count-field" for-id="sampling-total-count" label="每位学生总题数" :error="fieldErrors.sampling_total_count"><input id="sampling-total-count" v-model.number="form.sampling_total_count" type="number" min="1"></FormField>
            <div class="kt-type-config-panel">
              <div class="kt-sitev2-allocation-heading"><h3>题型构成</h3><button class="kt-text-button" type="button" data-type-picker data-select-types aria-haspopup="dialog" @click="openTypePicker">＋ 选择题型</button></div>
              <div v-if="selectedQuestionTypes.length" class="kt-range-column-head"><span>题型</span><span>抽题范围</span><span>抽取数量</span><span /></div>
              <div class="kt-sitev2-type-rows">
                <div v-for="questionType in selectedQuestionTypes" :key="questionType.value" class="kt-sitev2-type-row kt-range-type-row">
                  <div class="kt-range-type-name"><span class="kt-sitev2-type-symbol" aria-hidden="true">{{ questionType.label.slice(0, 1) }}</span><div><strong>{{ questionType.label }}</strong><small>{{ questionTypeAvailabilityLabel(questionType.value) }}</small></div></div>
                  <div class="kt-range-cell"><button type="button" class="kt-range-trigger" :class="{ selected: form.type_ranges[questionType.value] }" :data-select-question-type="questionType.value" :aria-label="`${questionType.label}抽题范围`" aria-haspopup="dialog" @click="openRange($event, questionType.value)"><span>{{ form.type_ranges[questionType.value] ? `已选 ${form.type_ranges[questionType.value]!.length} 道候选` : '全题库随机' }}</span><small>设置范围 →</small></button><small>从{{ form.type_ranges[questionType.value] ? '选定候选题' : '当前题库与知识点范围' }}随机抽取</small></div>
                  <label class="kt-sitev2-type-quantity"><input v-model.number="form.type_quotas[questionType.value]" :aria-label="`${questionType.label}数量`" type="number" min="1" :max="form.type_ranges[questionType.value]?.length ?? questionTypeAvailable(questionType.value)">道</label>
                  <button type="button" class="kt-text-button" :aria-label="`移除${questionType.label}`" @click="setQuestionTypeSelected(questionType.value, false)">×</button>
                </div>
              </div>
              <template v-if="selectedQuestionTypes.length"><div class="kt-sitev2-allocation-meter"><span :style="{ width: `${Math.min(100, typeQuotaTotal / Math.max(1, Number(form.sampling_total_count)) * 100)}%`, background: typeQuotaTotal === form.sampling_total_count ? '#285dbb' : '#b6804b' }" /></div><div class="kt-selection-facts">已分配 {{ typeQuotaTotal }} / 总题数 {{ form.sampling_total_count || 0 }}</div></template>
              <div class="kt-type-allocation-status" :class="{ 'is-error': typeQuotaError || fieldErrors.sampling_type_quotas }"><span v-if="!selectedQuestionTypes.length && !fieldErrors.sampling_type_quotas">不限题型数量，从所选题库候选题中随机抽取。</span><span v-else-if="typeQuotaError || fieldErrors.sampling_type_quotas" role="alert">{{ typeQuotaError || fieldErrors.sampling_type_quotas }}</span><span v-else>题型构成有效，按各题型候选桶确定性抽取。</span></div>
            </div>
            <div class="kt-compact-form-row kt-pinned-question-config">
              <strong class="kt-config-label">必出题目（可选）</strong>
              <div>
                <div class="kt-inline-actions"><button type="button" class="kt-button kt-button--secondary" data-select-questions aria-haspopup="dialog" @click="openQuestionPicker($event)">选择具体题目</button><span class="kt-muted">已指定 {{ form.pinned_question_ids.length }} 道，其余随机补足至 {{ form.sampling_total_count || 0 }} 道。</span></div>
                <ul v-if="pinnedQuestions.length" class="kt-pinned-question-list"><li v-for="item in pinnedQuestions" :key="item.id"><span>{{ item.title }}<small v-if="!item.available && !inventoryLoading" class="kt-field__error"> · 已不在当前候选范围，请移除或调整范围</small></span><button class="kt-text-button" type="button" :aria-label="`移除指定题目 ${item.title}`" @click="form.pinned_question_ids = form.pinned_question_ids.filter(id => id !== item.id)">移除</button></li></ul>
                <p v-if="fieldErrors.sampling_pinned_question_ids || fieldErrors.pinned_question_ids" role="alert" class="kt-field__error">{{ fieldErrors.sampling_pinned_question_ids || fieldErrors.pinned_question_ids }}</p>
              </div>
            </div>
          </template>

          <p v-if="inventoryLoading" role="status">正在加载所选题库库存…</p>
          <div v-if="(typeRanges.length ? scopedInventory : samplingInventory)?.bank_counts?.length" class="kt-experiment-bank-inventory" aria-label="各题库候选库存">
            <span v-for="bank in (typeRanges.length ? scopedInventory : samplingInventory)?.bank_counts" :key="bank.question_bank_id">{{ bank.name }}：{{ bank.candidate_count }} 题</span>
          </div>
          <Alert v-if="inventoryError" tone="danger">{{ inventoryError }}</Alert>
          <p v-if="fieldErrors.sampling_kcs" class="kt-field__error" role="alert">{{ fieldErrors.sampling_kcs }}</p>
          <Alert v-if="samplingChanged && !legacySampling" tone="warning">抽题配置尚未保存。保存后将按所选题库、匹配层级和配额重新进行发布预检。</Alert>
          <div v-if="readiness && !legacySampling && !samplingChanged" class="kt-experiment-readiness" aria-label="已保存抽题配置库存预检">
            <Alert :tone="readiness.ready ? 'success' : 'danger'">已保存配置：计划抽取 {{ readiness.requested_count ?? '—' }} 题，题库全局候选 {{ readiness.candidate_count }} 题，尚未按学生历史过滤。<span v-if="readiness.issues.length"> {{ readiness.issues.join('；') }}</span></Alert>
            <div v-if="readiness.inventory_buckets.length" class="kt-table-scroll"><table class="kt-table"><thead><tr><th>KC</th><th>匹配层级</th><th>计划题数</th><th>可用题数</th></tr></thead><tbody><tr v-for="bucket in readiness.inventory_buckets" :key="bucket.kc_code"><td><span class="kt-mono">{{ bucket.kc_code }}</span> {{ bucket.kc_name }}</td><td>{{ bucket.match_scope === 'primary' ? '仅根 KC' : '任意层级' }}</td><td>{{ bucket.requested ?? '范围' }}</td><td>{{ bucket.available }}</td></tr></tbody></table></div>
            <small class="kt-muted">库存数据对应当前已保存版本；保存本页更改后会重新计算。</small>
          </div>
        </div>
      </section>

      <section :inert="legacySampling" class="kt-card kt-experiment-advanced-panel">
        <details class="kt-experiment-advanced" :open="form.sampling_mode !== 'total_count'">
          <summary><span>知识点范围与配额（可选）</span><span>{{ form.sampling_mode === 'kc_quota' ? '按知识点配额' : selectedConcepts.length ? `已选 ${selectedConcepts.length} 个 KC` : '全题库范围' }}</span></summary>
          <FormField for-id="sampling-advanced-mode" label="数量规则"><select id="sampling-advanced-mode" v-model="form.sampling_mode"><option value="total_count">按总题数</option><option value="kc_quota">按知识点配额</option><option value="all">全量完整题目</option></select></FormField>
          <p v-if="form.sampling_mode === 'kc_quota'" class="kt-muted">知识点配额合计决定每人题数：{{ quotaTotal }} 道。</p>
          <template v-if="form.sampling_mode === 'total_count'">          <div class="kt-kc-config-panel kt-compact-form-row"><strong class="kt-config-label">知识点范围（可选）</strong><div class="kt-kc-config-content"><div class="kt-kc-config-top"><div><span class="kt-muted">{{ selectedConcepts.length ? '已选择的 KC' : '当前使用所选题库' }}</span><div class="kt-selected-kcs"><span v-for="concept in selectedConcepts" :key="concept.id"><b class="kt-mono">{{ concept.kc_code }}</b> {{ concept.name }}</span><span v-if="!selectedConcepts.length" class="is-empty">将从所选题库全部可用题目中抽取</span></div></div><div class="kt-selection-facts">{{ selectedConcepts.length ? `已选择 ${selectedConcepts.length} 个 KC` : '全题库范围' }}</div><details class="kt-kc-picker"><summary class="kt-button kt-button--secondary">选择知识点</summary><div class="kt-kc-tree-selector"><label v-for="concept in concepts" :key="concept.id"><input v-model="form.selected_kcs[concept.id]" type="checkbox" :value="concept.id" :disabled="concept.status !== 'active'"><span><b class="kt-mono">{{ concept.kc_code }}</b> {{ concept.name }}</span><small>{{ concept.status === 'active' ? `${availableCount(concept)} 道可用` : '已归档' }}</small></label></div></details></div><div v-if="selectedConcepts.length" class="kt-match-scope"><span>匹配层级</span><label><input v-model="form.sampling_match_scope" type="radio" value="primary">根 KC</label><small>仅匹配所选节点本身</small><label><input v-model="form.sampling_match_scope" type="radio" value="any_level">任意层级</label><small>包含其在题目树中任意层级的出现</small></div></div></div></template>
          <div v-if="!legacySampling && form.sampling_mode === 'kc_quota'" class="kt-quota-panel kt-compact-form-row"><strong class="kt-config-label">知识点配额</strong><div class="kt-kc-config-content"><div class="kt-quota-toolbar"><p>按所选 KC 及匹配层级设置每位参与者题量。</p><details class="kt-kc-picker"><summary class="kt-button kt-button--secondary">＋ 添加 KC</summary><div class="kt-kc-tree-selector"><label v-for="concept in concepts" :key="concept.id"><input v-model="form.selected_kcs[concept.id]" type="checkbox" :value="concept.id" :disabled="concept.status !== 'active'"><span><b class="kt-mono">{{ concept.kc_code }}</b> {{ concept.name }}</span><small>{{ concept.status === 'active' ? `${availableCount(concept)} 道可用` : '已归档' }}</small></label></div></details></div><div class="kt-quota-table-wrap"><table class="kt-quota-table"><thead><tr><th>KC</th><th>匹配层级</th><th>可用题目</th><th>配额</th><th>操作</th></tr></thead><tbody><tr v-for="concept in selectedConcepts" :key="concept.id"><td><b class="kt-mono">{{ concept.kc_code }}</b> {{ concept.name }}</td><td><select v-model="form.kc_match_scopes[concept.id]" :aria-label="`${concept.name}匹配层级`"><option value="primary">根 KC</option><option value="any_level">任意层级</option></select></td><td>{{ availableCount(concept) }}</td><td><input v-model.number="form.kc_quotas[concept.id]" :aria-label="`${concept.name}配额`" type="number" min="1"></td><td><button class="kt-text-button kt-text-button--danger" type="button" @click="form.selected_kcs[concept.id] = false">删除</button></td></tr><tr v-if="!selectedConcepts.length"><td colspan="5" class="kt-empty-quota">请添加需要设置配额的 KC</td></tr></tbody><tfoot><tr><td colspan="5">每位参与者共抽 <strong>{{ quotaTotal }}</strong> 题</td></tr></tfoot></table></div></div></div>
          <p v-if="!legacySampling && form.sampling_mode === 'total_count'" class="kt-muted">未选择知识点时使用所选题库全部“可用”题目；选择知识点后按所选范围过滤。一篇阅读或填空材料计一道完整题目，小题完整保留并分别作答。发布时校验每位学生的题量和历史范围。</p>
        </details>
      </section>

      <section :inert="legacySampling" class="kt-numbered-form-section" aria-labelledby="definition-history">
        <div class="kt-numbered-form-section__heading"><span aria-hidden="true">03</span><h2 id="definition-history">跨实验题目控制</h2></div>
        <div class="kt-card kt-numbered-form-section__content kt-history-policy-section">
          <fieldset class="kt-history-policy-fieldset">
            <legend>历史题目策略</legend>
            <div class="kt-history-policy-grid">
              <label data-history-policy><input v-model="form.cross_experiment_item_policy" name="cross_experiment_item_policy" type="radio" value="exclude_previous"><span>排除已提交或已分配题目</span></label>
              <label data-history-policy><input v-model="form.cross_experiment_item_policy" name="cross_experiment_item_policy" type="radio" value="mixed_review"><span>混合复测</span></label>
              <label data-history-policy><input v-model="form.cross_experiment_item_policy" name="cross_experiment_item_policy" type="radio" value="unrestricted"><span>不限制历史题目</span></label>
            </div>
          </fieldset>
          <div v-if="form.cross_experiment_item_policy === 'mixed_review'" class="kt-review-count-row">
            <FormField for-id="experiment-review-count" label="复测题数" :error="fieldErrors.review_count || reviewCountError"><input id="experiment-review-count" v-model.number="form.review_count" type="number" min="1" :max="Math.max(1, assignmentTotal - 1)"></FormField>
            <p data-review-summary>本次共 <strong>{{ assignmentTotal }}</strong> 题，其中复测 <strong>{{ Number(form.review_count || 0) }}</strong> 题，新题 <strong>{{ Math.max(0, assignmentTotal - Number(form.review_count || 0)) }}</strong> 题。</p>
          </div>
          <p class="kt-history-policy-note">{{ historyPolicyDescription }}</p>
          <div class="kt-history-resource-strip" role="status">
            <div class="kt-history-resource-strip__heading"><strong>个体题目资源检查</strong><small>选择班级后执行逐人检查</small></div>
            <span>题库全局候选 <b>{{ (typeRanges.length ? scopedInventory : samplingInventory)?.candidate_count ?? '—' }}</b> 题，尚未按学生历史过滤</span>
            <span>本次需要 <b>{{ assignmentTotal || '—' }}</b> 题</span>
            <span>选择参与班级后，将检查每名学生的历史题、新题和有效任务占用。</span>
          </div>
        </div>
      </section>

      <section :inert="legacySampling" class="kt-numbered-form-section" aria-labelledby="definition-runtime">
        <div class="kt-numbered-form-section__heading"><span aria-hidden="true">04</span><h2 id="definition-runtime">作答与反馈</h2></div>
        <div ref="runtimeEditor" class="kt-card kt-numbered-form-section__content kt-runtime-primary">
          <div class="kt-runtime-summary">
            <Icon aria-hidden="true" class="kt-runtime-summary__icon" name="definition" :size="18" />
            <strong>当前配置：</strong>
            <span>{{ form.time_mode === 'none' ? '无时限' : (form.time_mode === 'total' ? '实验总时限' : '单题时限') }} / {{ form.resume_mode === 'resumable' ? '允许续答' : '必须一次完成' }} / {{ form.feedback_mode === 'none' ? '仅显示已提交' : (form.feedback_mode === 'correctness' ? '显示正误' : '显示答案与解析') }}</span>
            <button type="button" class="kt-runtime-summary__edit" @click="focusRuntimeEditor">✎ <span>编辑</span></button>
          </div>
          <div class="kt-runtime-settings-grid">
            <section class="kt-runtime-settings-card" aria-label="作答控制">
              <fieldset class="kt-radio-group kt-runtime-row"><legend>题目顺序</legend><div class="kt-radio-options"><label><input v-model="form.ordering_mode" name="ordering_mode" type="radio" value="fixed"><span>固定顺序</span></label><label><input v-model="form.ordering_mode" name="ordering_mode" type="radio" value="random"><span>随机顺序</span></label><label><input v-model="form.ordering_mode" name="ordering_mode" type="radio" value="stratified_random"><span>分层随机</span></label></div></fieldset>
              <div class="kt-runtime-divider" />
              <fieldset class="kt-radio-group kt-runtime-row"><legend>时限</legend><div class="kt-radio-options"><label><input v-model="form.time_mode" name="time_mode" type="radio" value="none"><span>无时限</span></label><label><input v-model="form.time_mode" name="time_mode" type="radio" value="total"><span>实验总时限</span></label></div><p v-if="fieldErrors.time_mode" class="kt-field__error" role="alert">{{ fieldErrors.time_mode }}</p></fieldset>
              <Alert v-if="form.time_mode === 'per_question'" tone="warning">此历史草稿使用单题计时（{{ form.per_question_time_limit_seconds }} 秒）。请明确选择无时限或实验总时限后再保存。</Alert>
              <div v-if="form.time_mode === 'total'" class="kt-runtime-time-fields">
                <FormField for-id="experiment-total-time" label="总时限（分钟）" :error="fieldErrors.total_time_limit_minutes"><input id="experiment-total-time" v-model.number="form.total_time_limit_minutes" type="number" min="1"><small>完成整个实验的最长作答时间</small></FormField>
              </div>
              <div class="kt-runtime-divider" />
              <fieldset class="kt-radio-group kt-runtime-row kt-runtime-resume-row"><legend>中断后继续</legend><div class="kt-radio-options"><label><input v-model="form.resume_mode" name="resume_mode" type="radio" value="resumable"><span>允许中断续答</span></label><label><input v-model="form.resume_mode" name="resume_mode" type="radio" value="single_session"><span>必须一次完成</span></label></div><p>学生在作答过程中退出后，可在有效期内继续作答。</p><p v-if="fieldErrors.resume_mode" class="kt-field__error" role="alert">{{ fieldErrors.resume_mode }}</p></fieldset>
            </section>
            <section class="kt-runtime-settings-card" aria-labelledby="runtime-feedback-title">
              <header><h3 id="runtime-feedback-title">反馈设置</h3></header>
              <fieldset class="kt-radio-group kt-runtime-row"><legend>反馈</legend><div class="kt-radio-options"><label><input v-model="form.feedback_mode" name="feedback_mode" type="radio" value="none"><span>仅显示已提交</span></label><label><input v-model="form.feedback_mode" name="feedback_mode" type="radio" value="correctness"><span>显示正误</span></label><label><input v-model="form.feedback_mode" name="feedback_mode" type="radio" value="full"><span>显示正误、答案与解析</span></label></div><p>反馈内容按下方选择的发布时间开放。</p></fieldset>
              <div class="kt-runtime-divider" />
              <fieldset class="kt-radio-group kt-runtime-row"><legend>反馈发布</legend><div class="kt-radio-options"><label><input v-model="form.feedback_release_policy" name="feedback_release_policy" type="radio" value="after_personal_submission"><span>学生作答结束后（完成或超时）</span></label><label><input v-model="form.feedback_release_policy" name="feedback_release_policy" type="radio" value="after_assignment_deadline"><span>班级任务截止后</span></label><label><input v-model="form.feedback_release_policy" name="feedback_release_policy" type="radio" value="manual_release"><span>教师人工发布</span></label></div><p>选择反馈内容的发布时间点。</p></fieldset>
            </section>
          </div>
        </div>
      </section>

      <section :inert="legacySampling" class="kt-numbered-form-section" aria-labelledby="definition-collection">
        <div class="kt-numbered-form-section__heading"><span aria-hidden="true">05</span><h2 id="definition-collection">研究设置</h2></div>
        <div class="kt-card kt-numbered-form-section__content kt-research-settings-card">
          <fieldset class="kt-radio-group kt-runtime-row"><legend>作答信心采集</legend><div class="kt-radio-options"><label><input v-model="form.confidence_mode" name="confidence_mode" type="radio" value="off"><span>不采集</span></label><label><input v-model="form.confidence_mode" name="confidence_mode" type="radio" value="all"><span>全部采集</span></label><label><input v-model="form.confidence_mode" name="confidence_mode" type="radio" value="sampled"><span>抽样采集</span></label></div><p v-if="fieldErrors.confidence_mode" class="kt-field__error" role="alert">{{ fieldErrors.confidence_mode }}</p></fieldset>
          <div v-if="form.confidence_mode !== 'off'" class="kt-form-grid"><fieldset class="kt-radio-group kt-compact-form-row"><legend>采集单元</legend><div class="kt-radio-options"><label><input v-model="form.confidence_sampling_unit" name="confidence_sampling_unit" type="radio" value="student"><span>学生</span></label><label><input v-model="form.confidence_sampling_unit" name="confidence_sampling_unit" type="radio" value="session"><span>会话</span></label><label><input v-model="form.confidence_sampling_unit" name="confidence_sampling_unit" type="radio" value="attempt"><span>作答</span></label></div><p v-if="fieldErrors.confidence_sampling_unit" class="kt-field__error" role="alert">{{ fieldErrors.confidence_sampling_unit }}</p></fieldset><FormField v-if="form.confidence_mode === 'sampled'" class="kt-compact-form-row" for-id="experiment-confidence-probability" label="抽样概率（0–1）" :error="fieldErrors.confidence_probability"><input id="experiment-confidence-probability" v-model.number="form.confidence_probability" type="number" min="0" max="1" step="0.001"></FormField></div>
          <ResearchToolsEditor v-model="researchTools" />
        </div>
      </section>

      <div class="kt-form-actions kt-experiment-form-actions">
        <span class="kt-muted">保存定义后确认班级安排与逐人检查。</span>
        <Button type="button" :disabled="saving || legacySampling || typeQuotaInvalid" @click="submit('publish')">{{ saving ? '正在保存…' : '保存并进入班级排期 →' }}</Button>
      </div>
    </form>
    <aside class="kt-experiment-config-summary" aria-label="当前实验配置">
      <section class="kt-card kt-sitev2-summary-panel"><header class="kt-sitev2-summary-top"><h2>本次实验</h2><div class="kt-sitev2-big-number">{{ assignmentTotal }}<small>道题</small></div><p>一篇阅读或填空文章，计为一道题。</p></header><dl><div><dt>每位学生</dt><dd>{{ assignmentTotal }} 道</dd></div><div><dt>题库</dt><dd>{{ selectedQuestionBanks.length }} 个</dd></div><div><dt>指定题目</dt><dd>{{ form.pinned_question_ids.length }} 道</dd></div><div><dt>随机补足</dt><dd>{{ Math.max(0, assignmentTotal - form.pinned_question_ids.length) }} 道</dd></div><div><dt>题型构成</dt><dd>{{ form.sampling_mode === 'kc_quota' ? '按知识点配额' : selectedQuestionTypes.map(item => `${item.label} ${form.type_quotas[item.value]}`).join('、') || '不限题型' }}</dd></div><div><dt>实验限时</dt><dd>{{ form.time_mode === 'total' ? `${form.total_time_limit_minutes || '—'} 分钟` : form.time_mode === 'none' ? '无时限' : '待重新选择' }}</dd></div></dl><footer class="kt-sitev2-summary-bottom"><Button type="button" :disabled="saving || legacySampling || typeQuotaInvalid" @click="submit('publish')">保存并进入班级排期 →</Button><p>保存定义后确认班级安排与逐人检查。</p></footer></section>
      <section class="kt-card"><h2>候选题目</h2><dl><div><dt>当前范围</dt><dd>{{ (typeRanges.length ? scopedInventory : samplingInventory)?.candidate_count ?? '—' }} 道</dd></div><div v-for="bank in (typeRanges.length ? scopedInventory : samplingInventory)?.bank_counts || []" :key="bank.question_bank_id"><dt>{{ bank.name }}</dt><dd>{{ bank.candidate_count }} 道</dd></div></dl><p>选择班级后，发布检查将逐人核验历史题目与分配。</p></section>
    </aside>
  </div>
  <SamplingRangePicker v-if="rangeType" :question-type="rangeType" :label="questionTypeLabel(rangeType)" :course-id="courseId" :quota="form.type_quotas[rangeType]" :selected="form.type_ranges[rangeType] ?? null" :questions="samplingInventory?.questions || []" :banks="questionBanks" :loading="inventoryLoading" :error="inventoryError" @close="rangeType = ''" @apply="applyRange" />
  <ModalDialog :open="bankPickerOpen" title="选择题库" @close="bankPickerOpen = false">
    <p class="kt-picker-note">题型将根据所选题库的可用题目更新。合并题库抽题，不保证每个题库都被抽中。</p>
    <label class="kt-picker-search">搜索题库<input id="experiment-bank-search" v-model="bankSearch" type="search" placeholder="输入题库名称"></label>
    <p v-if="bankCountsError" role="alert">{{ bankCountsError }}</p>
    <label v-for="bank in filteredQuestionBanks" :key="bank.id" class="kt-picker-choice">
      <input v-model="pendingBankIds" name="question_bank_choice" :aria-label="bank.name" type="checkbox" :value="bank.id">
      <span><strong>{{ bank.name }}</strong><small>课程题库</small></span>
      <small class="kt-picker-choice-count">{{ bankCountsLoading ? '读取中…' : `${bankCounts?.find(item => item.question_bank_id === bank.id)?.candidate_count ?? '—'} 道可用` }}</small>
    </label>
    <p v-if="!filteredQuestionBanks.length" class="kt-muted">{{ questionBanks.length ? '没有匹配的题库' : '当前课程暂无可选题库' }}</p>
    <template #footer><button class="kt-button kt-button--secondary" type="button" data-cancel-selection @click="bankPickerOpen = false">取消</button><button class="kt-button" type="button" data-apply-selection @click="applyBanks">应用题库</button></template>
  </ModalDialog>
  <ModalDialog :open="typePickerOpen" title="选择题型" @close="typePickerOpen = false">
    <p class="kt-picker-note">仅列出当前所选范围有可用题目的题型。取消不会改变已设置的题型数量。</p>
    <p v-if="inventoryLoading" role="status">正在读取题型…</p>
    <p v-else-if="inventoryError" role="alert">{{ inventoryError }}</p>
    <p v-else-if="!availableQuestionTypes.length" class="kt-muted">所选范围暂无可用题型。</p>
    <label v-for="item in availableQuestionTypes" :key="item.value" class="kt-picker-choice">
      <input v-model="pendingTypes" name="question_type_choice" :aria-label="item.label" type="checkbox" :value="item.value">
      <span class="kt-picker-symbol" aria-hidden="true">{{ item.label.slice(0, 1) }}</span>
      <span><strong>{{ item.label }}</strong><small>{{ ['reading', 'words', 'cloze'].includes(item.value) ? '包含共享材料与全部小题，计为一道完整题' : '独立题目' }}</small></span>
      <small class="kt-picker-choice-count">{{ questionTypeAvailabilityLabel(item.value) }}</small>
    </label>
    <template #footer><button class="kt-button kt-button--secondary" type="button" data-cancel-selection @click="typePickerOpen = false">取消</button><button class="kt-button" type="button" data-apply-selection :disabled="inventoryLoading || Boolean(inventoryError)" @click="applyTypes">应用题型</button></template>
  </ModalDialog>
  <ModalDialog :open="questionPickerOpen" :title="questionTypeFilter ? `选择具体题目 · ${questionTypeLabel(questionTypeFilter)}` : '选择具体题目'" wide @close="questionPickerOpen = false">
    <div class="kt-picker-toolbar"><label class="kt-picker-search">搜索题目<input v-model="questionSearch" type="search" placeholder="编号、标题或题干"></label><span>已指定 {{ pendingPins.length }} / {{ pickerQuota }} 道</span></div>
    <p class="kt-picker-note">可以只指定部分题目，其余由系统随机补足；取消全部指定即为全部随机。阅读、选词填空与短文填空按完整题目计数并保留全部小题。</p>
    <p v-if="invalidPendingPins.length" class="kt-picker-error" role="alert">{{ invalidPendingPins.length }} 道已指定题目不在当前可用范围内。<button class="kt-text-button" type="button" data-clear-invalid-pins @click="pendingPins = pendingPins.filter(id => !invalidPendingPins.includes(id)); pinError = ''">清除失效题目</button></p>
    <p v-if="pinError" class="kt-picker-error" role="alert">{{ pinError }}</p>
    <p v-if="inventoryLoading" role="status">正在读取候选题目…</p>
    <p v-else-if="inventoryError" class="kt-picker-error" role="alert">{{ inventoryError }}</p>
    <div v-for="item in questionPickerRows" :key="item.question_id" class="kt-picker-question" :class="{ selected: pendingPins.includes(item.question_id) }">
      <label class="kt-picker-choice"><input v-model="pendingPins" type="checkbox" :value="item.question_id" :data-pin-question="item.question_id"><span><strong>{{ item.title || item.code }}</strong><small>{{ questionBanks.find(bank => bank.id === item.question_bank_id)?.name }} · {{ questionTypeLabel(item.question_type) }} · {{ item.item_count }} 个作答项 · {{ item.code }}</small></span></label>
      <details @toggle="($event.target as HTMLDetailsElement).open && !previewIds.includes(item.question_id) && previewIds.push(item.question_id)"><summary>查看题目</summary><ExperimentQuestionPreview v-if="previewIds.includes(item.question_id)" :course-id="courseId" :bank-id="item.question_bank_id" :question-id="item.question_id" /></details>
    </div>
    <p v-if="!inventoryLoading && !filteredQuestions.length" class="kt-muted">当前范围没有匹配的可用题目。</p>
    <Paginator :page="questionPickerPage" :total-pages="questionPickerPages" @navigate="questionPickerPage = $event" />
    <template #footer><button class="kt-button kt-button--secondary" type="button" data-cancel-selection @click="questionPickerOpen = false">取消</button><button class="kt-button kt-button--secondary" type="button" data-all-random @click="pendingPins = []; pinError = ''">全部随机</button><button class="kt-button" type="button" data-apply-selection :disabled="inventoryLoading || Boolean(inventoryError)" @click="applyPins">确认指定题目</button></template>
  </ModalDialog>
</template>

<style scoped>
.kt-experiment-readiness{min-width:0}
.kt-pinned-question-list,.kt-question-picker-list{list-style:none;padding:0;margin:12px 0}.kt-pinned-question-list li{display:flex;justify-content:space-between;gap:16px;padding:6px 0;border-bottom:1px solid var(--kt-border)}.kt-question-picker-filters{display:grid;grid-template-columns:1fr 150px;gap:12px;margin:18px 0}.kt-question-picker-filters label{display:grid;gap:6px;font-size:14px}.kt-question-picker-list li{border-bottom:1px solid var(--kt-border);padding:12px 0}.kt-question-picker-list label{display:flex;align-items:flex-start;gap:12px;cursor:pointer}.kt-question-picker-list label>span{display:grid;gap:5px;min-width:0}.kt-question-picker-list small{color:var(--kt-text-muted)}.kt-question-picker-list label>span>span{font-size:14px;white-space:pre-line;overflow-wrap:anywhere;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}.kt-question-picker-list input{margin-top:4px}.kt-question-picker-list strong{font-size:15px}@media(max-width:600px){.kt-question-picker-filters{grid-template-columns:1fr}}
</style>
