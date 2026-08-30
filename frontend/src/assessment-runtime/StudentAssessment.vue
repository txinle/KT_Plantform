<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'

import { getBootstrapContext } from '@/app/bootstrap'
import { ApiError } from '@/api/client'
import Drawer from '@/design-system/components/Drawer.vue'
import FocusAssessmentHeader from '@/design-system/components/FocusAssessmentHeader.vue'
import type {
  DraftMutationResponse,
  StudentRuntimeQuestionEntry,
  SubmissionResponse,
} from './api'
import { getStudentAssessmentApi } from './api'
import { createProcessRuntime, type StudentProcessRuntime } from './process-bridge'
import {
  createTextObservation,
  materializeTextObservation,
  mergeTextObservation,
  type TextObservation,
} from './text-observation'
import './student-assessment.css'

const route = useRoute()
const router = useRouter()
const state = ref<StudentRuntimeQuestionEntry>()
const answer = ref<unknown>(null)
const loading = ref(true)
const error = ref('')
const saveStatus = ref('尚未保存')
const missingCapabilities = ref<string[]>([])
const secondsRemaining = ref<number | null>(null)
const submitDialogOpen = ref(false)
const finishDialogOpen = ref(false)
const submitting = ref(false)
const finishing = ref(false)
const timedOut = ref(false)
const online = ref(globalThis.navigator.onLine)
const pendingConfidence = ref<ConfidenceContract>()
const confidenceValue = ref(50)
const confidenceStatus = ref('等待评价')
const confidenceResolving = ref(false)
const feedback = ref<SubmissionResponse>()
const navigationOpen = ref(false)
let countdown: ReturnType<typeof globalThis.setInterval> | undefined
let textSaveTimer: ReturnType<typeof globalThis.setTimeout> | undefined
let textObservationTimer: ReturnType<typeof globalThis.setTimeout> | undefined
let lastSavedAnswer = 'null'
let saveQueue = Promise.resolve<unknown>(undefined)
let inputStarted = false
let processRuntime: StudentProcessRuntime | undefined
let confidencePromptPromise: Promise<unknown> | undefined
let lastRecordedResponseState: unknown = null
let lastStableText = ''
let pendingTextObservation: TextObservation | undefined
let routeTransitionPrepared = false

interface ConfidenceContract {
  confidence_assignment_id: string
  scale_version: string
  prompt_version: string
  minimum: number
  maximum: number
  step: number
  prompt_shown_url: string
  commit_url: string
  skip_url: string
}

const sequenceLabel = computed(() => {
  if (!state.value) return ''
  return `第 ${state.value.assignment.sequence_index}/${state.value.progress.total} 题`
})
const answerLocked = computed(() => (
  !!state.value
  && (
    timedOut.value
    || ['submitted', 'timed_out', 'unanswered'].includes(state.value.assignment.status)
  )
))

function statusLabel(status: string) {
  return {
    unvisited: '未访问',
    visited: '已访问',
    draft: '已有草稿',
    skipped: '已跳过',
    submitted: '已提交',
    timed_out: '已超时',
    unanswered: '未作答',
  }[status] || status
}

function timerLabel(value: number) {
  const minutes = Math.floor(value / 60).toString().padStart(2, '0')
  const seconds = (value % 60).toString().padStart(2, '0')
  return `${minutes}:${seconds}`
}

function confidenceContract(value: unknown): ConfidenceContract | undefined {
  if (!value || typeof value !== 'object') return undefined
  const candidate = value as Record<string, unknown>
  const requiredStrings = [
    'confidence_assignment_id',
    'scale_version',
    'prompt_version',
    'prompt_shown_url',
    'commit_url',
    'skip_url',
  ]
  const requiredNumbers = ['minimum', 'maximum', 'step']
  if (
    requiredStrings.some((key) => typeof candidate[key] !== 'string')
    || requiredNumbers.some((key) => typeof candidate[key] !== 'number')
  ) return undefined
  return candidate as unknown as ConfidenceContract
}

function formatCorrectAnswer(value: unknown) {
  if (state.value?.question.question_type === 'true_false' && typeof value === 'boolean') {
    return value ? '正确' : '错误'
  }
  if (Array.isArray(value)) return value.join('、')
  return value === null || value === undefined ? '' : String(value)
}

