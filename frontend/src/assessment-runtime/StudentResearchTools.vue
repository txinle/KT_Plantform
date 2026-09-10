<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef } from 'vue'
import { getStudentAssessmentApi } from './api'
import type { StudentProcessRuntime } from './process-bridge'
import type { ToolRule } from '@/features/teacher-experiments/research-tools'

const props = defineProps<{
  endpoint: string
  runtime: StudentProcessRuntime
  answerEventId: string | null
  waitForCapture: () => Promise<unknown>
}>()
const emit = defineEmits<{ interaction: [] }>()
interface ConfidenceScale {
  scale_version: string
  minimum: number
  maximum: number
  step: number
}
const available = ref<{
  doubt_enabled?: boolean
  check_enabled?: boolean
  hints?: { id: string; level: number }[]
  confidence_scale?: ConfidenceScale
}>({})
const activeRules = ref<ToolRule[]>([])
const displayed = new Set<string>()
const doubt = ref(false)
const pendingCheck = ref(false)
const checking = ref(false)
const doubtNote = ref('')
const probability = ref(50)
const probabilityInput = ref('50')
const probabilityTouched = ref(false)
const probabilityError = ref('')
const confidenceScale = computed(() => available.value.confidence_scale || {
  scale_version: 'v1', minimum: 0, maximum: 100, step: 10,
})
const hint = shallowRef<{ text: string; source: ToolResponse; data: Record<string, unknown>; initiation: string }>()
const error = ref('')
const contentVersion = ref('')
let timer: ReturnType<typeof setInterval> | undefined
interface ToolResponse { event_id: string; content_version: string; text?: string }
type DueRule = ToolRule & { pending_request?: ToolResponse; already_presented?: boolean }
type QueuedHint = { id: string; rule?: DueRule }
const queuedHints: QueuedHint[] = []
let hintTransitions = 0
let unmounted = false

async function action(tool: string, stage: string, data: Record<string, unknown> = {}, rule?: ToolRule) {
  const operationId = crypto.randomUUID()
  const answerEventId = props.answerEventId
  const effectiveData = rule ? { ...data, rule_id: rule.id } : data
  const initiation = rule ? (rule.required ? 'task_required' : 'system_prompted') : 'self_initiated'
  if (['mark_added', 'mark_changed', 'mark_removed', 'started', 'finished', 'responded'].includes(stage) || (stage === 'requested' && !rule)) emit('interaction')
  await props.runtime.record('research_tool_requested', {
    capture_version: 'capture-v2', operation_id: operationId, tool, stage, data: effectiveData,
    answer_event_id: answerEventId, content_version: contentVersion.value, initiation_type: initiation,
  }, { eventId: crypto.randomUUID() })
  await props.waitForCapture()
  await props.runtime.flushBounded(1500)
  return getStudentAssessmentApi().post<ToolResponse>(props.endpoint, {
    tool, stage, operation_id: operationId, data: effectiveData,
    answer_event_id: answerEventId, initiation_type: initiation,
  })
}

async function observed(source: ToolResponse, tool: string, stage: string, data: Record<string, unknown>, initiation: string) {
  await props.runtime.record('research_tool_observed', {
    capture_version: 'capture-v2', operation_id: crypto.randomUUID(), server_event_id: source.event_id,
    tool, stage, data, initiation_type: initiation, answer_event_id: props.answerEventId,
    content_version: source.content_version,
  })
}

async function run(operation: () => Promise<unknown>) {
  try { error.value = ''; await operation() } catch (caught) { error.value = caught instanceof Error ? caught.message : '工具记录失败，请重试。' }
}

async function toggleMark(tool: 'doubt' | 'check') {
  const flag = tool === 'doubt' ? doubt : pendingCheck
  await action(tool, flag.value ? 'mark_removed' : 'mark_added')
  flag.value = !flag.value
}

