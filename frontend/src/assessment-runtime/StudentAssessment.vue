<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
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
import { ResponseTrajectory } from './response-trajectory'
import StudentResearchTools from './StudentResearchTools.vue'
import { prepareOfflineQuestion } from './offline-cache'
import { createWriterLease, isWriterLeaseConflict, type WriterLease } from './writer-lease'
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
const hasLocalResponse = computed(() => Array.isArray(answer.value) ? answer.value.length > 0 : answer.value !== null && answer.value !== undefined && answer.value !== '')
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
const responseConflict = ref<Record<string, unknown>>()
const captureReady = ref(false)
let countdown: ReturnType<typeof globalThis.setInterval> | undefined
let recoveryTimer: ReturnType<typeof globalThis.setInterval> | undefined
let leaseRetryNeeded = false
let reconnecting = false
let textSaveTimer: ReturnType<typeof globalThis.setTimeout> | undefined
let textObservationTimer: ReturnType<typeof globalThis.setTimeout> | undefined
let lastSavedAnswer = 'null'
let lastQueuedAnswer = 'null'
let saveQueue = Promise.resolve<unknown>(undefined)
let inputStarted = false
let processRuntime: StudentProcessRuntime | undefined
let confidencePromptPromise: Promise<unknown> | undefined
let lastRecordedResponseState: unknown = null
let lastStableText = ''
let pendingTextObservation: TextObservation | undefined
let routeTransitionPrepared = false
let trajectory: ResponseTrajectory | undefined
let writerLease: WriterLease | undefined
let responseVersion = 0
let composing = false
let compositionInitial = ''
let inputOperationId: string | undefined

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
const responseDisabled = computed(() => answerLocked.value || submitting.value || loading.value || !!responseConflict.value
  || (state.value?.process.capture_version === 'capture-v2' && !captureReady.value))

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
  const currentState = state.value
  const fixedAnswer = cloneJson(answer.value)
  const fixedRuntime = processRuntime
  const fixedTrajectory = trajectory
  const fixedLease = writerLease
  const targetEventId = fixedTrajectory?.head
  const clientTime = new Date().toISOString()
  const serialized = JSON.stringify(fixedAnswer)
  if (!force && serialized === lastQueuedAnswer) return
  lastQueuedAnswer = serialized
  if (fixedTrajectory) await fixedTrajectory.settled()
  if (!online.value) {
    saveStatus.value = fixedTrajectory ? '已保存到本机，等待联网同步' : '当前离线，等待恢复后保存'
    if (force) throw new Error('当前离线，草稿尚未保存。')
    return
  }
  saveStatus.value = '正在保存'
  const operation = async () => {
    try {
      const questionType = currentState.question.question_type
      if (fixedTrajectory) {
        if (!targetEventId) return
        await fixedRuntime?.flushBounded(1500)
      } else if (questionType !== 'short_answer') {
        await recordChoiceState(questionType, fixedAnswer, force)
      }
      const response = await getStudentAssessmentApi().saveDraft(
        currentState.actions.save_draft_url,
        fixedAnswer,
        clientTime,
        ...(fixedTrajectory ? [{ target_event_id: targetEventId || null, expected_version: responseVersion, ...await fixedLease?.beforePointerWrite() }] : []),
      )
      if (!fixedTrajectory && questionType === 'short_answer') await recordTextSnapshot(response, force)
      if (response.response_version !== undefined) responseVersion = response.response_version
      lastSavedAnswer = serialized
      saveStatus.value = '草稿已保存'
      leaseRetryNeeded = false
      error.value = ''
    } catch (caught) {
      if (isWriterLeaseConflict(caught)) { fixedLease?.invalidate(); leaseRetryNeeded = true }
      if (caught instanceof ApiError && caught.code === 'RESPONSE_VERSION_CONFLICT') {
        responseConflict.value = { ...caught.fields, candidate_event_id: fixedTrajectory?.head || caught.fields.candidate_event_id,
          candidate_answer: cloneJson(answer.value) }
      }
      saveStatus.value = '草稿保存失败'
      error.value = `${caught instanceof Error ? caught.message : '草稿保存失败。'} 当前答案仍保留在本页，请不要关闭页面。`
      throw caught
    }
  }
  const pending = saveQueue.then(operation, operation)
  saveQueue = pending.catch(() => undefined)
  return pending
}