function checkCapabilities() {
  const missing: string[] = []
  if (!globalThis.indexedDB) missing.push('IndexedDB')
  if (!globalThis.fetch) missing.push('Fetch')
  if (!globalThis.AbortController) missing.push('AbortController')
  if (!globalThis.crypto?.subtle || !globalThis.TextEncoder) missing.push('Web Crypto')
  missingCapabilities.value = missing
  return missing.length === 0
}

function startCountdown() {
  if (secondsRemaining.value === null) return
  if (countdown !== undefined) globalThis.clearInterval(countdown)
  countdown = globalThis.setInterval(() => {
    if (secondsRemaining.value === null) return
    secondsRemaining.value = Math.max(0, secondsRemaining.value - 1)
    if (secondsRemaining.value === 0 && !timedOut.value) {
      timedOut.value = true
      error.value = '本题或实验时间已结束，页面将刷新并同步服务器状态。'
      if (countdown !== undefined) globalThis.clearInterval(countdown)
      void flushTextObservation().then(() => processRuntime?.closeAttempt('timeout')).finally(() => {
        globalThis.setTimeout(() => globalThis.location.reload(), 1_200)
      })
    }
  }, 1000)
}

async function noteInputStarted() {
  if (inputStarted || !state.value) return
  inputStarted = true
  await processRuntime?.record('response_input_started', {
    question_type: state.value.question.question_type,
  })
}

async function flushTextObservation() {
  if (textObservationTimer !== undefined) globalThis.clearTimeout(textObservationTimer)
  textObservationTimer = undefined
  const observation = pendingTextObservation
  pendingTextObservation = undefined
  if (!observation || observation.before === observation.after || !state.value) return
  lastStableText = observation.after
  const payload = await materializeTextObservation(
    state.value.question.question_type,
    observation,
  )
  if (payload) await recordProcessEvent('text_edit_observed', payload)
}

function queueTextObservation(value: string, event: unknown) {
  const details = event as { inputType?: string; isComposing?: boolean }
  if (details.isComposing) return
  const monotonicMs = Math.round(globalThis.performance.now() * 1000) / 1000
  const next = createTextObservation(lastStableText, value, {
    inputType: details.inputType,
    isComposing: details.isComposing,
    monotonicMs,
  })
  if (pendingTextObservation && pendingTextObservation.operation !== next.operation) {
    void flushTextObservation()
  }
  pendingTextObservation = pendingTextObservation
    ? mergeTextObservation(pendingTextObservation, value, {
        inputType: details.inputType,
        isComposing: details.isComposing,
        monotonicMs,
      })
    : next
  if (textObservationTimer !== undefined) globalThis.clearTimeout(textObservationTimer)
  textObservationTimer = globalThis.setTimeout(() => {
    void flushTextObservation()
  }, 800)
}

function hasMeaningfulResponse(value: unknown) {
  if (Array.isArray(value)) return value.length > 0
  return value !== null && value !== undefined && String(value) !== ''
}

function cloneJson<T>(value: T): T {
  if (value === undefined) return value
  return JSON.parse(JSON.stringify(value)) as T
}

async function recordProcessEvent(
  eventType: string,
  payload: Record<string, unknown>,
  options: Record<string, unknown> = {},
) {
  try {
    return await processRuntime?.record(eventType, payload, options)
  } catch {
    return undefined
  }
}

async function recordChoiceState(questionType: string, nextState: unknown, force: boolean) {
  const changed = JSON.stringify(nextState) !== JSON.stringify(lastRecordedResponseState)
  const shouldRecord = changed && (
    lastRecordedResponseState !== null || hasMeaningfulResponse(nextState)
  )
  if (!shouldRecord) return
  const recorded = await recordProcessEvent('response_state_changed', {
    question_type: questionType,
    response_state_before: lastRecordedResponseState,
    response_state_after: nextState,
    change_source: force ? 'forced_snapshot' : 'student',
  })
  if (recorded) lastRecordedResponseState = cloneJson(nextState)
}

async function recordTextSnapshot(
  response: DraftMutationResponse,
  force: boolean,
) {
  if (!response.response_snapshot_id) return
  const nextState = {
    snapshot_id: response.response_snapshot_id,
    state_hash: response.state_hash,
    content_hash: response.content_hash,
    content_length: response.content_length,
    schema_version: 1,
  }
  if (JSON.stringify(nextState) === JSON.stringify(lastRecordedResponseState)) return
  const recorded = await recordProcessEvent('response_state_changed', {
    question_type: 'short_answer',
    response_state_before: lastRecordedResponseState,
    response_state_after: nextState,
    change_source: force ? 'forced_snapshot' : 'student',
  })
  if (recorded) lastRecordedResponseState = nextState
}