async function check(rule?: ToolRule) {
  await action('check', checking.value ? 'finished' : 'started', {}, rule)
  checking.value = !checking.value
  if (!checking.value && rule) activeRules.value = activeRules.value.filter((row) => row.id !== rule.id)
}

async function openHint(id: string, rule?: DueRule) {
  if (unmounted) return
  queuedHints.push({ id, rule })
  await showNextHint()
}

async function showNextHint() {
  if (unmounted || hint.value || hintTransitions) return
  const nextHint = queuedHints.shift()
  if (!nextHint) return
  hintTransitions += 1
  const { id, rule } = nextHint
  const data = { hint_id: id, ...(rule ? { rule_id: rule.id } : {}) }
  const initiation = rule ? (rule.required ? 'task_required' : 'system_prompted') : 'self_initiated'
  try {
    const source = rule?.pending_request || await action('hint', 'requested', { hint_id: id }, rule)
    if (unmounted) return
    hint.value = { text: source.text || '', source, data, initiation }
    await nextTick()
    if (unmounted) return
    await observed(source, 'hint', 'presented', data, initiation)
    if (!rule?.already_presented) await action('hint', 'presented', { hint_id: id }, rule)
  } finally {
    hintTransitions -= 1
    if (!unmounted && !hintTransitions && !hint.value && queuedHints.length) void run(showNextHint)
  }
}

async function closeHint() {
  if (!hint.value) return
  const displayedHint = hint.value
  hint.value = undefined
  hintTransitions += 1
  try {
    await observed(displayedHint.source, 'hint', 'closed', displayedHint.data, displayedHint.initiation)
    const rule = activeRules.value.find((row) => row.id === displayedHint.data.rule_id)
    if (rule) {
      await action('hint', 'responded', { hint_id: rule.hint_id }, rule)
      activeRules.value = activeRules.value.filter((row) => row.id !== rule.id)
    }
  } finally {
    hintTransitions -= 1
  }
  if (!unmounted) await showNextHint()
}

async function respondConfidence(rule: ToolRule) {
  if (!probabilityTouched.value || probabilityError.value) return
  await action('confidence', 'responded', { probability: probability.value }, rule)
  activeRules.value = activeRules.value.filter((row) => row.id !== rule.id)
}

function selectConfidenceFromRange() {
  probabilityInput.value = String(probability.value)
  probabilityError.value = ''
  probabilityTouched.value = true
}

function selectConfidenceFromNumber(event: Event) {
  const rawValue = (event.target as HTMLInputElement).value
  probabilityInput.value = rawValue
  const scale = confidenceScale.value
  if (rawValue.trim() === '') {
    probabilityError.value = '请输入作答信心。'
    return
  }
  const value = Number(rawValue)
  if (!Number.isInteger(value)) {
    probabilityError.value = '作答信心必须是整数。'
    return
  }
  if (value < scale.minimum || value > scale.maximum) {
    probabilityError.value = `请输入 ${scale.minimum} 到 ${scale.maximum} 之间的整数。`
    return
  }
  if ((value - scale.minimum) % scale.step !== 0) {
    probabilityError.value = `当前量表要求以 ${scale.step}% 为间隔。`
    return
  }
  probability.value = value
  probabilityError.value = ''
  probabilityTouched.value = true
}