async function chooseSingle(value: string | null) {
  answer.value = value
  void noteInputStarted()
  captureChange(value, value === null ? 'clear' : 'select', { option_key: value, option_action: value === null ? 'clear' : 'select' })
  await saveDraft().catch(() => undefined)
}

async function toggleMultiple(value: string, selected: boolean) {
  const values = new Set(Array.isArray(answer.value) ? answer.value.map(String) : [])
  if (selected) values.add(value)
  else values.delete(value)
  answer.value = [...values].sort()
  void noteInputStarted()
  captureChange(answer.value, selected ? 'select' : 'deselect', { option_key: value, option_action: selected ? 'select' : 'deselect' })
  await saveDraft().catch(() => undefined)
}

function toggleMultipleFromEvent(value: string, event: unknown) {
  const selected = Boolean((event as { target?: { checked?: boolean } }).target?.checked)
  void toggleMultiple(value, selected)
}

function updateText(event: unknown) {
  const value = String((event as { target?: { value?: unknown } }).target?.value ?? '')
  answer.value = value
  if (trajectory) {
    const details = event as InputEvent
    if (composing || details.isComposing) return
    void noteInputStarted()
    captureChange(value, details.inputType || 'input')
    scheduleTextSave()
    return
  }
  queueTextObservation(value, event)
  void noteInputStarted()
  saveStatus.value = '等待自动保存'
  if (textSaveTimer !== undefined) globalThis.clearTimeout(textSaveTimer)
  textSaveTimer = globalThis.setTimeout(() => {
    void saveDraft().catch(() => undefined)
  }, 800)
}

function captureChange(value: unknown, inputType: string, extra = {}) {
  if (!trajectory) return
  trajectory.change(value, inputType, inputOperationId || crypto.randomUUID(), extra)
  inputOperationId = undefined
  void trajectory.settled().then(() => {
    saveStatus.value = '已保存到本机，等待同步'
  }).catch((caught) => {
    captureReady.value = false
    saveStatus.value = '本地保存失败'
    error.value = `无法持久化作答轨迹：${caught instanceof Error ? caught.message : '存储不可用'}。请勿关闭本页。`
  })
}

function scheduleTextSave() {
  if (textSaveTimer !== undefined) globalThis.clearTimeout(textSaveTimer)
  textSaveTimer = globalThis.setTimeout(() => { void saveDraft().catch(() => undefined) }, 800)
}

async function chooseResponseBranch(side: 'current' | 'candidate') {
  if (!state.value?.actions.resolve_branch_url || !responseConflict.value || !processRuntime) return
  await trajectory?.settled()
  await processRuntime.flushBounded(1500)
  const conflict = responseConflict.value
  const result = await getStudentAssessmentApi().post<{ response_version: number; active_response_event_id: string; answer: unknown }>(
    state.value.actions.resolve_branch_url, {
      candidate_event_ids: [conflict.current_event_id, conflict.candidate_event_id],
      selected_event_id: conflict[`${side}_event_id`], expected_version: conflict.expected_version,
      operation_id: crypto.randomUUID(),
      ...await writerLease?.beforePointerWrite(),
    },
  ).catch((caught) => { error.value = caught instanceof Error ? caught.message : '版本选择失败。'; return undefined })
  if (!result) return
  await processRuntime.clearResponseRecovery?.()
  answer.value = cloneJson(result.answer)
  responseVersion = result.response_version
  lastSavedAnswer = JSON.stringify(answer.value)
  const runtime = processRuntime
  trajectory = new ResponseTrajectory(state.value.question.question_type, answer.value, result.active_response_event_id,
    (type, payload, options) => runtime.record(type, payload, options))
  responseConflict.value = undefined
  error.value = ''
  saveStatus.value = '已保存所选版本'
}

function beforeTextInput(event: Event) {
  if (!trajectory) return
  const input = event as InputEvent
  const target = input.target as HTMLInputElement | HTMLTextAreaElement
  inputOperationId = inputOperationId || crypto.randomUUID()
  trajectory.operation(input.inputType || 'input', composing ? 'updating' : 'none', target.selectionStart, target.selectionEnd, inputOperationId)
  void noteInputStarted()
}