async function saveDraft(force = false) {
  if (!state.value) return
  const serialized = JSON.stringify(answer.value)
  if (!force && serialized === lastSavedAnswer) return
  if (!online.value) {
    saveStatus.value = '当前离线，等待恢复后保存'
    if (force) throw new Error('当前离线，草稿尚未保存。')
    return
  }
  saveStatus.value = '正在保存'
  const operation = async () => {
    try {
      const questionType = state.value!.question.question_type
      if (questionType !== 'short_answer') {
        await recordChoiceState(questionType, answer.value, force)
      }
      const response = await getStudentAssessmentApi().saveDraft(
        state.value!.actions.save_draft_url,
        answer.value,
        new Date().toISOString(),
      )
      if (questionType === 'short_answer') await recordTextSnapshot(response, force)
      lastSavedAnswer = serialized
      saveStatus.value = '草稿已保存'
      error.value = ''
    } catch (caught) {
      saveStatus.value = '草稿保存失败'
      error.value = `${caught instanceof Error ? caught.message : '草稿保存失败。'} 当前答案仍保留在本页，请不要关闭页面。`
      throw caught
    }
  }
  const pending = saveQueue.then(operation, operation)
  saveQueue = pending.catch(() => undefined)
  return pending
}

async function chooseSingle(value: string) {
  answer.value = value
  await noteInputStarted()
  await saveDraft().catch(() => undefined)
}

async function toggleMultiple(value: string, selected: boolean) {
  const values = new Set(Array.isArray(answer.value) ? answer.value.map(String) : [])
  if (selected) values.add(value)
  else values.delete(value)
  answer.value = [...values].sort()
  await noteInputStarted()
  await saveDraft().catch(() => undefined)
}

function toggleMultipleFromEvent(value: string, event: unknown) {
  const selected = Boolean((event as { target?: { checked?: boolean } }).target?.checked)
  void toggleMultiple(value, selected)
}

function updateText(event: unknown) {
  const value = String((event as { target?: { value?: unknown } }).target?.value ?? '')
  answer.value = value
  queueTextObservation(value, event)
  void noteInputStarted()
  saveStatus.value = '等待自动保存'
  if (textSaveTimer !== undefined) globalThis.clearTimeout(textSaveTimer)
  textSaveTimer = globalThis.setTimeout(() => {
    void saveDraft().catch(() => undefined)
  }, 800)
}

function finishComposition(event: unknown) {
  const value = String((event as { target?: { value?: unknown } }).target?.value ?? '')
  queueTextObservation(value, { inputType: 'insertCompositionText' })
  void flushTextObservation()
}

function openSubmitDialog() {
  submitDialogOpen.value = true
}

async function navigate(url: string, runtimePrepared = false) {
  if (/^\/student\/sessions\/\d+\/questions\/\d+\//.test(url)) {
    routeTransitionPrepared = runtimePrepared
    try {
      await router.push(url)
    } catch (caught) {
      routeTransitionPrepared = false
      throw caught
    }
    return
  }
  globalThis.location.assign(url)
}

async function confirmSubmit() {
  if (!state.value || submitting.value) return
  submitting.value = true
  submitDialogOpen.value = false
  error.value = ''
  try {
    await flushTextObservation()
    await saveDraft(true)
    const submissionToken = globalThis.crypto.randomUUID()
    await recordProcessEvent(
      'response_commit_requested',
      { submission_token: submissionToken },
      { correlationId: submissionToken },
    )
    await processRuntime?.checkpoint('commit')
    await processRuntime?.flushBounded(1500)
    const response = await getStudentAssessmentApi().submit(
      state.value.actions.submit_url,
      submissionToken,
      new Date().toISOString(),
    )
    await processRuntime?.closeAttempt('submit')
    if (response.confidence_required) {
      pendingConfidence.value = confidenceContract(response.confidence)
      if (!pendingConfidence.value) throw new Error('信心评价契约无效，请刷新页面后重试。')
      state.value.assignment.status = 'submitted'
      submitting.value = false
      await showConfidencePrompt()
      return
    }
    await showFeedback(response)
    await navigate(response.next_url, true)
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '提交失败，请重试。'
    submitting.value = false
  }
}