async function refresh() {
  if (unmounted || !navigator.onLine) return
  const response = await getStudentAssessmentApi().post<typeof available.value & { rules: DueRule[]; content_version: string;
    state?: { doubt: boolean; pending_check: boolean; checking: boolean; doubt_note: string } }>(props.endpoint, {})
  if (unmounted) return
  contentVersion.value = response.content_version
  available.value = response
  if (response.state) {
    doubt.value = response.state.doubt
    pendingCheck.value = response.state.pending_check
    checking.value = response.state.checking
    doubtNote.value = response.state.doubt_note
  }
  for (const rule of response.rules) {
    if (unmounted) return
    if (displayed.has(rule.id)) continue
    displayed.add(rule.id)
    if (rule.tool === 'hint') {
      activeRules.value.push(rule)
      await openHint(rule.hint_id, rule)
      continue
    }
    const source = rule.pending_request || await action(rule.tool, 'requested', {}, rule)
    if (rule.tool === 'confidence') {
      probability.value = 50
      probabilityInput.value = '50'
      probabilityTouched.value = false
      probabilityError.value = ''
    }
    activeRules.value.push(rule)
    await nextTick()
    const initiation = rule.required ? 'task_required' : 'system_prompted'
    await observed(source, rule.tool, 'presented', { rule_id: rule.id }, initiation)
    if (!rule.already_presented) await action(rule.tool, 'presented', {}, rule)
  }
}
onMounted(() => { void run(refresh); timer = setInterval(() => { void run(refresh) }, 10000) })
onBeforeUnmount(() => {
  unmounted = true
  queuedHints.length = 0
  if (timer) clearInterval(timer)
})
</script>

<template>
  <section v-if="available.doubt_enabled || available.check_enabled || available.hints?.length || activeRules.length" class="kt-research-tools" aria-label="题目工具">
    <button v-if="available.doubt_enabled" class="kt-button kt-button--secondary" type="button" :aria-pressed="doubt" @click="run(() => toggleMark('doubt'))">{{ doubt ? '解除疑点标记' : '标记疑点' }}</button>
    <label v-if="doubt">疑点备注（可选）<input v-model="doubtNote" type="text" @change="run(() => action('doubt', 'mark_changed', { note: doubtNote }))"></label>
    <button v-if="available.check_enabled" class="kt-button kt-button--secondary" type="button" :aria-pressed="pendingCheck" @click="run(() => toggleMark('check'))">{{ pendingCheck ? '解除待检查' : '待检查' }}</button>
    <button v-if="available.check_enabled" class="kt-button kt-button--secondary" type="button" @click="run(() => check())">{{ checking ? '结束检查' : '开始检查' }}</button>
    <button v-for="item in available.hints" :key="item.id" class="kt-button kt-button--secondary" type="button" @click="run(() => openHint(item.id))">查看第 {{ item.level }} 级提示</button>
    <div v-for="rule in activeRules" :key="rule.id" class="kt-alert kt-alert--info">
      <p>{{ rule.required ? '本题要求完成' : '作答提醒' }}：{{ rule.tool === 'confidence' ? '信心评价' : rule.tool === 'check' ? '检查答案' : '查看提示' }}</p>
      <button v-if="rule.tool === 'check'" class="kt-button" type="button" @click="run(() => check(rule))">{{ checking ? '完成检查' : '开始检查' }}</button>
      <template v-if="rule.tool === 'confidence'">
        <label>你认为当前答案正确的概率：{{ probability }}%<input v-model.number="probability" data-tool-confidence-range type="range" :min="confidenceScale.minimum" :max="confidenceScale.maximum" :step="confidenceScale.step" @input="selectConfidenceFromRange"></label>
        <label>信心百分比<input data-tool-confidence-number type="number" inputmode="numeric" :value="probabilityInput" :min="confidenceScale.minimum" :max="confidenceScale.maximum" :step="confidenceScale.step" :aria-invalid="probabilityError ? 'true' : undefined" @input="selectConfidenceFromNumber">%</label>
        <p v-if="probabilityError" class="kt-field__error" role="alert" data-tool-confidence-error>{{ probabilityError }}</p>
        <button class="kt-button" type="button" :disabled="!probabilityTouched || !!probabilityError" @click="run(() => respondConfidence(rule))">记录信心</button>
      </template>
    </div>
    <div v-if="hint" class="kt-alert kt-alert--info"><p>{{ hint.text }}</p><button class="kt-button kt-button--secondary" type="button" @click="run(closeHint)">关闭提示</button></div>
  </section>
  <p v-if="error" class="kt-alert kt-alert--danger" role="alert">{{ error }}</p>
</template>