function startComposition(event: Event) {
  composing = true
  compositionInitial = String(answer.value ?? '')
  inputOperationId = crypto.randomUUID()
  const target = event.target as HTMLInputElement | HTMLTextAreaElement
  trajectory?.operation('insertCompositionText', 'started', target.selectionStart, target.selectionEnd, inputOperationId)
  void noteInputStarted()
}

function finishComposition(event: unknown) {
  const value = String((event as { target?: { value?: unknown } }).target?.value ?? '')
  composing = false
  if (trajectory) {
    const operationId = inputOperationId || crypto.randomUUID()
    trajectory.operation('insertCompositionText', value === compositionInitial ? 'cancelled' : 'confirmed', null, null, operationId)
    answer.value = value
    captureChange(value, 'insertCompositionText')
    scheduleTextSave()
    return
  }
  queueTextObservation(value, { inputType: 'insertCompositionText' })
  void flushTextObservation()
}

function openSubmitDialog() {
  submitDialogOpen.value = true
}

async function navigate(url: string, runtimePrepared = false) {
  await processRuntime?.flushBounded(1500)
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
    if (composing) throw new Error('请先完成当前输入法文字，再确认提交。')
    const submittedState = trajectory?.head
    const clickedAt = new Date().toISOString()
    const clickedMonotonic = performance.now()
    const submissionToken = globalThis.crypto.randomUUID()
    const commitRecorded = recordProcessEvent(
      'response_commit_requested',
      { submission_token: submissionToken },
      { correlationId: submissionToken, wallTime: clickedAt, monotonicMs: clickedMonotonic },
    )
    const fixedSave = trajectory ? saveDraft(true) : undefined
    await commitRecorded
    await flushTextObservation()
    if (fixedSave) await fixedSave
    else await saveDraft(true)
    await processRuntime?.checkpoint('commit')
    await processRuntime?.flushBounded(1500)
    const api = getStudentAssessmentApi()
    const response = trajectory
      ? await api.submit(state.value.actions.submit_url, submissionToken, clickedAt, submittedState, await writerLease?.beforePointerWrite())
      : await api.submit(state.value.actions.submit_url, submissionToken, clickedAt)
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
  await nextTick()
  if (trajectory) await processRuntime?.record('confidence_observed', {
    capture_version: 'capture-v2', operation_id: crypto.randomUUID(),
    confidence_assignment_id: pendingConfidence.value.confidence_assignment_id,
    stage: 'presented', answer_event_id: trajectory.head, probability: null, feedback_phase: 'before_feedback',
  })
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
  await nextTick()
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
    if (trajectory) await processRuntime?.record('confidence_observed', {
      capture_version: 'capture-v2', operation_id: crypto.randomUUID(),
      confidence_assignment_id: pendingConfidence.value.confidence_assignment_id,
      stage: skip ? 'skipped' : 'responded', answer_event_id: trajectory.head,
      probability: skip ? null : confidenceValue.value, feedback_phase: 'before_feedback',
    })
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

async function handleOnline() {
  if (reconnecting) return
  reconnecting = true
  online.value = true
  try {
    if (trajectory && state.value) {
      await trajectory.settled()
      await processRuntime?.flushBounded(1500)
      const current = await getStudentAssessmentApi().question(state.value.session_id, state.value.assignment.assignment_id, 'resume')
      if (current.offline_recovery) { online.value = false; return }
      responseVersion = current.process.response_version || 0
      state.value.assignment.status = current.assignment.status
      if (answerLocked.value) return
    }
    if (JSON.stringify(answer.value) !== lastSavedAnswer) await saveDraft(true)
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '重新同步失败，请重试。'
  } finally { reconnecting = false }
}

function disposeQuestionRuntime() {
  if (countdown !== undefined) globalThis.clearInterval(countdown)
  if (textSaveTimer !== undefined) globalThis.clearTimeout(textSaveTimer)
  if (textObservationTimer !== undefined) globalThis.clearTimeout(textObservationTimer)
  if (recoveryTimer !== undefined) globalThis.clearInterval(recoveryTimer)
  recoveryTimer = undefined
  countdown = undefined
  textSaveTimer = undefined
  textObservationTimer = undefined
  pendingTextObservation = undefined
  processRuntime?.dispose()
  processRuntime = undefined
}

function resetQuestionState() {
  captureReady.value = false
  responseConflict.value = undefined
  trajectory = undefined
  writerLease = undefined
  leaseRetryNeeded = false
  responseVersion = 0
  composing = false
  inputOperationId = undefined
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
  lastQueuedAnswer = 'null'
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
    if (state.value.offline_recovery) online.value = false
    lastSavedAnswer = JSON.stringify(answer.value)
    lastQueuedAnswer = lastSavedAnswer
    lastRecordedResponseState = cloneJson(state.value.initial_response_state)
    lastStableText = typeof state.value.draft_answer === 'string'
      ? state.value.draft_answer
      : ''
    saveStatus.value = state.value.draft_answer === null ? '尚未保存' : '已恢复草稿'
    secondsRemaining.value = state.value.remaining_seconds
    if (state.value.presented_deadline_at) secondsRemaining.value = Math.max(0, Math.floor((Date.parse(state.value.presented_deadline_at) - Date.now()) / 1000))
    processRuntime = await createProcessRuntime(
      state.value.process,
      state.value.assignment.status,
      getBootstrapContext().csrf_token,
    )
    if (state.value.process.capture_version === 'capture-v2') {
      const process = state.value.process
      if (!process.writer_lease_acquire_url || !process.writer_lease_renew_url || !processRuntime.clientInstanceId) throw new Error('缺少写入租约契约，无法保存正式答案。')
      writerLease = createWriterLease({ client: getStudentAssessmentApi(), clientInstanceId: processRuntime.clientInstanceId,
        acquireUrl: process.writer_lease_acquire_url, renewUrl: process.writer_lease_renew_url })
      responseVersion = state.value.process.response_version || 0
      let head = state.value.process.active_response_event_id || null
      const restored = !answerLocked.value && processRuntime.restoredResponse
      if (restored && restored.event_id !== head) {
        answer.value = cloneJson(restored.payload.after_state.value)
        head = restored.event_id
        saveStatus.value = '已恢复本机答案，等待同步'
      }
      const kind = state.value.question.question_type
      const initial = answer.value ?? (kind === 'multiple_choice' ? [] : ['short_answer', 'fill_blank', 'numeric'].includes(kind) ? '' : null)
      const runtime = processRuntime
      trajectory = new ResponseTrajectory(kind, initial, head, (type, payload, options) => runtime.record(type, payload, options))
      await prepareOfflineQuestion(state.value)
      captureReady.value = true
      recoveryTimer = globalThis.setInterval(() => {
        if (!online.value) {
          void fetch('/health/', { cache: 'no-store' }).then((response) => {
            if (response.ok) void handleOnline()
          }).catch(() => undefined)
        } else if (leaseRetryNeeded && !responseConflict.value && !answerLocked.value) {
          void saveDraft(true).catch(() => undefined)
        }
      }, 5000)
    }
    loading.value = false
    await nextTick()
    await processRuntime.record('item_rendered', { presentation_state: 'interactive' })
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
      <header class="kt-assessment-topbar">
        <div><span class="kt-assessment-topbar__label">作业</span><strong>{{ state.experiment_name }}</strong></div>
        <div class="kt-assessment-topbar__status">
          <span v-if="secondsRemaining !== null" role="timer" aria-live="off">剩余 {{ timerLabel(secondsRemaining) }}</span>
          <p class="kt-assessment-save-status" role="status" data-save-status>{{ saveStatus }}</p>
        </div>
      </header>
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

      <section class="kt-assessment-question" aria-labelledby="assessment-question-title">
        <header class="kt-assessment-question__header">
          <div>
            <p class="kt-kicker">{{ state.experiment_name }}</p>
            <h1 id="assessment-question-title">{{ sequenceLabel }}</h1>
          </div>
        </header>
        <p class="kt-assessment-context">
          {{ state.question.stable_code || state.question.code }}<span v-if="state.question.primary_kc"> · 主 KC：{{ state.question.primary_kc }}</span>
        </p>
        <p class="kt-assessment-stem">{{ state.question.text }}</p>
        <fieldset v-if="['single_choice', 'multiple_choice', 'true_false'].includes(state.question.question_type)">
          <legend class="kt-visually-hidden">请选择答案</legend>
          <label v-for="option in state.question.options" :key="option.key" class="kt-assessment-option">
            <input
              name="answer"
              :type="state.question.question_type === 'multiple_choice' ? 'checkbox' : 'radio'"
              :value="option.key"
              :disabled="responseDisabled"
              :checked="state.question.question_type === 'multiple_choice' ? Array.isArray(answer) && answer.map(String).includes(option.key) : answer === option.key"
              @change="state.question.question_type === 'multiple_choice' ? toggleMultipleFromEvent(option.key, $event) : chooseSingle(option.key)"
            >
            <strong>{{ option.key }}</strong>
            <span>{{ option.text }}</span>
          </label>
          <button v-if="trajectory && state.question.question_type !== 'multiple_choice'" type="button" class="kt-button kt-button--secondary" :disabled="responseDisabled || answer === null" @click="chooseSingle(null)">清除选择</button>
        </fieldset>
        <label v-else class="kt-assessment-text-answer">
          <span>你的答案</span>
          <textarea v-if="state.question.question_type === 'short_answer'" name="answer" rows="6" :disabled="responseDisabled" :value="String(answer ?? '')" @beforeinput="beforeTextInput" @input="updateText" @compositionstart="startComposition" @compositionend="finishComposition" @blur="flushTextObservation" />
          <input v-else name="answer" :disabled="responseDisabled" :inputmode="state.question.question_type === 'numeric' ? 'decimal' : undefined" :value="String(answer ?? '')" @beforeinput="beforeTextInput" @input="updateText" @compositionstart="startComposition" @compositionend="finishComposition" @blur="flushTextObservation">
        </label>
        <StudentResearchTools v-if="trajectory && processRuntime && state.actions.research_tools_url && !answerLocked && captureReady && online" :endpoint="state.actions.research_tools_url" :runtime="processRuntime" :answer-event-id="trajectory.head" :wait-for-capture="() => trajectory!.settled()" @interaction="noteInputStarted" />
        <p v-if="error" class="kt-alert kt-alert--danger" role="alert">{{ error }}</p>
        <button v-if="trajectory && online && captureReady && !answerLocked && saveStatus.includes('失败')" class="kt-button kt-button--secondary" type="button" @click="saveDraft(true).catch(() => undefined)">重试同步</button>
        <section v-if="responseConflict" class="kt-alert kt-alert--warning" aria-label="选择答案版本">
          <p>另一设备保存了不同答案。请选择接下来使用的版本。</p>
          <p>已保存版本：{{ formatCorrectAnswer(responseConflict.current_answer) }}</p>
          <button class="kt-button kt-button--secondary" type="button" @click="chooseResponseBranch('current')">继续使用已保存版本</button>
          <p>本机版本：{{ formatCorrectAnswer(responseConflict.candidate_answer) }}</p>
          <button class="kt-button" type="button" @click="chooseResponseBranch('candidate')">继续使用本机版本</button>
        </section>
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
        <div v-if="!answerLocked" class="kt-assessment-actions">
          <button type="button" class="kt-button kt-button--secondary" data-skip-request @click="skipCurrent">标记跳过</button>
          <button type="button" class="kt-button" data-submit-request :disabled="submitting" @click="openSubmitDialog">确认提交本题</button>
        </div>
      </section>
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
            :data-answer-status="item.status"
            :data-has-response="item.current ? hasLocalResponse : ['draft', 'submitted'].includes(item.status)"
            :aria-current="item.current ? 'step' : undefined"
            :data-nav-assignment="item.assignment_id"
            @click.prevent="navigateToAssignment(item)"
          >
            <span>第 {{ item.sequence_index }} 题</span>
            <small>{{ item.current && hasLocalResponse && !answerLocked ? '待提交' : statusLabel(item.status) }}</small>
          </a>
        </nav>
        <button type="button" class="kt-button kt-button--danger-secondary" data-finish-request :disabled="timedOut || finishing" @click="finishDialogOpen = true">结束实验</button>
      </aside>

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
            :data-answer-status="item.status"
            :data-has-response="item.current ? hasLocalResponse : ['draft', 'submitted'].includes(item.status)"
            :aria-current="item.current ? 'step' : undefined"
            @click.prevent="navigateFromDrawer(item)"
          >
            <span>第 {{ item.sequence_index }} 题</span>
            <small>{{ item.current && hasLocalResponse && !answerLocked ? '待提交' : statusLabel(item.status) }}</small>
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