async function showConfidencePrompt() {
  if (!pendingConfidence.value) return
  confidenceStatus.value = '正在确认评价表已显示'
  confidencePromptPromise = getStudentAssessmentApi().post(
    pendingConfidence.value.prompt_shown_url,
    {},
  )
  try {
    await confidencePromptPromise
    confidenceStatus.value = '请选择把握程度，或跳过评价'
  } catch (caught) {
    confidenceStatus.value = '评价表状态同步失败，请重试'
    throw caught
  }
}

async function showFeedback(response: SubmissionResponse) {
  feedback.value = response
  await recordProcessEvent('feedback_shown', {
    feedback_mode: response.feedback_mode || 'none',
    included_correctness: response.is_correct !== undefined,
    included_correct_answer: response.correct_answer !== undefined,
    included_explanation: Boolean(response.explanation),
  })
  if (response.explanation) {
    await recordProcessEvent('explanation_shown', {
      source: 'submission_feedback',
    })
  }
}

async function resolveConfidence(skip: boolean) {
  if (!pendingConfidence.value || confidenceResolving.value) return
  confidenceResolving.value = true
  confidenceStatus.value = '正在保存评价'
  error.value = ''
  try {
    await confidencePromptPromise
    const response = await getStudentAssessmentApi().post<SubmissionResponse>(
      skip ? pendingConfidence.value.skip_url : pendingConfidence.value.commit_url,
      skip ? {} : { probability: confidenceValue.value },
    )
    pendingConfidence.value = undefined
    await showFeedback(response)
    await navigate(response.next_url, true)
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '信心评价保存失败。'
    confidenceStatus.value = '保存失败，可重新提交或跳过'
    confidenceResolving.value = false
  }
}

async function navigateToAssignment(item: StudentRuntimeQuestionEntry['navigation'][number]) {
  if (item.current || !state.value) return
  error.value = ''
  try {
    await flushTextObservation()
    if (!answerLocked.value) await saveDraft(true)
    await processRuntime?.closeAttempt('navigation', item.assignment_id)
    await navigate(item.url, true)
  } catch (caught) {
    error.value = caught instanceof Error
      ? caught.message
      : '草稿保存失败，尚未切换题目。'
  }
}

async function navigateFromDrawer(item: StudentRuntimeQuestionEntry['navigation'][number]) {
  navigationOpen.value = false
  await navigateToAssignment(item)
}

function requestFinishFromDrawer() {
  navigationOpen.value = false
  finishDialogOpen.value = true
}

async function skipCurrent() {
  if (!state.value || answerLocked.value) return
  error.value = ''
  try {
    await flushTextObservation()
    await saveDraft(true)
    const response = await getStudentAssessmentApi().skip(state.value.actions.skip_url)
    await processRuntime?.closeAttempt('skip')
    await navigate(response.next_url, true)
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '跳过操作失败。'
  }
}

function submitFinishForm(url: string) {
  const form = globalThis.document.createElement('form')
  form.method = 'post'
  form.action = url
  form.dataset.runtimeFinishForm = ''
  const csrf = globalThis.document.createElement('input')
  csrf.type = 'hidden'
  csrf.name = 'csrfmiddlewaretoken'
  csrf.value = getBootstrapContext().csrf_token
  form.append(csrf)
  globalThis.document.body.append(form)
  form.submit()
}

async function confirmFinish() {
  if (!state.value || finishing.value || timedOut.value) return
  finishing.value = true
  finishDialogOpen.value = false
  navigationOpen.value = false
  error.value = ''
  try {
    await flushTextObservation()
    if (!answerLocked.value) await saveDraft(true)
    await processRuntime?.closeAttempt('finish')
    submitFinishForm(state.value.actions.finish_url)
  } catch {
    finishing.value = false
    error.value = '草稿保存失败，实验尚未结束。请检查网络后重试。'
  }
}

function handleOffline() {
  online.value = false
  saveStatus.value = '当前离线，等待恢复后保存'
}

function handleOnline() {
  online.value = true
  if (JSON.stringify(answer.value) !== lastSavedAnswer) {
    void saveDraft(true).catch(() => undefined)
  }
}

function disposeQuestionRuntime() {
  if (countdown !== undefined) globalThis.clearInterval(countdown)
  if (textSaveTimer !== undefined) globalThis.clearTimeout(textSaveTimer)
  if (textObservationTimer !== undefined) globalThis.clearTimeout(textObservationTimer)
  countdown = undefined
  textSaveTimer = undefined
  textObservationTimer = undefined
  pendingTextObservation = undefined
  processRuntime?.dispose()
  processRuntime = undefined
}

function resetQuestionState() {
  state.value = undefined
  answer.value = null
  pendingConfidence.value = undefined
  feedback.value = undefined
  secondsRemaining.value = null
  submitDialogOpen.value = false
  finishDialogOpen.value = false
  submitting.value = false
  finishing.value = false
  timedOut.value = false
  inputStarted = false
  lastSavedAnswer = 'null'
  lastRecordedResponseState = null
  lastStableText = ''
  saveQueue = Promise.resolve(undefined)
}

async function loadQuestion(targetRoute = route) {
  const sessionId = Number(targetRoute.params.sessionId)
  const assignmentId = Number(targetRoute.params.assignmentId)
  loading.value = true
  error.value = ''
  try {
    state.value = await getStudentAssessmentApi().question(
      sessionId,
      assignmentId,
      typeof targetRoute.query.reason === 'string'
        ? targetRoute.query.reason
        : 'navigation_click',
    )
    answer.value = cloneJson(state.value.draft_answer)
    lastSavedAnswer = JSON.stringify(answer.value)
    lastRecordedResponseState = cloneJson(state.value.initial_response_state)
    lastStableText = typeof state.value.draft_answer === 'string'
      ? state.value.draft_answer
      : ''
    saveStatus.value = state.value.draft_answer === null ? '尚未保存' : '已恢复草稿'
    secondsRemaining.value = state.value.remaining_seconds
    processRuntime = await createProcessRuntime(
      state.value.process,
      state.value.assignment.status,
      getBootstrapContext().csrf_token,
    )
    pendingConfidence.value = confidenceContract(state.value.pending_confidence)
    if (pendingConfidence.value) await showConfidencePrompt()
    startCountdown()
  } catch (caught) {
    const confidenceQuestionUrl = caught instanceof ApiError
      && caught.code === 'CONFIDENCE_REQUIRED'
      && typeof caught.fields.question_url === 'string'
      ? caught.fields.question_url
      : undefined
    if (
      confidenceQuestionUrl
      && /^\/student\/sessions\/\d+\/questions\/\d+\/$/.test(confidenceQuestionUrl)
    ) {
      await router.replace(confidenceQuestionUrl)
      return
    }
    error.value = caught instanceof Error ? caught.message : '作答页面加载失败。'
  } finally {
    loading.value = false
  }
}

onBeforeRouteUpdate(async (to) => {
  if (
    String(to.params.sessionId) === String(route.params.sessionId)
    && String(to.params.assignmentId) === String(route.params.assignmentId)
  ) return
  const targetAssignmentId = Number(to.params.assignmentId)
  try {
    if (!routeTransitionPrepared && state.value) {
      await flushTextObservation()
      if (!answerLocked.value) await saveDraft(true)
      await processRuntime?.closeAttempt('navigation', targetAssignmentId)
    }
    routeTransitionPrepared = false
    disposeQuestionRuntime()
    resetQuestionState()
    await loadQuestion(to)
  } catch (caught) {
    routeTransitionPrepared = false
    error.value = caught instanceof Error ? caught.message : '切换题目失败，请重试。'
    return false
  }
})

onMounted(async () => {
  if (!checkCapabilities()) {
    loading.value = false
    return
  }
  globalThis.addEventListener('offline', handleOffline)
  globalThis.addEventListener('online', handleOnline)
  await loadQuestion()
})

onBeforeUnmount(() => {
  disposeQuestionRuntime()
  globalThis.removeEventListener('offline', handleOffline)
  globalThis.removeEventListener('online', handleOnline)
})
</script>

<template>
  <main class="kt-assessment-focus">
    <section v-if="missingCapabilities.length" class="kt-assessment-capability" role="alert">
      <h1>当前浏览器无法安全保存作答过程</h1>
      <p>请升级浏览器或更换受支持的浏览器后再进入实验。</p>
      <p>缺少能力：{{ missingCapabilities.join('、') }}</p>
    </section>
    <p v-else-if="loading" role="status">正在加载作答页面…</p>
    <p v-else-if="!state && error" class="kt-alert kt-alert--danger" role="alert">{{ error }}</p>
    <div v-else-if="state" class="kt-assessment-layout">
      <FocusAssessmentHeader
        class="kt-assessment-mobile-header"
        data-mobile-assessment-header
        :current="state.assignment.sequence_index"
        :total="state.progress.total"
      >
        <button
          type="button"
          class="kt-button kt-button--secondary"
          data-mobile-navigation-trigger
          aria-controls="assessment-mobile-navigation"
          :aria-expanded="navigationOpen"
          @click="navigationOpen = true"
        >
          题目导航
        </button>
      </FocusAssessmentHeader>
      <aside class="kt-assessment-nav" aria-labelledby="assessment-navigation-title">
        <div class="kt-assessment-nav__header">
          <h2 id="assessment-navigation-title">题目导航</h2>
          <span>{{ state.progress.submitted }}/{{ state.progress.total }}</span>
        </div>
        <nav aria-label="题目状态">
          <a
            v-for="item in state.navigation"
            :key="item.assignment_id"
            :href="item.url"
            class="kt-assessment-nav__item"
            :aria-current="item.current ? 'step' : undefined"
            :data-nav-assignment="item.assignment_id"
            @click.prevent="navigateToAssignment(item)"
          >
            <span>第 {{ item.sequence_index }} 题</span>
            <small>{{ statusLabel(item.status) }}</small>
          </a>
        </nav>
        <button type="button" class="kt-button kt-button--danger-secondary" data-finish-request :disabled="timedOut || finishing" @click="finishDialogOpen = true">结束实验</button>
      </aside>

      <section class="kt-assessment-question" aria-labelledby="assessment-question-title">
        <header class="kt-assessment-question__header">
          <div>
            <p class="kt-kicker">{{ state.experiment_name }}</p>
            <h1 id="assessment-question-title">{{ sequenceLabel }}</h1>
          </div>
          <span v-if="secondsRemaining !== null" role="timer" aria-live="off">
            剩余 {{ timerLabel(secondsRemaining) }}
          </span>
        </header>
        <p class="kt-assessment-context">
          {{ state.question.stable_code || state.question.code }} · 主 KC：{{ state.question.primary_kc }}
        </p>
        <p class="kt-assessment-stem">{{ state.question.text }}</p>
        <fieldset v-if="['single_choice', 'multiple_choice', 'true_false'].includes(state.question.question_type)">
          <legend class="kt-visually-hidden">请选择答案</legend>
          <label v-for="option in state.question.options" :key="option.key" class="kt-assessment-option">
            <input
              name="answer"
              :type="state.question.question_type === 'multiple_choice' ? 'checkbox' : 'radio'"
              :value="option.key"
              :disabled="answerLocked"
              :checked="state.question.question_type === 'multiple_choice' ? Array.isArray(answer) && answer.map(String).includes(option.key) : answer === option.key"
              @change="state.question.question_type === 'multiple_choice' ? toggleMultipleFromEvent(option.key, $event) : chooseSingle(option.key)"
            >
            <strong>{{ option.key }}</strong>
            <span>{{ option.text }}</span>
          </label>
        </fieldset>
        <label v-else class="kt-assessment-text-answer">
          <span>你的答案</span>
          <textarea v-if="state.question.question_type === 'short_answer'" name="answer" rows="6" :disabled="answerLocked" :value="String(answer ?? '')" @input="updateText" @compositionend="finishComposition" @blur="flushTextObservation" />
          <input v-else name="answer" :disabled="answerLocked" :inputmode="state.question.question_type === 'numeric' ? 'decimal' : undefined" :value="String(answer ?? '')" @input="updateText" @compositionend="finishComposition" @blur="flushTextObservation">
        </label>
        <p v-if="error" class="kt-alert kt-alert--danger" role="alert">{{ error }}</p>
        <p v-if="!online" class="kt-alert kt-alert--warning" role="status" data-connectivity-status>当前离线。答案保留在本页，恢复网络后将自动保存。</p>
        <p v-if="answerLocked" class="kt-alert kt-alert--info">本题已提交并锁定，答案不可再修改。</p>
        <section v-if="pendingConfidence" class="kt-assessment-confidence" data-confidence-panel aria-labelledby="confidence-title">
          <h2 id="confidence-title">你对这次答案有多大把握？</h2>
          <p>该评价不影响得分，也可以跳过。</p>
          <label for="confidence-probability">作答信心：{{ confidenceValue }}%</label>
          <input
            id="confidence-probability"
            v-model.number="confidenceValue"
            data-confidence-input
            type="range"
            :min="pendingConfidence.minimum"
            :max="pendingConfidence.maximum"
            :step="pendingConfidence.step"
            :disabled="confidenceResolving"
          >
          <p role="status">{{ confidenceStatus }}</p>
          <div class="kt-assessment-actions">
            <button type="button" class="kt-button kt-button--secondary" :disabled="confidenceResolving" @click="resolveConfidence(true)">跳过评价</button>
            <button type="button" class="kt-button" data-confidence-submit :disabled="confidenceResolving" @click="resolveConfidence(false)">提交信心评价</button>
          </div>
        </section>
        <div v-if="feedback" class="kt-alert kt-alert--success" data-feedback>
          <strong>本题已提交并锁定</strong>
          <span v-if="feedback.is_correct === true">，回答正确</span>
          <span v-else-if="feedback.is_correct === false">，回答错误</span>
          <div v-if="feedback.correct_answer !== undefined"><strong>正确答案：</strong>{{ formatCorrectAnswer(feedback.correct_answer) }}</div>
          <div v-if="feedback.explanation"><strong>解析：</strong>{{ feedback.explanation }}</div>
        </div>
        <p class="kt-assessment-save-status" role="status" data-save-status>{{ saveStatus }}</p>
        <div v-if="!answerLocked" class="kt-assessment-actions">
          <button type="button" class="kt-button kt-button--secondary" data-skip-request @click="skipCurrent">标记跳过</button>
          <button type="button" class="kt-button" data-submit-request :disabled="submitting" @click="openSubmitDialog">确认提交本题</button>
        </div>
      </section>
      <Drawer
        id="assessment-mobile-navigation"
        :open="navigationOpen"
        title="题目导航"
        @close="navigationOpen = false"
      >
        <p class="kt-assessment-drawer-progress">已提交 {{ state.progress.submitted }}/{{ state.progress.total }}</p>
        <nav aria-label="移动端题目状态">
          <a
            v-for="item in state.navigation"
            :key="item.assignment_id"
            :href="item.url"
            class="kt-assessment-nav__item"
            :aria-current="item.current ? 'step' : undefined"
            @click.prevent="navigateFromDrawer(item)"
          >
            <span>第 {{ item.sequence_index }} 题</span>
            <small>{{ statusLabel(item.status) }}</small>
          </a>
        </nav>
        <button
          type="button"
          class="kt-button kt-button--danger-secondary"
          :disabled="timedOut || finishing"
          @click="requestFinishFromDrawer"
        >
          结束实验
        </button>
      </Drawer>
    </div>
    <div v-if="submitDialogOpen" class="kt-dialog-backdrop">
      <section class="kt-dialog" role="dialog" aria-modal="true" aria-labelledby="submit-dialog-title" aria-describedby="submit-dialog-description">
        <h2 id="submit-dialog-title">确认提交本题</h2>
        <p id="submit-dialog-description">提交后答案将立即锁定，不能修改。草稿会在提交前再次保存。</p>
        <div class="kt-dialog__actions">
          <button type="button" class="kt-button kt-button--secondary" @click="submitDialogOpen = false">取消</button>
          <button type="button" class="kt-button" data-submit-confirm @click="confirmSubmit">提交并锁定</button>
        </div>
      </section>
    </div>
    <div v-if="finishDialogOpen" class="kt-dialog-backdrop">
      <section class="kt-dialog" role="dialog" aria-modal="true" aria-labelledby="finish-dialog-title" aria-describedby="finish-dialog-description">
        <h2 id="finish-dialog-title">确认结束实验</h2>
        <p id="finish-dialog-description">未正式提交的题目将记录为未作答。此操作会结束当前会话。</p>
        <div class="kt-dialog__actions">
          <button type="button" class="kt-button kt-button--secondary" @click="finishDialogOpen = false">取消</button>
          <button type="button" class="kt-button kt-button--danger" data-finish-confirm @click="confirmFinish">结束实验</button>
        </div>
      </section>
    </div>
  </main>
</template>
