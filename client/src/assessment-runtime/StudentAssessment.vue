<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'

import { getBootstrapContext, isOfflineBootstrapRecovery } from '@/app/bootstrap'
import InitialPasswordReminder from '@/app/InitialPasswordReminder.vue'
import { ApiError } from '@/api/client'
import Drawer from '@/design-system/components/Drawer.vue'
import FocusAssessmentHeader from '@/design-system/components/FocusAssessmentHeader.vue'
import type {
  DraftMutationResponse,
  StudentRuntimeQuestionEntry,
  NavigationClickContext,
  NextEntryRequest,
} from './api'
import { getStudentAssessmentApi, nextEntryRequest } from './api'
import { createProcessRuntime, getSessionController, disposeSessionController, type StudentProcessRuntime, type NavigationIntent } from './session-controller'
import { markPreparedStartupPhase, hydratePreparedQuestion, submissionPollDelay, type PreparedManifest, type ExpectedAttemptVersion, type PaperSubmissionIntent, type PaperSubmissionResponse } from './prepared-paper'
import { ResponseTrajectory } from './response-trajectory'
import { createStableTextInput, type TextCandidate, type TextFlushReason } from './stable-text-input'
import { createOperationBudget } from './operation-budget'
import { matchesMutationRejection, mutationMaterialFingerprint } from './mutation-material'
import { createDraftSaveQueue } from './draft-save-queue'
import StudentResearchTools from './StudentResearchTools.vue'
import { prepareOfflineQuestion } from './offline-cache'
import { clearAdmissionCredentials } from './admission'
import { isWriterLeaseConflict, type WriterLease } from './writer-lease'
import type { CaptureBundle } from './capture-contract'
import { beginAssessmentOperation, currentAssessmentOperationId, finishAssessmentOperation, timedAssessmentStage } from './operation-timing'
import {
  createTextObservation,
  materializeTextObservation,
  mergeTextObservation,
  type TextObservation,
} from './text-observation'
import './student-assessment.css'

const route = useRoute()
const assessmentUser = getBootstrapContext().user
const initialPasswordReminderPending = getBootstrapContext().user?.initial_password_reminder_pending === true
const router = useRouter()
const state = ref<StudentRuntimeQuestionEntry>()
const answer = ref<unknown>(null)
const paperFlow = computed(() => state.value?.assessment_flow_version === 1)
const inlineProbability = ref<number | null>(null)
const inlineConfidenceInput = ref<string>()
const inlineConfidenceError = ref('')
const paperSubmission = ref<PaperSubmissionIntent>()
const paperConfirmOpen = ref(false)
let paperConfirmTrigger: HTMLElement | null = null
const paperStatus = ref('')
let paperManifest: Promise<PreparedManifest> | undefined
let paperPrefetch: { generation: number; start: () => void } | undefined
let operablePaperGeneration = -1
let paperPollTimer: ReturnType<typeof setTimeout> | undefined
let paperPollIndex = 0
let paperReceiptVersions: Record<number, ExpectedAttemptVersion> = {}
let paperResponsePresence: Record<number, boolean> = {}
let inlineConfidenceWrites: Promise<unknown> = Promise.resolve()
let saveMaximumTimer: ReturnType<typeof setTimeout> | undefined
const inlineContract = computed(() => paperFlow.value ? confidenceContract(state.value?.pending_confidence) : undefined)
const previousQuestion = computed(() => state.value?.navigation.find(row => row.sequence_index === (state.value?.assignment.sequence_index || 0) - 1))
const nextQuestion = computed(() => state.value?.navigation.find(row => row.sequence_index === (state.value?.assignment.sequence_index || 0) + 1))
const answeredQuestionCount = computed(() => state.value?.navigation.length ? state.value.navigation.filter(row => row.current ? hasLocalResponse.value : ['draft', 'submitted'].includes(row.status)).length : hasLocalResponse.value ? 1 : 0)
const missingAnswerCount = computed(() => state.value?.navigation.filter(row => row.current ? !hasLocalResponse.value : !['draft', 'submitted'].includes(row.status)).length || 0)
const hasLocalResponse = computed(() => Array.isArray(answer.value) ? answer.value.length > 0 : answer.value !== null && answer.value !== undefined && answer.value !== '')
const loading = ref(true)
const error = ref('')
const saveStatus = ref('尚未保存')
const missingCapabilities = ref<string[]>([])
const secondsRemaining = ref<number | null>(null)
const navigating = ref(false)
const toolsPending = ref(false)
const navigationUncertain = ref(false)
const pendingNavigation = ref<NavigationIntent>()
let navigationSaving = false
let pointerWritePromise: Promise<void> | undefined
const taskClosed = ref(false)
const finishing = ref(false)
const timedOut = ref(false)
const online = ref(globalThis.navigator.onLine)
const navigationOpen = ref(false)
const responseConflict = ref<Record<string, unknown>>()
const captureReady = ref(false)
const initializationState = ref<'loading' | 'preparing' | 'failed' | 'ready'>('loading')
const initializationError = ref<{ message: string; technicalDetails: string }>()
let countdown: ReturnType<typeof globalThis.setInterval> | undefined
let recoveryTimer: ReturnType<typeof globalThis.setInterval> | undefined
let leaseRetryNeeded = false
let reconnecting = false
let textSaveTimer: ReturnType<typeof globalThis.setTimeout> | undefined
let textObservationTimer: ReturnType<typeof globalThis.setTimeout> | undefined
let lastSavedAnswer = 'null'
let lastSavedHead: string | null | undefined
let serverDraftConfirmed = false
let lastQueuedAnswer = 'null'
let lastQueuedHead: string | null | undefined
let inputStarted = false
let processRuntime: StudentProcessRuntime | undefined
const draftSaveQueues = new WeakMap<StudentProcessRuntime, ReturnType<typeof createDraftSaveQueue>>()
let lastRecordedResponseState: unknown = null
let lastStableText = ''
let pendingTextObservation: TextObservation | undefined
let routeTransitionPrepared = false
let trajectory: ResponseTrajectory | undefined
let stableTextInput: ReturnType<typeof createStableTextInput> | undefined
let writerLease: WriterLease | undefined
let composing = false
let compositionInitial = ''
let inputOperationId: string | undefined
let initializationGeneration = 0
let preparationPromise: Promise<void> | undefined
let itemRenderedRecorded = false
let pendingRouteInitialization: { generation: number; sessionId: number; assignmentId: number } | undefined
let pendingNavigationClick: (NavigationClickContext & { assignmentId: number }) | undefined
let closePromise: { runtime: StudentProcessRuntime; pending: Promise<void> } | undefined

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
    || state.value.response_write_allowed === false
    || ['submitted', 'timed_out', 'unanswered'].includes(state.value.assignment.status)
  )
))
const transitionBusy = computed(() => navigating.value || finishing.value)
const actionBusy = computed(() => transitionBusy.value || toolsPending.value)
const responseDisabled = computed(() => answerLocked.value || taskClosed.value || transitionBusy.value || navigationUncertain.value || paperConfirmOpen.value || !!paperSubmission.value || loading.value || !!responseConflict.value
  || !captureReady.value)

function interactionAllowed() {
  return !!state.value && captureReady.value && initializationState.value === 'ready'
}

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

function questionTypeLabel(questionType: string) {
  return {
    single_choice: '单选题',
    multiple_choice: '多选题',
    numeric: '数值题',
    short_answer: '简答题',
    fill_blank: '填空题',
    true_false: '判断题',
  }[questionType] || questionType
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
  if (paperFlow.value && state.value?.response_write_allowed === false) return
  countdown = globalThis.setInterval(() => {
    if (secondsRemaining.value === null) return
    secondsRemaining.value = Math.max(0, secondsRemaining.value - 1)
    if (secondsRemaining.value === 0 && !timedOut.value) {
      timedOut.value = true
      error.value = '本题或实验时间已结束，页面将刷新并同步服务器状态。'
      if (countdown !== undefined) globalThis.clearInterval(countdown)
      void flushAnswerBoundary('deadline').then(flushTextObservation).then(() => closeAttemptAndRelease('timeout')).catch(handleLocalInputError).finally(() => {
        globalThis.setTimeout(() => globalThis.location.reload(), 1_200)
      })
    }
  }, 1000)
}

async function noteInputStarted() {
  if (!interactionAllowed() || inputStarted || !state.value) return
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
  if (!interactionAllowed() || !observation || observation.before === observation.after || !state.value) return
  lastStableText = observation.after
  const payload = await materializeTextObservation(
    state.value.question.question_type,
    observation,
  )
  if (payload) await recordProcessEvent('text_edit_observed', payload)
}

function queueTextObservation(value: string, event: unknown) {
  if (!interactionAllowed()) return
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
  if (!interactionAllowed() || !state.value || navigationSaving || navigationUncertain.value) return
  await flushAnswerBoundary(force ? 'submit' : 'idle')
  if (saveMaximumTimer !== undefined) { clearTimeout(saveMaximumTimer); saveMaximumTimer = undefined }
  if (textSaveTimer !== undefined) {
    globalThis.clearTimeout(textSaveTimer)
    textSaveTimer = undefined
  }
  if (paperFlow.value) await inlineConfidenceWrites
  const currentState = state.value
  const fixedAnswer = cloneJson(answer.value)
  const fixedRuntime = processRuntime
  const fixedTrajectory = trajectory
  const fixedLease = writerLease
  const targetEventId = fixedTrajectory?.head
  const clientTime = new Date().toISOString()
  const operationId = currentAssessmentOperationId() ?? targetEventId ?? crypto.randomUUID()
  const inlineConfidence = paperFlow.value && inlineContract.value ? { probability: inlineProbability.value, answer_event_id: targetEventId || null } : undefined
  let serialized = JSON.stringify(paperFlow.value ? { answer: fixedAnswer, inline_confidence: inlineConfidence } : fixedAnswer)
  if (!force && serialized === lastQueuedAnswer && targetEventId === lastQueuedHead) return
  lastQueuedAnswer = serialized
  lastQueuedHead = targetEventId
  if (fixedTrajectory) await fixedTrajectory.settled()
  if (!online.value) {
    saveStatus.value = fixedTrajectory ? '已保存到本机，等待联网同步' : '当前离线，等待恢复后保存'
    if (force) throw new Error('当前离线，草稿尚未保存。')
    return
  }
  saveStatus.value = '正在保存'
  const operation = async () => {
    if (inlineConfidence) await inlineConfidenceWrites
    if (navigationSaving || navigationUncertain.value || state.value !== currentState || processRuntime !== fixedRuntime) return
    if (inlineConfidence && trajectory?.head === targetEventId) {
      inlineConfidence.probability = inlineProbability.value
      serialized = JSON.stringify({ answer: fixedAnswer, inline_confidence: inlineConfidence })
      lastQueuedAnswer = serialized
    }
    let pointerDone: (() => void) | undefined
    try {
      if (serverDraftConfirmed && serialized === lastSavedAnswer && targetEventId === lastSavedHead) {
        if (JSON.stringify(paperFlow.value ? { answer: answer.value, inline_confidence: inlineConfidence } : answer.value) === serialized && trajectory?.head === targetEventId) saveStatus.value = '草稿已保存'
        return
      }
      saveStatus.value = '正在保存'
      const questionType = currentState.question.question_type
      if (fixedTrajectory) {
        if (!targetEventId) return
        if (!fixedRuntime?.captureMutation && await fixedRuntime?.flushConfirmed() === false) throw new Error('作答轨迹尚未确认保存，请恢复网络后重试。')
      } else if (questionType !== 'short_answer') {
        await recordChoiceState(questionType, fixedAnswer, force)
      }
      if (navigationSaving || navigationUncertain.value) return
      pointerWritePromise = new Promise<void>((resolve) => { pointerDone = resolve })
      const prepareLease = () => timedAssessmentStage('lease', async () => fixedTrajectory ? { ...await fixedLease?.beforePointerWrite() } : undefined, operationId ?? null)
      const credentials = fixedRuntime?.captureMutation ? undefined : await prepareLease()
      const body = { answer: fixedAnswer, client_occurred_at: clientTime, ...(inlineConfidence ? { inline_confidence: inlineConfidence } : {}),
        ...(fixedTrajectory ? { target_event_id: targetEventId || null, expected_version: fixedRuntime?.responseVersion ?? 0, ...credentials } : {}) }
      const send = (frozen: Record<string, unknown>) => timedAssessmentStage('draft_remote', () => getStudentAssessmentApi(operationId ?? null).saveDraft(
        currentState.actions.save_draft_url,
        frozen.answer,
        frozen.client_occurred_at as string,
        ...(fixedTrajectory || inlineConfidence ? [{ ...(fixedTrajectory ? { target_event_id: frozen.target_event_id as string | null, expected_version: frozen.expected_version as number } : { target_event_id: null, expected_version: 0 }),
          ...(frozen.inline_confidence ? { inline_confidence: frozen.inline_confidence as { probability: number | null; answer_event_id: string | null } } : {}),
          ...(frozen.writer_lease_token ? { writer_lease_token: frozen.writer_lease_token as string, writer_client_instance_id: frozen.writer_client_instance_id as string } : credentials),
          ...(frozen.capture_bundle ? { capture_bundle: frozen.capture_bundle as CaptureBundle, capture_operation_id: frozen.capture_operation_id as string } : {}) }] : []),
      ), operationId ?? null)
      const response = fixedTrajectory && fixedRuntime?.captureMutation
        ? await fixedRuntime.captureMutation(currentState.actions.save_draft_url, body, send, operationId ?? null, prepareLease)
        : await send(body)
      if (!fixedTrajectory && questionType === 'short_answer') await recordTextSnapshot(response, force)
      if (response.response_version !== undefined && fixedRuntime) fixedRuntime.responseVersion = response.response_version
      if (state.value !== currentState || processRuntime !== fixedRuntime) return
      if (paperFlow.value) await rememberPaperReceipt(currentState.assignment.attempt_id, response.response_version ?? fixedRuntime?.responseVersion ?? 0, response.active_response_event_id ?? targetEventId ?? null, hasMeaningfulResponse(fixedAnswer))
      lastSavedAnswer = serialized
      lastSavedHead = targetEventId
      serverDraftConfirmed = true
      saveStatus.value = JSON.stringify(paperFlow.value ? { answer: answer.value, inline_confidence: inlineConfidence } : answer.value) === serialized && trajectory?.head === targetEventId
        ? '草稿已保存' : '等待自动保存'
      leaseRetryNeeded = false
      error.value = ''
    } catch (caught) {
      if (state.value !== currentState || processRuntime !== fixedRuntime) throw caught
      if (isWriterLeaseConflict(caught)) { fixedLease?.invalidate(); leaseRetryNeeded = true }
      if (caught instanceof ApiError && ['RESPONSE_VERSION_CONFLICT', 'response_version_conflict', 'navigation_response_version_conflict'].includes(caught.code)) {
        serverDraftConfirmed = false
        responseConflict.value = { ...caught.fields, candidate_event_id: fixedTrajectory?.head || caught.fields.candidate_event_id,
          candidate_answer: cloneJson(answer.value) }
      }
      saveStatus.value = '草稿保存失败'
      error.value = `${caught instanceof Error ? caught.message : '草稿保存失败。'} 当前答案仍保留在本页，请不要关闭页面。`
      throw caught
    } finally {
      pointerDone?.()
      if (pointerDone) pointerWritePromise = undefined
    }
  }
  let queue = draftSaveQueues.get(fixedRuntime!)
  if (!queue) {
    queue = createDraftSaveQueue()
    draftSaveQueues.set(fixedRuntime!, queue)
  }
  return queue.enqueue(() => fixedRuntime!.serializePointerWrite(operation))
}

async function chooseSingle(value: string | null) {
  if (!interactionAllowed() || responseDisabled.value) return
  answer.value = value
  invalidateInlineConfidence()
  void noteInputStarted()
  captureChange(value, value === null ? 'clear' : 'select', { option_key: value, option_action: value === null ? 'clear' : 'select' })
  if (paperFlow.value) scheduleTextSave()
  else await saveDraft().catch(() => undefined)
}

async function toggleMultiple(value: string, selected: boolean) {
  if (!interactionAllowed() || responseDisabled.value) return
  const values = new Set(Array.isArray(answer.value) ? answer.value.map(String) : [])
  if (selected) values.add(value)
  else values.delete(value)
  answer.value = [...values].sort()
  invalidateInlineConfidence()
  void noteInputStarted()
  captureChange(answer.value, selected ? 'select' : 'deselect', { option_key: value, option_action: selected ? 'select' : 'deselect' })
  if (paperFlow.value) scheduleTextSave()
  else await saveDraft().catch(() => undefined)
}

function toggleMultipleFromEvent(value: string, event: unknown) {
  if (!interactionAllowed()) return
  const selected = Boolean((event as { target?: { checked?: boolean } }).target?.checked)
  void toggleMultiple(value, selected)
}

function updateText(event: unknown) {
  if (!interactionAllowed() || responseDisabled.value) return
  const value = String((event as { target?: { value?: unknown } }).target?.value ?? '')
  answer.value = value
  invalidateInlineConfidence()
  if (stableTextInput) {
    const details = event as InputEvent
    stableTextInput.update(value, details.inputType || 'insertText', composing || details.isComposing)
    void noteInputStarted()
    saveStatus.value = '正在编辑'
    return
  }
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
  scheduleTextSave()
}

function handleLocalInputError(caught: unknown) {
  saveStatus.value = '本地保存失败'
  error.value = `无法持久化作答材料：${caught instanceof Error ? caught.message : '存储不可用'}。请勿关闭本页。`
}

async function flushAnswerBoundary(reason: TextFlushReason) {
  if (stableTextInput) {
    if (composing) {
      const active = document.activeElement
      if (active instanceof HTMLInputElement || active instanceof HTMLTextAreaElement) active.blur()
      await nextTick()
    }
    await stableTextInput.flush(reason)
    await trajectory?.settled()
  }
  await commitInlineConfidence()
  if (inlineConfidenceError.value) throw new Error(inlineConfidenceError.value)
}

async function flushTextInputOnBlur() {
  try {
    await flushAnswerBoundary('blur')
    await flushTextObservation()
    if (stableTextInput) await saveDraft()
  } catch (caught) { error.value = caught instanceof Error ? caught.message : '草稿保存失败，请重试。' }
}

function captureChange(value: unknown, inputType: string, extra = {}) {
  if (!interactionAllowed() || !trajectory) return
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
  if (!interactionAllowed()) return
  if (textSaveTimer !== undefined) globalThis.clearTimeout(textSaveTimer)
  // Provisional local candidate; end-to-end capacity qualification remains separate.
  const nearDeadline = secondsRemaining.value !== null && secondsRemaining.value <= 1
  textSaveTimer = globalThis.setTimeout(() => { void saveDraft().catch(() => undefined) }, nearDeadline ? 0 : 100)
  if (saveMaximumTimer === undefined) saveMaximumTimer = setTimeout(() => { void saveDraft().catch(() => undefined) }, nearDeadline ? 0 : 250)
}

async function chooseResponseBranch(side: 'current' | 'candidate') {
  if (!interactionAllowed() || !state.value?.actions.resolve_branch_url || !responseConflict.value || !processRuntime) return
  await trajectory?.settled()
  if (await processRuntime.flushConfirmed() === false) {
    error.value = '作答轨迹尚未确认保存，请恢复网络后重试。'
    return
  }
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
  if (processRuntime) processRuntime.responseVersion = result.response_version
  lastSavedAnswer = JSON.stringify({ answer: answer.value, inline_confidence: inlineContract.value ? { probability: inlineProbability.value, answer_event_id: trajectory?.head || null } : undefined })
  lastSavedHead = result.active_response_event_id
  serverDraftConfirmed = true
  const runtime = processRuntime
  trajectory = new ResponseTrajectory(state.value.question.question_type, answer.value, result.active_response_event_id,
    (type, payload, options) => runtime.record(type, payload, options))
  responseConflict.value = undefined
  error.value = ''
  saveStatus.value = '已保存所选版本'
}

function beforeTextInput(event: Event) {
  if (!interactionAllowed() || !trajectory) return
  if (stableTextInput) { void noteInputStarted(); return }
  const input = event as InputEvent
  const target = input.target as HTMLInputElement | HTMLTextAreaElement
  inputOperationId = inputOperationId || crypto.randomUUID()
  trajectory.operation(input.inputType || 'input', composing ? 'updating' : 'none', target.selectionStart, target.selectionEnd, inputOperationId)
  void noteInputStarted()
}

function startComposition(event: Event) {
  if (!interactionAllowed()) return
  composing = true
  compositionInitial = String(answer.value ?? '')
  if (stableTextInput) { stableTextInput.compositionStart(); void noteInputStarted(); return }
  inputOperationId = crypto.randomUUID()
  const target = event.target as HTMLInputElement | HTMLTextAreaElement
  trajectory?.operation('insertCompositionText', 'started', target.selectionStart, target.selectionEnd, inputOperationId)
  void noteInputStarted()
}

function finishComposition(event: unknown) {
  if (!interactionAllowed()) return
  const value = String((event as { target?: { value?: unknown } }).target?.value ?? '')
  composing = false
  if (stableTextInput) {
    answer.value = value
    invalidateInlineConfidence()
    stableTextInput.compositionEnd(value)
    return
  }
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

async function closeAttemptAndRelease(reason: string, toAssignmentId?: number | null, nextEntry?: NextEntryRequest) {
  const runtime = processRuntime
  const lease = writerLease
  if (!runtime) return
  if (closePromise?.runtime === runtime) return closePromise.pending
  const pending = (async () => {
    const close = async () => {
      await runtime.pointerWritesSettled()
      const confirmed = await timedAssessmentStage('close', async () => nextEntry
        ? await runtime.closeAttempt(reason, toAssignmentId, nextEntry)
        : toAssignmentId === undefined
        ? await runtime.closeAttempt(reason)
        : await runtime.closeAttempt(reason, toAssignmentId))
      if (confirmed === false) throw new Error('作答轨迹尚未确认保存，请恢复网络后重试。')
      if (!runtime.closesWriterLease) await timedAssessmentStage('lease', () => lease?.release())
    }
    await close()
  })()
  closePromise = { runtime, pending }
  try { await pending }
  catch (caught) {
    if (closePromise?.pending === pending) closePromise = undefined
    throw caught
  }
}

function releaseWriterLeaseOnPageHide() {
  void stableTextInput?.flush('hidden').catch(handleLocalInputError)
  if (processRuntime?.closesWriterLease && closePromise?.runtime === processRuntime) return
  void writerLease?.release({ keepalive: true }).catch(() => undefined)
}

function openNavigation() {
  if (!interactionAllowed()) return
  navigationOpen.value = true
}

async function navigate(
  url: string,
  runtimePrepared = false,
  entryReason: string,
  navigation?: NavigationClickContext,
) {
  if (!interactionAllowed()) return
  await processRuntime?.pointerWritesSettled()
  if (!runtimePrepared && await processRuntime?.flushBounded(1500) === false) {
    throw new Error('作答轨迹尚未确认保存，请恢复网络后重试。')
  }
  if (/^\/student\/sessions\/\d+\/questions\/\d+\//.test(url)) {
    routeTransitionPrepared = runtimePrepared
    try {
      await router.push({
        path: url,
        query: {
          reason: entryReason,
          ...(navigation ? {
            navigation_id: navigation.navigationId,
            client_occurred_at: navigation.clientOccurredAt,
            client_monotonic_ms: String(navigation.clientMonotonicMs),
            client_time_origin: navigation.clientTimeOrigin,
          } : {}),
        },
      })
    } catch (caught) {
      routeTransitionPrepared = false
      throw caught
    }
    return
  }
  globalThis.location.assign(url)
}

async function navigateToAssignment(item: StudentRuntimeQuestionEntry['navigation'][number]) {
  if (!interactionAllowed() || item.current || !state.value || actionBusy.value || paperConfirmOpen.value || paperSubmission.value || navigationUncertain.value) return
  navigating.value = true
  processRuntime?.setOperationId?.(beginAssessmentOperation('navigation'))
  if (!pendingNavigationClick || pendingNavigationClick.assignmentId !== item.assignment_id) {
    pendingNavigationClick = {
      assignmentId: item.assignment_id,
      navigationId: globalThis.crypto.randomUUID(),
      clientOccurredAt: new Date().toISOString(),
      clientMonotonicMs: Math.round(performance.now() * 1000) / 1000,
      clientTimeOrigin: new Date(performance.timeOrigin).toISOString(),
    }
  }
  const navigation = pendingNavigationClick
  error.value = ''
  try {
    const entryIntent = await (await getSessionController(state.value.session_id, getBootstrapContext().csrf_token)).freezeEntry(state.value.session_id, item.assignment_id, 'navigation_click', navigation, { sourceAssignmentId: state.value.assignment.assignment_id, targetUrl: item.url })
    if (state.value.process.save_and_leave?.version === 1 && trajectory && !answerLocked.value) {
      await saveAndLeave(item, entryIntent)
      await navigate(item.url, true, 'navigation_click', navigation)
      return
    }
    await flushTextObservation()
    if (!answerLocked.value) await saveDraft(true)
    await closeAttemptAndRelease('navigation', item.assignment_id)
    await navigate(item.url, true, 'navigation_click', navigation)
  } catch (caught) {
    finishAssessmentOperation(false)
    error.value = caught instanceof Error
      ? caught.message
      : '草稿保存失败，尚未切换题目。'
  } finally { navigating.value = false }
}

async function saveAndLeave(item: StudentRuntimeQuestionEntry['navigation'][number], navigation?: NavigationClickContext & { clientInstanceId?: string }) {
  if (!state.value || !processRuntime || !trajectory) throw new Error('切题记录器尚未准备完成。')
  const runtime = processRuntime
  if (!runtime.prepareNavigation || !runtime.freezeNavigationSnapshot || !runtime.acknowledgeNavigation || !runtime.saveNavigationIntent) {
    throw new Error('切题恢复契约不完整，请刷新页面后重试。')
  }
  navigationSaving = true
  const budget = createOperationBudget(() => { saveStatus.value = '正在同步作答记录，切题结果尚待确认…' })
  try {
    if (!pendingNavigation.value) {
      if (textSaveTimer !== undefined) { globalThis.clearTimeout(textSaveTimer); textSaveTimer = undefined }
      await budget.run(() => flushAnswerBoundary('navigation'))
      await budget.run(flushTextObservation)
      await budget.run(() => trajectory!.settled())
      await budget.run(() => pointerWritePromise)
      await budget.run(() => runtime.pointerWritesSettled())
      if (!navigation) throw new Error('缺少本次切题令牌。')
      const credentials = writerLease?.cachedCredentials()
      const left = await budget.run(() => runtime.prepareNavigation!(navigation.navigationId, item.assignment_id))
      const intent: NavigationIntent = {
        token: navigation.navigationId, clickedAt: navigation.clientOccurredAt,
        toAssignmentId: item.assignment_id, leaveEventId: left.event_id,
        head: trajectory.head, expectedResponseVersion: processRuntime?.responseVersion ?? 0,
        ...(paperFlow.value && inlineContract.value ? { inlineConfidence: { probability: inlineProbability.value, answer_event_id: trajectory.head } } : {}),
        ...(state.value.process.transition_entry?.enabled && state.value.process.transition_entry.version === 1
          ? { nextEntry: nextEntryRequest({ ...navigation, assignmentId: item.assignment_id, reason: 'navigation_click' }) } : {}),
        ...(credentials ? { writerLeaseToken: credentials.writer_lease_token,
          writerClientInstanceId: credentials.writer_client_instance_id } : {}),
      }
      pendingNavigation.value = await budget.run(() => runtime.freezeNavigationSnapshot!(intent))
    }
    const intent = pendingNavigation.value
    if (!intent.processSnapshot) throw new Error('切题证据尚未持久化到本机。')
    const snapshot = intent.processSnapshot
    navigationUncertain.value = true
    const receipt = await budget.run(() => timedAssessmentStage('save_and_leave_remote', () => getStudentAssessmentApi().saveAndLeave(
      state.value!.actions.save_draft_url, {
        navigation_token: intent.token, leave_event_id: intent.leaveEventId,
        to_assignment_id: intent.toAssignmentId, target_event_id: intent.head,
        client_clicked_at: intent.clickedAt,
        ...(intent.writerLeaseToken ? { writer_lease_token: intent.writerLeaseToken,
          writer_client_instance_id: intent.writerClientInstanceId } : {}),
        save_and_leave: { version: 1, ...snapshot, expected_version: intent.expectedResponseVersion },
        ...(intent.nextEntry ? { next_entry: intent.nextEntry } : {}),
        ...(intent.inlineConfidence ? { inline_confidence: intent.inlineConfidence } : {}),
      },
    )))
    await runtime.acknowledgeNavigation(intent.processSnapshot, receipt)
    await (await getSessionController(state.value!.session_id, getBootstrapContext().csrf_token)).confirmLeave(state.value!.session_id, intent.token)
    if (intent.nextEntry) await (await getSessionController(state.value!.session_id, getBootstrapContext().csrf_token))
      .rememberEnteredQuestion(state.value!.session_id, intent.nextEntry, receipt.next_entry)
    writerLease?.invalidate()
    runtime.responseVersion = receipt.response_version
    if (paperFlow.value) await rememberPaperReceipt(state.value!.assignment.attempt_id, receipt.response_version, receipt.active_response_event_id)
    lastSavedHead = receipt.active_response_event_id
    lastSavedAnswer = JSON.stringify({ answer: answer.value, inline_confidence: inlineContract.value ? { probability: inlineProbability.value, answer_event_id: trajectory?.head || null } : undefined })
    serverDraftConfirmed = true
    saveStatus.value = '草稿已保存'
    await runtime.saveNavigationIntent(null)
    pendingNavigation.value = undefined
    navigationUncertain.value = false
  } catch (caught) {
    const intent = pendingNavigation.value
    const waitExpired = (caught as { code?: string })?.code === 'assessment_wait_expired'
    if (waitExpired) navigationUncertain.value = true
    if (!intent && navigation && !waitExpired) {
      runtime.cancelNavigation?.(navigation.navigationId)
      pendingNavigationClick = undefined
    }
    const material = intent?.processSnapshot ? {
      save_and_leave: { version: 1, ...intent.processSnapshot, expected_version: intent.expectedResponseVersion },
      leave_event_id: intent.leaveEventId, target_event_id: intent.head, to_assignment_id: intent.toAssignmentId,
      client_clicked_at: intent.clickedAt,
      ...(intent.nextEntry ? { next_entry: intent.nextEntry } : {}),
      ...(intent.inlineConfidence ? { inline_confidence: intent.inlineConfidence } : {}),
    } : undefined
    const rejected = intent && caught instanceof ApiError && caught.fields.navigation_rejected === true
      && caught.fields.navigation_token === intent.token
      && (caught.fields.receipt_version === undefined || material && matchesMutationRejection(caught, {
        mutationKind: 'navigation', attemptId: state.value!.assignment.attempt_id, navigationToken: intent.token,
        materialFingerprint: await mutationMaterialFingerprint(material),
      }))
    if (intent && rejected) {
      await runtime.saveNavigationIntent(null)
      await (await getSessionController(state.value!.session_id, getBootstrapContext().csrf_token)).cancelEntry(state.value!.session_id, intent.token)
      runtime.cancelNavigation?.(intent.token)
      pendingNavigation.value = undefined
      pendingNavigationClick = undefined
      navigationUncertain.value = false
      if (['RESPONSE_VERSION_CONFLICT', 'response_version_conflict', 'navigation_response_version_conflict'].includes(caught.code)) {
        responseConflict.value = { ...caught.fields, candidate_event_id: trajectory.head, candidate_answer: cloneJson(answer.value) }
      }
      if (isWriterLeaseConflict(caught)) { writerLease?.invalidate(); leaseRetryNeeded = true }
    }
    throw caught
  } finally { navigationSaving = false }
}

async function recoverNavigation() {
  const intent = pendingNavigation.value
  const item = state.value?.navigation.find((row) => row.assignment_id === (intent?.toAssignmentId ?? pendingNavigationClick?.assignmentId))
  if (!item || transitionBusy.value) return
  navigating.value = true
  error.value = ''
  try {
    await saveAndLeave(item, pendingNavigationClick)
    await navigate(item.url, true, 'navigation_click', pendingNavigationClick)
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '尚未确认切题保存结果，请重试核验。'
  } finally { navigating.value = false }
}

async function navigateFromDrawer(item: StudentRuntimeQuestionEntry['navigation'][number]) {
  if (!interactionAllowed()) return
  navigationOpen.value = false
  await navigateToAssignment(item)
}

function requestFinishFromDrawer() {
  navigationOpen.value = false
  void requestPaperSubmit()
}

function paperMetadataKey(kind: string) {
  const session = state.value?.session_id || Number(route.params.sessionId)
  const version = state.value?.prepared_paper?.content_version || paperSubmission.value?.content_version || 'v1'
  return `paper-${kind}:${getBootstrapContext().user?.id || 0}:${session}${kind === 'submission' ? '' : `:${version}`}`
}

async function paperStore() {
  return (await getSessionController(state.value?.session_id || Number(route.params.sessionId), getBootstrapContext().csrf_token)).store
}

async function rememberPaperReceipt(attemptId: number, version: number, head: string | null, presence = hasLocalResponse.value) {
  paperReceiptVersions[attemptId] = { attempt_id: attemptId, response_version: version, target_event_id: head }
  paperResponsePresence[attemptId] = presence
  const row = state.value?.navigation.find(item => (item as { attempt_id?: number }).attempt_id === attemptId)
  if (row && !['submitted', 'timed_out', 'unanswered'].includes(row.status)) row.status = presence ? 'draft' : 'visited'
  await (await paperStore()).writeMetadata(paperMetadataKey('receipts'), paperReceiptVersions)
  await (await paperStore()).writeMetadata(paperMetadataKey('presence'), paperResponsePresence)
}

function invalidateInlineConfidence() {
  if (!paperFlow.value) return
  inlineConfidenceInput.value = undefined
  inlineConfidenceError.value = ''
  if (inlineProbability.value === null) return
  inlineProbability.value = null
  void persistInlineConfidence(null).catch(() => undefined)
}

function inlineConfidenceKey(dto: StudentRuntimeQuestionEntry) {
  return `paper-confidence:${getBootstrapContext().user?.id || 0}:${dto.session_id}:${dto.prepared_paper?.content_version || 'v1'}:${dto.assignment.attempt_id}`
}

function persistInlineConfidence(probability: number | null, head = trajectory?.head || null, dto = state.value) {
  if (!dto || !inlineContract.value) return Promise.resolve()
  const key = inlineConfidenceKey(dto)
  const value = { probability, answer_event_id: head, scale_version: inlineContract.value.scale_version }
  inlineConfidenceWrites = inlineConfidenceWrites.then(async () => {
    const controller = await getSessionController(dto.session_id, getBootstrapContext().csrf_token)
    await controller.store.writeMetadata(key, value)
  }).catch(caught => {
    captureReady.value = false
    error.value = `作答信心未能保存到本机：${caught instanceof Error ? caught.message : '存储不可用'}。请勿关闭本页。`
    throw caught
  })
  return inlineConfidenceWrites
}

function updateInlineConfidence(event: Event) {
  if (responseDisabled.value || !hasLocalResponse.value || !inlineContract.value) return
  const value = (event.target as HTMLInputElement).value
  inlineConfidenceInput.value = value
  const probability = value === '' ? null : Number(value)
  const contract = inlineContract.value
  if (probability !== null && (!Number.isInteger(probability) || probability < contract.minimum || probability > contract.maximum || (probability - contract.minimum) % contract.step !== 0)) {
    inlineConfidenceError.value = `请输入 ${contract.minimum} 到 ${contract.maximum} 之间、间隔为 ${contract.step} 的整数。`
    return
  }
  inlineConfidenceError.value = ''
}

async function commitInlineConfidence() {
  if (!interactionAllowed() || answerLocked.value || !hasLocalResponse.value || !inlineContract.value
      || inlineConfidenceInput.value === undefined || inlineConfidenceError.value) return
  const value = inlineConfidenceInput.value
  const currentState = state.value
  await stableTextInput?.flush('blur')
  await trajectory?.settled()
  if (currentState !== state.value || inlineConfidenceInput.value !== value) return
  const probability = value === '' ? null : Number(value)
  inlineConfidenceInput.value = undefined
  if (inlineProbability.value === probability) return
  const head = trajectory?.head || null
  const contract = inlineContract.value
  try {
    await processRuntime?.record('confidence_observed', {
      capture_version: 'capture-v2', operation_id: crypto.randomUUID(),
      confidence_assignment_id: contract.confidence_assignment_id,
      stage: probability === null ? 'skipped' : 'responded', answer_event_id: head, probability,
      feedback_phase: 'before_feedback',
    }, { eventVersion: contract.scale_version === 'v2' ? 2 : 1 })
  } catch (caught) {
    if (currentState === state.value && inlineConfidenceInput.value === undefined) inlineConfidenceInput.value = value
    handleLocalInputError(caught)
    throw caught
  }
  inlineProbability.value = probability
  await persistInlineConfidence(probability, head, currentState)
  if (currentState === state.value) scheduleTextSave()
}

function acceptToolInlineConfidence(receipt: NonNullable<StudentRuntimeQuestionEntry['inline_confidence']>) {
  if (!paperFlow.value || !inlineContract.value || !hasLocalResponse.value || !receipt.answer_event_id || receipt.answer_event_id !== trajectory?.head) return
  inlineProbability.value = receipt.probability
  inlineConfidenceInput.value = undefined
  inlineConfidenceError.value = ''
  void persistInlineConfidence(receipt.probability, receipt.answer_event_id).catch(() => undefined)
}

async function requestPaperSubmit() {
  if (!interactionAllowed() || actionBusy.value || paperConfirmOpen.value || paperSubmission.value || !state.value || inlineConfidenceError.value) return
  try {
    if (paperManifest) await paperManifest
    paperConfirmTrigger = document.activeElement instanceof HTMLElement ? document.activeElement : null
    paperConfirmOpen.value = true
    await nextTick()
    document.querySelector<HTMLButtonElement>('[data-paper-submit-cancel]')?.focus()
  } catch (caught) { error.value = caught instanceof Error ? caught.message : '试卷清单尚未加载，请重试。' }
}

function cancelPaperSubmit() {
  paperConfirmOpen.value = false
  paperConfirmTrigger?.focus()
}

function trapPaperConfirmationFocus(event: KeyboardEvent) {
  const controls = [...(event.currentTarget as HTMLElement).querySelectorAll<HTMLButtonElement>('button:not([disabled])')]
  const first = controls[0], last = controls.at(-1)
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
}

async function acceptPaperStatus(response: PaperSubmissionResponse) {
  if (!paperSubmission.value || !response.success || !response.submission_id || !response.status_url) throw new Error('整卷提交回执不完整，请核验原操作。')
  if (paperSubmission.value.submission_id && paperSubmission.value.submission_id !== response.submission_id) throw new Error('整卷提交回执与原操作不一致。')
  Object.assign(paperSubmission.value, { submission_id: response.submission_id, status_url: response.status_url, status: response.status })
  await (await paperStore()).writeMetadata(paperMetadataKey('submission'), cloneJson(paperSubmission.value))
  if (countdown !== undefined) { clearInterval(countdown); countdown = undefined }
  if (response.status === 'completed') {
    paperStatus.value = '交卷处理完成。'
    finishAssessmentOperation()
    if (!response.next_url) throw new Error('提交已完成，但缺少后续页面。请核验状态。')
    globalThis.location.assign(response.next_url)
  } else if (response.status === 'failed') {
    paperStatus.value = response.error?.message || '提交处理失败，请核验提交状态。'
    finishing.value = false
    finishAssessmentOperation(false)
  } else paperStatus.value = response.status === 'queued'
    ? '试卷已受理，等待后台处理…'
    : response.processing_state === 'recovering'
      ? '试卷已受理，正在恢复后台处理…'
      : '试卷已受理，后台正在处理…'
}

let paperPollFlight: Promise<void> | undefined
async function pollPaperSubmission() {
  if (paperPollFlight) return paperPollFlight
  if (!paperSubmission.value || document.visibilityState === 'hidden') return
  if (paperPollTimer !== undefined) clearTimeout(paperPollTimer)
  paperPollTimer = undefined
  finishing.value = true
  const pending = (async () => {
    try {
      const intent = paperSubmission.value!
      const api = getStudentAssessmentApi(intent.operation_id ?? currentAssessmentOperationId() ?? null)
      const response = intent.status_url ? await api.paperSubmissionStatus(intent.status_url)
        : await api.finishPaper(intent.finish_url || state.value!.actions.finish_url, intent)
      await acceptPaperStatus(response)
      if (response.status === 'queued' || response.status === 'running') paperPollTimer = setTimeout(() => { void pollPaperSubmission() }, submissionPollDelay(paperPollIndex++))
    } catch (caught) {
      finishing.value = false
      paperStatus.value = '提交结果尚未确认。答案已冻结，请核验原操作。'
      error.value = caught instanceof Error ? caught.message : '提交状态查询失败。'
      if (caught instanceof ApiError && caught.status === 429 && caught.retryAfterSeconds !== undefined) {
        paperPollTimer = setTimeout(() => { void pollPaperSubmission() }, Math.max(1000, caught.retryAfterSeconds * 1000))
      }
    }
  })()
  paperPollFlight = pending
  try { await pending } finally { if (paperPollFlight === pending) paperPollFlight = undefined }
}

async function submitPaper(clickedAt = new Date().toISOString()) {
  if (!interactionAllowed() || !state.value || actionBusy.value || paperSubmission.value || inlineConfidenceError.value) return
  finishing.value = true
  paperConfirmOpen.value = false
  paperStatus.value = '正在保存作答材料…'
  error.value = ''
  const budget = createOperationBudget(() => { paperStatus.value = '正在准备交卷材料，等待同步确认…' })
  try {
    beginAssessmentOperation('submit')
    processRuntime?.setOperationId?.(currentAssessmentOperationId())
    await budget.run(() => flushAnswerBoundary('submit'))
    await budget.run(flushTextObservation)
    if (!answerLocked.value) await budget.run(() => saveDraft(true))
    await budget.run(() => trajectory?.settled())
    await budget.run(() => processRuntime?.pointerWritesSettled())
    await budget.run(() => closeAttemptAndRelease('finish'))
    await budget.run(() => processRuntime?.checkpoint('commit'))
    if (await budget.run(() => processRuntime?.flushConfirmed()) === false) throw new Error('作答材料尚未确认保存，请恢复网络后重试。')
    const manifest = paperManifest ? await budget.run(() => paperManifest) : undefined
    const versions: ExpectedAttemptVersion[] = manifest ? manifest.navigation.map(row => paperReceiptVersions[row.attempt_id]
      || { attempt_id: row.attempt_id, response_version: row.response_version, target_event_id: row.active_response_event_id })
      : Object.values(paperReceiptVersions)
    const current = { attempt_id: state.value.assignment.attempt_id, response_version: processRuntime?.responseVersion ?? state.value.process.response_version ?? 0, target_event_id: trajectory?.head || state.value.process.active_response_event_id || null }
    const index = versions.findIndex(row => row.attempt_id === current.attempt_id)
    if (index >= 0) versions[index] = current
    else versions.push(current)
    paperSubmission.value = { submission_token: crypto.randomUUID(), client_clicked_at: clickedAt, expected_versions: versions, finish_url: state.value.actions.finish_url, content_version: state.value.prepared_paper?.content_version || 'v1' }
    paperSubmission.value.operation_id = currentAssessmentOperationId()
    await budget.run(async () => (await paperStore()).writeMetadata(paperMetadataKey('submission'), cloneJson(paperSubmission.value)))
    paperStatus.value = '正在提交试卷，等待服务器确认…'
    finishing.value = false
    await budget.run(pollPaperSubmission)
  } catch (caught) {
    finishing.value = false
    paperStatus.value = paperSubmission.value ? '提交结果尚未确认，请核验原操作。' : '作答材料尚未确认保存，请重试。'
    error.value = caught instanceof Error ? caught.message : '作答材料保存失败，请重试。'
  }
}

function resumePaperPolling() {
  if (document.visibilityState === 'hidden') void stableTextInput?.flush('hidden').catch(handleLocalInputError)
  else stableTextInput?.resume()
  if (document.visibilityState !== 'hidden' && paperSubmission.value && !['completed', 'failed'].includes(paperSubmission.value.status || '')) void pollPaperSubmission()
}

function handleOffline() {
  online.value = false
  saveStatus.value = '当前离线，等待恢复后保存'
}

async function handleOnline() {
  if (!interactionAllowed() || reconnecting) return
  if (navigationUncertain.value) { online.value = true; return }
  reconnecting = true
  online.value = true
  try {
    if (trajectory && state.value) {
      await trajectory.settled()
      await processRuntime?.flushBounded(1500)
      const current = await getStudentAssessmentApi().question(state.value.session_id, state.value.assignment.assignment_id)
      if (current.offline_recovery) { online.value = false; return }
      if (processRuntime) processRuntime.responseVersion = current.process.response_version || 0
      lastSavedAnswer = JSON.stringify({ answer: current.draft_answer, inline_confidence: inlineContract.value ? { probability: current.inline_confidence?.probability ?? null, answer_event_id: current.process.active_response_event_id || null } : undefined })
      lastSavedHead = current.process.active_response_event_id || null
      serverDraftConfirmed = true
      state.value.assignment.status = current.assignment.status
      if (answerLocked.value) return
    }
    if (JSON.stringify(answer.value) !== lastSavedAnswer || (trajectory && (!serverDraftConfirmed || trajectory.head !== lastSavedHead))) await saveDraft(true)
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '重新同步失败，请重试。'
  } finally { reconnecting = false }
}

function isCurrentInitialization(
  generation: number,
  currentState: StudentRuntimeQuestionEntry,
  runtime: StudentProcessRuntime,
) {
  return generation === initializationGeneration
    && state.value === currentState
    && processRuntime === runtime
    && !timedOut.value
}

function preparationErrorDetails(caught: unknown) {
  const message = caught instanceof Error ? caught.message : '离线恢复组件未返回可识别的错误。'
  const code = typeof (caught as { code?: unknown })?.code === 'string'
    ? (caught as { code: string }).code
    : ''
  return {
    message: '作答环境准备失败，当前暂不能作答。请重试；持续失败请联系教师。',
    technicalDetails: code && !message.includes(code) ? `${code}: ${message}` : message,
  }
}

function startRecoveryTimer() {
  if (recoveryTimer !== undefined) return
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

async function finishInitialization(
  generation: number,
  currentState: StudentRuntimeQuestionEntry,
  runtime: StudentProcessRuntime,
) {
  if (!isCurrentInitialization(generation, currentState, runtime)) return
  if (currentState.process.capture_version === 'capture-v2' && !answerLocked.value && !currentState.offline_recovery) {
    await runtime.writerLease?.beforePointerWrite()
    if (!isCurrentInitialization(generation, currentState, runtime)) return
  }
  captureReady.value = true
  initializationState.value = 'ready'
  initializationError.value = undefined
  await nextTick()
  if (!isCurrentInitialization(generation, currentState, runtime)) return
  if (!itemRenderedRecorded && !navigationUncertain.value) {
    const readonlyView = paperFlow.value && answerLocked.value
    await runtime.record('item_rendered', { presentation_state: readonlyView ? 'readonly' : 'interactive' }, ...(readonlyView ? [{ eventVersion: 3 }] : []))
    await (await getSessionController(currentState.session_id, getBootstrapContext().csrf_token)).confirmEntry(currentState.session_id, currentState.assignment.assignment_id)
    if (!isCurrentInitialization(generation, currentState, runtime)) return
    itemRenderedRecorded = true
  }
  if (currentState.process.capture_version === 'capture-v2') startRecoveryTimer()
  if (paperFlow.value) {
    markPreparedStartupPhase('runtime_init_end')
    if (!responseDisabled.value) {
      if (document.querySelector<HTMLInputElement>('.kt-assessment-focus input[name="answer"]:not(:disabled), .kt-assessment-focus textarea[name="answer"]:not(:disabled)')) markPreparedStartupPhase('operable')
      operablePaperGeneration = generation
      if (paperPrefetch?.generation === generation) {
        paperPrefetch.start()
        paperPrefetch = undefined
      }
    }
  }
  runtime.startBackgroundCapture?.()
  finishAssessmentOperation()
  if (paperFlow.value && paperSubmission.value) void pollPaperSubmission()
}

async function prepareCaptureInitialization(generation = initializationGeneration) {
  if (preparationPromise) return preparationPromise
  const currentState = state.value
  const runtime = processRuntime
  if (!currentState || !runtime || currentState.process.capture_version !== 'capture-v2') return
  captureReady.value = false
  initializationState.value = 'preparing'
  initializationError.value = undefined
  const pending = (async () => {
    try {
      await timedAssessmentStage('offline_prepare', () => prepareOfflineQuestion(currentState))
      await finishInitialization(generation, currentState, runtime)
    } catch (caught) {
      if (isCurrentInitialization(generation, currentState, runtime)) {
        captureReady.value = false
        initializationState.value = 'failed'
        initializationError.value = preparationErrorDetails(caught)
      }
    }
  })()
  preparationPromise = pending
  try {
    await pending
  } finally {
    if (preparationPromise === pending) preparationPromise = undefined
  }
}

async function retryCaptureInitialization() {
  if (initializationState.value !== 'failed' && initializationState.value !== 'preparing') return
  await prepareCaptureInitialization()
}

function disposeQuestionRuntime() {
  stableTextInput?.dispose()
  stableTextInput = undefined
  closePromise = undefined
  initializationGeneration += 1
  preparationPromise = undefined
  pendingRouteInitialization = undefined
  captureReady.value = false
  if (countdown !== undefined) globalThis.clearInterval(countdown)
  if (textSaveTimer !== undefined) globalThis.clearTimeout(textSaveTimer)
  if (saveMaximumTimer !== undefined) { clearTimeout(saveMaximumTimer); saveMaximumTimer = undefined }
  if (paperPollTimer !== undefined) { clearTimeout(paperPollTimer); paperPollTimer = undefined }
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
  paperSubmission.value = undefined
  paperConfirmOpen.value = false
  paperStatus.value = ''
  toolsPending.value = false
  captureReady.value = false
  initializationState.value = 'loading'
  initializationError.value = undefined
  itemRenderedRecorded = false
  responseConflict.value = undefined
  trajectory = undefined
  writerLease = undefined
  leaseRetryNeeded = false
  composing = false
  inputOperationId = undefined
  state.value = undefined
  answer.value = null
  pendingNavigation.value = undefined
  navigationUncertain.value = false
  navigationSaving = false
  pointerWritePromise = undefined
  taskClosed.value = false
  secondsRemaining.value = null
  navigating.value = false
  finishing.value = false
  timedOut.value = false
  inputStarted = false
  lastSavedAnswer = 'null'
  lastSavedHead = undefined
  serverDraftConfirmed = false
  lastQueuedAnswer = 'null'
  lastQueuedHead = undefined
  lastRecordedResponseState = null
  lastStableText = ''
}

async function loadQuestion(targetRoute = route, deferCapturePreparation = false) {
  if (!currentAssessmentOperationId()) beginAssessmentOperation('entry')
  const generation = ++initializationGeneration
  const sessionId = Number(targetRoute.params.sessionId)
  const assignmentId = Number(targetRoute.params.assignmentId)
  loading.value = true
  initializationState.value = 'loading'
  initializationError.value = undefined
  error.value = ''
  try {
    const navigation = (
      deferCapturePreparation
      && typeof targetRoute.query.navigation_id === 'string'
      && typeof targetRoute.query.client_occurred_at === 'string'
      && typeof targetRoute.query.client_monotonic_ms === 'string'
      && typeof targetRoute.query.client_time_origin === 'string'
    ) ? {
        navigationId: targetRoute.query.navigation_id,
        clientOccurredAt: targetRoute.query.client_occurred_at,
        clientMonotonicMs: Number(targetRoute.query.client_monotonic_ms),
        clientTimeOrigin: targetRoute.query.client_time_origin,
      } : undefined
    const reason = targetRoute.query.reason === 'resume' || !deferCapturePreparation ? 'resume' : 'navigation_click'
    const controller = await getSessionController(sessionId, getBootstrapContext().csrf_token)
    const restoredPaper = await controller.store?.readMetadata<PaperSubmissionIntent>(paperMetadataKey('submission'))
    if (restoredPaper) {
      paperSubmission.value = restoredPaper
      paperStatus.value = '正在核验原试卷提交结果…'
      initializationState.value = 'ready'
      loading.value = false
      void pollPaperSubmission()
      return
    }
    const pendingEntry = await controller.pendingEntry(sessionId)
    if (generation !== initializationGeneration) return
    if (pendingEntry?.sourceAssignmentId === assignmentId && pendingEntry.leaveConfirmed && pendingEntry.targetUrl) {
      await router.replace(pendingEntry.targetUrl)
      return
    }
    let loadedState = await timedAssessmentStage('question_entry', async () => {
      const started = controller.takeStartedQuestion(sessionId, assignmentId)
      if (started) return started
      if (!navigator.onLine || (!state.value && isOfflineBootstrapRecovery(`/student/sessions/${sessionId}/questions/${assignmentId}/`))) {
        const dto = await getStudentAssessmentApi().question(sessionId, assignmentId)
        if (dto.offline_recovery) {
          if (pendingEntry?.assignmentId === assignmentId) controller.validateEntry(sessionId, pendingEntry, dto as StudentRuntimeQuestionEntry & { entry_receipt: import('./api').EntryReceipt })
          return dto
        }
      }
      const intent = await controller.freezeEntry(sessionId, assignmentId, reason, navigation)
      if (generation !== initializationGeneration) throw new DOMException('Initialization replaced', 'AbortError')
      const dto = controller.takeEnteredQuestion(sessionId, intent)
        || await getStudentAssessmentApi().enterQuestion(sessionId, assignmentId, intent.reason, { ...intent, sessionId })
      if (dto.task_id) clearAdmissionCredentials(dto.task_id)
      controller.validateEntry(sessionId, intent, dto)
      return dto
    })
    if (generation !== initializationGeneration) return
    if (loadedState.entry_allowed === false) {
      const recovered = await controller.recoverClosedSubmission(loadedState.process)
      if (generation !== initializationGeneration) return
      if (recovered) {
        await controller.confirmEntry(sessionId, assignmentId)
        if (generation !== initializationGeneration) return
        await router.replace(recovered.next_url)
        return
      }
      state.value = loadedState
      taskClosed.value = true
      error.value = loadedState.entry_denial_code === 'DEADLINE_EXPIRED' ? '作答时间已截止，请核验任务状态。' : '当前任务不允许继续作答，请返回任务列表核验状态。'
      initializationState.value = 'failed'
      finishAssessmentOperation(false)
      return
    }
    if (loadedState.assessment_flow_version !== 1) throw new Error('作答流程已更新，请返回任务列表重新进入试卷。')
    const hydrated = await hydratePreparedQuestion(loadedState, controller.store, getBootstrapContext().user?.id || 0)
    loadedState = hydrated.dto
    paperManifest = hydrated.manifest
    state.value = loadedState
    inlineProbability.value = loadedState.inline_confidence?.probability ?? null
    inlineConfidenceInput.value = undefined
    inlineConfidenceError.value = ''
    if (loadedState.assessment_flow_version === 1) {
      paperReceiptVersions = await controller.store.readMetadata<Record<number, ExpectedAttemptVersion>>(paperMetadataKey('receipts')) || {}
      paperResponsePresence = await controller.store.readMetadata<Record<number, boolean>>(paperMetadataKey('presence')) || {}
      paperResponsePresence[loadedState.assignment.attempt_id] = hasMeaningfulResponse(loadedState.draft_answer)
      paperReceiptVersions[loadedState.assignment.attempt_id] = { attempt_id: loadedState.assignment.attempt_id, response_version: loadedState.process.response_version || 0, target_event_id: loadedState.process.active_response_event_id || null }
      await controller.store.writeMetadata(paperMetadataKey('receipts'), paperReceiptVersions)
      await controller.store.writeMetadata(paperMetadataKey('presence'), paperResponsePresence)
      paperSubmission.value = await controller.store.readMetadata<PaperSubmissionIntent>(paperMetadataKey('submission')) || undefined
      if (paperManifest) void paperManifest.then(manifest => {
        if (generation !== initializationGeneration || !state.value) return
        state.value.navigation = manifest.navigation.map(row => ({ ...row, current: row.assignment_id === assignmentId, status: paperResponsePresence[row.attempt_id] === undefined ? row.status : paperResponsePresence[row.attempt_id] ? 'draft' : 'visited' }))
        const start = () => { void hydrated.cache?.prefetch(manifest.navigation.filter(row => row.assignment_id !== assignmentId).map(row => row.content)).catch(() => undefined) }
        if (operablePaperGeneration === generation) start()
        else paperPrefetch = { generation, start }
      }).catch(caught => { error.value = caught instanceof Error ? caught.message : '试卷清单加载失败。' })
    }
    pendingNavigationClick = pendingEntry?.sourceAssignmentId === assignmentId ? pendingEntry : undefined
    answer.value = cloneJson(state.value.draft_answer)
    if (state.value.offline_recovery) online.value = false
    lastSavedAnswer = JSON.stringify({ answer: answer.value, inline_confidence: inlineContract.value ? { probability: inlineProbability.value, answer_event_id: state.value.process.active_response_event_id || null } : undefined })
    lastQueuedAnswer = lastSavedAnswer
    lastRecordedResponseState = cloneJson(state.value.initial_response_state)
    lastStableText = typeof state.value.draft_answer === 'string'
      ? state.value.draft_answer
      : ''
    saveStatus.value = state.value.draft_answer === null ? '尚未保存' : '已恢复草稿'
    secondsRemaining.value = state.value.remaining_seconds
    if (state.value.presented_deadline_at) secondsRemaining.value = Math.max(0, Math.floor((Date.parse(state.value.presented_deadline_at) - Date.now()) / 1000))
    startCountdown()
    if (paperFlow.value) markPreparedStartupPhase('runtime_init_start')
    const loadedRuntime = await timedAssessmentStage('process_runtime', () => createProcessRuntime(
      loadedState.process,
      loadedState.assignment.status,
      getBootstrapContext().csrf_token, ...(!loadedState.offline_recovery && loadedState.initial_writer_lease ? [loadedState.initial_writer_lease] : []),
    ))
    if (generation !== initializationGeneration) {
      loadedRuntime.dispose()
      return
    }
    processRuntime = loadedRuntime
    if (loadedRuntime.restoredSubmission || loadedRuntime.restoredClosure) {
      throw new Error('检测到旧版提交记录，请返回任务列表核验；本机作答记录已保留。')
    }
    pendingNavigation.value = loadedRuntime.restoredNavigation || undefined
    navigationUncertain.value = !!pendingNavigation.value || Boolean(pendingNavigationClick)
    loadedRuntime.setOperationId?.(currentAssessmentOperationId())
    if (state.value.process.capture_version === 'capture-v2') {
      const process = state.value.process
      if (!process.writer_lease_acquire_url || !process.writer_lease_renew_url || !process.writer_lease_release_url || !processRuntime.clientInstanceId) throw new Error('缺少写入租约契约，无法保存正式答案。')
      if (!processRuntime.writerLease) throw new Error('会话写入租约尚未建立，无法保存正式答案。')
      writerLease = processRuntime.writerLease
      processRuntime.responseVersion = state.value.process.response_version || 0
      let head = state.value.process.active_response_event_id || null
      lastSavedHead = head
      lastQueuedHead = head
      serverDraftConfirmed = !state.value.offline_recovery
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
      const policy = (loadedState.process as typeof loadedState.process & { text_capture_policy?: { id: string; idle_ms: number } }).text_capture_policy
      if (policy?.id === 'stable-input-v1' && ['short_answer', 'fill_blank', 'numeric'].includes(kind) && !answerLocked.value) {
        const fixedTrajectory = trajectory
        const key = `text-candidate:${loadedState.session_id}:${loadedState.assignment.attempt_id}:${runtime.clientInstanceId}`
        const candidate = await controller.store.readMetadata<TextCandidate>(key)
        stableTextInput = createStableTextInput({ initialValue: String(initial), idleMs: policy.idle_ms,
          getHead: () => fixedTrajectory.head,
          persist: value => controller.store.writeMetadata(key, value),
          record: async (value, inputType, operationId, cancelled) => {
            fixedTrajectory.operation(inputType, cancelled ? 'cancelled' : inputType === 'insertCompositionText' ? 'confirmed' : 'none', null, null, operationId)
            if (!cancelled) fixedTrajectory.change(value, inputType, operationId)
            await fixedTrajectory.settled()
            if (processRuntime === runtime) saveStatus.value = '已保存到本机，等待同步'
          },
          onStable: async () => { if (processRuntime === runtime) await saveDraft().catch(() => undefined) },
          onError: handleLocalInputError,
        })
        if (candidate && !candidate.composing && candidate.baseHead === head && typeof candidate.value === 'string') {
          answer.value = candidate.value
          stableTextInput.update(candidate.value, 'insertReplacementText', false)
          saveStatus.value = '已恢复本机草稿，等待同步'
        }
      }
    }
    if (paperFlow.value && inlineContract.value) {
      const local = await controller.store.readMetadata<{ probability: number | null; answer_event_id: string | null; scale_version: string }>(inlineConfidenceKey(loadedState))
      const head = trajectory?.head || loadedState.process.active_response_event_id || null
      if (local && local.answer_event_id === head && local.scale_version === inlineContract.value.scale_version) inlineProbability.value = local.probability
      else if (loadedState.inline_confidence?.answer_event_id !== head) inlineProbability.value = null
      if (!hasLocalResponse.value) inlineProbability.value = null
    }
    loading.value = false
    initializationState.value = 'preparing'
    await nextTick()
    if (state.value.process.capture_version === 'capture-v2') {
      if (deferCapturePreparation) {
        pendingRouteInitialization = { generation, sessionId, assignmentId }
      } else {
        await prepareCaptureInitialization(generation)
      }
    } else {
      await finishInitialization(generation, state.value, processRuntime)
    }
  } catch (caught) {
    if (generation !== initializationGeneration) return
    error.value = caught instanceof Error ? caught.message : '作答页面加载失败。'
    if (caught instanceof ApiError && (caught.status === 409 || caught.status >= 500)) {
      try {
        const recovered = await getStudentAssessmentApi().recoverQuestion(sessionId, assignmentId)
        if (generation !== initializationGeneration) return
        if (recovered.result) {
          const url = recovered.result.next_url
          if (/^\/student\/sessions\/\d+\/questions\/\d+\//.test(url)) await router.push(url)
          else globalThis.location.assign(url)
          return
        }
        if (!recovered.can_continue) error.value = recovered.code === 'DEADLINE_EXPIRED'
          ? '作答时间已截止，尚未正式提交的内容不会计为提交。'
          : '任务已关闭，请返回任务列表查看当前状态。'
      } catch { error.value = '暂时无法核验任务状态。请恢复网络后重试，原任务与本机作答记录会保留。' }
    }
  } finally {
    if (generation === initializationGeneration) loading.value = false
  }
}

const removeAfterEach = router.afterEach((to, _from, failure) => {
  if (failure) return
  if (typeof to.query.navigation_id === 'string') {
    void router.replace(
      `/student/sessions/${String(to.params.sessionId)}/questions/${String(to.params.assignmentId)}/`,
    )
  }
  const pending = pendingRouteInitialization
  if (
    !pending
    || pending.generation !== initializationGeneration
    || Number(to.params.sessionId) !== pending.sessionId
    || Number(to.params.assignmentId) !== pending.assignmentId
  ) return
  pendingRouteInitialization = undefined
  void prepareCaptureInitialization(pending.generation)
})

onBeforeRouteUpdate(async (to) => {
  if (
    String(to.params.sessionId) === String(route.params.sessionId)
    && String(to.params.assignmentId) === String(route.params.assignmentId)
  ) return
  if (toolsPending.value) return false
  if (state.value && !interactionAllowed()) return false
  const targetAssignmentId = Number(to.params.assignmentId)
  if (navigationUncertain.value && !routeTransitionPrepared) return false
  try {
    if (paperSubmission.value) return false
    if (!routeTransitionPrepared && state.value) {
      const destination = state.value.navigation.find((row) => row.assignment_id === targetAssignmentId)
      if (trajectory && state.value.process.save_and_leave?.version === 1 && destination && !answerLocked.value) {
        navigationSaving = true
        const navigation = { navigationId: crypto.randomUUID(), clientOccurredAt: new Date().toISOString(),
          clientMonotonicMs: performance.now(), clientTimeOrigin: new Date(performance.timeOrigin).toISOString() }
        try {
          const entryIntent = await (await getSessionController(state.value.session_id, getBootstrapContext().csrf_token)).freezeEntry(state.value.session_id, targetAssignmentId, 'navigation_click', navigation, { sourceAssignmentId: state.value.assignment.assignment_id, targetUrl: destination.url })
          await saveAndLeave(destination, entryIntent)
        } finally { navigationSaving = false }
      } else {
        await processRuntime?.pointerWritesSettled()
        await flushTextObservation()
        if (!answerLocked.value) await saveDraft(true)
        await closeAttemptAndRelease('navigation', targetAssignmentId)
      }
    } else await processRuntime?.pointerWritesSettled()
    routeTransitionPrepared = false
    disposeQuestionRuntime()
    resetQuestionState()
    await loadQuestion(to, true)
  } catch (caught) {
    routeTransitionPrepared = false
    error.value = caught instanceof Error ? caught.message : '切换题目失败，请重试。'
    return false
  }
})

onBeforeRouteLeave(async () => {
  if (paperSubmission.value && paperSubmission.value.status !== 'completed') return false
  if (navigationUncertain.value || toolsPending.value) return false
  if (!state.value || !interactionAllowed()) return
  try {
    await flushTextObservation()
    if (!answerLocked.value) await saveDraft(true)
    await closeAttemptAndRelease('navigation')
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '离开作答页面失败，请重试。'
    return false
  }
})

onMounted(async () => {
  if (!checkCapabilities()) {
    loading.value = false
    return
  }
  document.addEventListener('visibilitychange', resumePaperPolling)
  globalThis.addEventListener('offline', handleOffline)
  globalThis.addEventListener('online', handleOnline)
  globalThis.addEventListener('pagehide', releaseWriterLeaseOnPageHide)
  await loadQuestion()
})

onBeforeUnmount(() => {
  releaseWriterLeaseOnPageHide()
  disposeSessionController(Number(route.params.sessionId))
  disposeQuestionRuntime()
  removeAfterEach()
  document.removeEventListener('visibilitychange', resumePaperPolling)
  globalThis.removeEventListener('offline', handleOffline)
  globalThis.removeEventListener('online', handleOnline)
  globalThis.removeEventListener('pagehide', releaseWriterLeaseOnPageHide)
})
</script>

<template>
  <main class="kt-assessment-focus" :data-init-state="initializationState">
    <InitialPasswordReminder v-if="initialPasswordReminderPending" />
    <section v-if="missingCapabilities.length" class="kt-assessment-capability" role="alert">
      <h1>当前浏览器无法安全保存作答过程</h1>
      <p>请升级浏览器或更换受支持的浏览器后再进入实验。</p>
      <p>缺少能力：{{ missingCapabilities.join('、') }}</p>
    </section>
    <p v-else-if="loading" role="status">正在加载作答页面…</p>
    <section v-else-if="paperSubmission && !state" class="kt-assessment-capability" data-paper-submission-recovery>
      <h1>正在确认试卷提交结果</h1>
      <p role="status" data-paper-submission-status>{{ paperStatus }}</p>
      <p v-if="error" role="alert">{{ error }}</p>
      <button v-if="!finishing" type="button" class="kt-button" data-paper-submission-retry @click="pollPaperSubmission">核验提交状态</button>
    </section>
    <section v-else-if="!state && error" class="kt-alert kt-alert--danger" role="alert">
      <p>{{ error }}</p><button type="button" class="kt-button" @click="loadQuestion()">重试进入原题目</button>
      <a href="/student/">返回任务列表</a>
    </section>
    <div v-else-if="state" class="kt-assessment-layout">
      <header class="kt-assessment-topbar" data-assessment-topbar>
        <a class="kt-assessment-topbar__return" data-assessment-return href="/student/" @click.prevent="router.push('/student/')">返回作业列表</a>
        <div class="kt-assessment-topbar__countdown" data-assessment-countdown>
          <span v-if="secondsRemaining !== null" role="timer" aria-live="off">剩余 {{ timerLabel(secondsRemaining) }}</span>
          <span v-else>不限时</span>
          <p class="kt-assessment-save-status" role="status" data-save-status>{{ saveStatus }}</p>
        </div>
        <div class="kt-assessment-topbar__status">
          <div class="kt-assessment-topbar__student" data-assessment-student>
            <strong>姓名：{{ assessmentUser?.display_name || '未提供' }}</strong>
            <span>学号：{{ assessmentUser?.student_number || '未提供' }}</span>
          </div>
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
          :disabled="!captureReady"
          @click="openNavigation"
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
          题型：{{ questionTypeLabel(state.question.question_type) }}<span v-if="state.question.primary_kc"> 主 KC：{{ state.question.primary_kc }}</span>
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
          <button v-if="trajectory && state.question.question_type !== 'multiple_choice'" type="button" class="kt-button kt-button--secondary" data-clear-response :disabled="responseDisabled || answer === null" @click="chooseSingle(null)">清除选择</button>
        </fieldset>
        <label v-else class="kt-assessment-text-answer">
          <span>你的答案</span>
          <textarea v-if="state.question.question_type === 'short_answer'" name="answer" rows="6" :disabled="responseDisabled" :value="String(answer ?? '')" @beforeinput="beforeTextInput" @input="updateText" @compositionstart="startComposition" @compositionend="finishComposition" @blur="flushTextInputOnBlur" />
          <input v-else name="answer" :disabled="responseDisabled" :inputmode="state.question.question_type === 'numeric' ? 'decimal' : undefined" :value="String(answer ?? '')" @beforeinput="beforeTextInput" @input="updateText" @compositionstart="startComposition" @compositionend="finishComposition" @blur="flushTextInputOnBlur">
        </label>
        <div v-if="paperFlow" class="kt-assessment-paper-actions">
          <button type="button" class="kt-button kt-button--secondary" data-previous-question :disabled="!previousQuestion || !captureReady || actionBusy || navigationUncertain || !!paperSubmission" @click="previousQuestion && navigateToAssignment(previousQuestion)">上一题</button>
          <label v-if="inlineContract" class="kt-assessment-inline-confidence">
            <span>作答信心（可选）</span>
            <input data-inline-confidence type="number" :value="inlineConfidenceInput ?? inlineProbability ?? ''" :min="inlineContract.minimum" :max="inlineContract.maximum" :step="inlineContract.step" :disabled="responseDisabled || !hasLocalResponse" placeholder="未评价" @input="updateInlineConfidence" @blur="commitInlineConfidence" @keydown.enter.prevent="commitInlineConfidence">
            <span>%</span>
            <small v-if="inlineConfidenceError" role="alert">{{ inlineConfidenceError }}</small>
          </label>
          <button type="button" class="kt-button" data-next-question :disabled="!captureReady || actionBusy || navigationUncertain || !!paperSubmission" @click="nextQuestion ? navigateToAssignment(nextQuestion) : requestPaperSubmit()">{{ nextQuestion ? '下一题' : '提交试卷' }}</button>
        </div>
        <StudentResearchTools v-if="trajectory && processRuntime && state.actions.research_tools_url && !answerLocked && captureReady && online && !transitionBusy && !navigationUncertain" :endpoint="state.actions.research_tools_url" :runtime="processRuntime" :answer-event-id="trajectory.head" :initial-configuration="state.offline_recovery ? undefined : state.research_tools" :wait-for-capture="() => trajectory!.settled()" @interaction="noteInputStarted" @pending="toolsPending = $event" @inline-confidence="acceptToolInlineConfidence" />
        <p v-if="initializationState === 'preparing'" class="kt-alert kt-alert--info" role="status" data-init-preparing>正在准备安全作答环境，请稍候…</p>
        <section v-else-if="initializationState === 'failed' && initializationError" class="kt-alert kt-alert--danger" role="alert" data-init-failure>
          <h2>安全作答环境准备失败</h2>
          <p>{{ initializationError.message }}</p>
          <p data-init-technical-details><strong>技术信息：</strong><code>{{ initializationError.technicalDetails }}</code></p>
          <div class="kt-assessment-actions">
            <button type="button" class="kt-button" data-init-retry @click="retryCaptureInitialization">重新准备</button>
            <a class="kt-button kt-button--secondary" data-init-return href="/student/">返回任务列表</a>
          </div>
        </section>
        <p v-if="error" class="kt-alert kt-alert--danger" role="alert">{{ error }}</p>
        <button v-if="navigationUncertain" type="button" class="kt-button" data-navigation-retry :disabled="actionBusy" @click="recoverNavigation">重新核验切题保存结果</button>
        <button v-if="trajectory && online && captureReady && !answerLocked && saveStatus.includes('失败')" class="kt-button kt-button--secondary" type="button" @click="saveDraft(true).catch(() => undefined)">重试同步</button>
        <section v-if="responseConflict" class="kt-alert kt-alert--warning" aria-label="选择答案版本">
          <p>另一设备保存了不同答案。请选择接下来使用的版本。</p>
          <p>已保存版本：{{ formatCorrectAnswer(responseConflict.current_answer) }}</p>
          <button class="kt-button kt-button--secondary" type="button" :disabled="!captureReady" @click="chooseResponseBranch('current')">继续使用已保存版本</button>
          <p>本机版本：{{ formatCorrectAnswer(responseConflict.candidate_answer) }}</p>
          <button class="kt-button" type="button" :disabled="!captureReady" @click="chooseResponseBranch('candidate')">继续使用本机版本</button>
        </section>
        <p v-if="!online" class="kt-alert kt-alert--warning" role="status" data-connectivity-status>当前离线。答案保留在本页，恢复网络后将自动保存。</p>
        <p v-if="answerLocked" class="kt-alert kt-alert--info">本题已提交并锁定，答案不可再修改。</p>
        <p v-if="paperFlow && paperStatus" role="status" data-paper-submission-status>{{ paperStatus }}</p>
        <button v-if="paperFlow && paperSubmission && !finishing" type="button" class="kt-button" data-paper-submission-retry @click="pollPaperSubmission">核验提交状态</button>
      </section>
      <aside class="kt-assessment-nav" aria-labelledby="assessment-navigation-title">
        <div class="kt-assessment-nav__header">
          <h2 id="assessment-navigation-title">题目导航</h2>
          <span>{{ answeredQuestionCount }}/{{ state.progress.total }}</span>
        </div>
        <nav aria-label="题目状态">
          <a
            v-for="item in state.navigation"
            :key="item.assignment_id"
            :href="captureReady && !actionBusy ? item.url : undefined"
            class="kt-assessment-nav__item"
            :data-answer-status="item.status"
            :data-has-response="item.current ? hasLocalResponse : ['draft', 'submitted'].includes(item.status)"
            :aria-current="item.current ? 'step' : undefined"
            :aria-disabled="!captureReady || actionBusy ? 'true' : undefined"
            :tabindex="!captureReady || actionBusy ? -1 : undefined"
            :data-nav-assignment="item.assignment_id"
            @click.prevent="navigateToAssignment(item)"
          >
            <span>第 {{ item.sequence_index }} 题</span>
            <small>{{ item.current && hasLocalResponse && !answerLocked ? '待提交' : statusLabel(item.status) }}</small>
          </a>
        </nav>
        <p v-if="navigating" role="status" data-navigation-status>正在切换题目…</p>
        <button type="button" class="kt-button kt-button--danger-secondary" data-finish-request :disabled="actionBusy || !captureReady" @click="requestPaperSubmit">提交试卷</button>
      </aside>

      <Drawer
        id="assessment-mobile-navigation"
        :open="navigationOpen"
        title="题目导航"
        @close="navigationOpen = false"
      >
        <p class="kt-assessment-drawer-progress">已作答 {{ answeredQuestionCount }}/{{ state.progress.total }}</p>
        <nav aria-label="移动端题目状态">
          <a
            v-for="item in state.navigation"
            :key="item.assignment_id"
            :href="captureReady && !actionBusy ? item.url : undefined"
            class="kt-assessment-nav__item"
            :data-answer-status="item.status"
            :data-has-response="item.current ? hasLocalResponse : ['draft', 'submitted'].includes(item.status)"
            :aria-current="item.current ? 'step' : undefined"
            :aria-disabled="!captureReady || actionBusy ? 'true' : undefined"
            :tabindex="!captureReady || actionBusy ? -1 : undefined"
            @click.prevent="navigateFromDrawer(item)"
          >
            <span>第 {{ item.sequence_index }} 题</span>
            <small>{{ item.current && hasLocalResponse && !answerLocked ? '待提交' : statusLabel(item.status) }}</small>
          </a>
        </nav>
        <button
          type="button"
          class="kt-button kt-button--danger-secondary"
          :disabled="finishing || !captureReady"
          @click="requestFinishFromDrawer"
        >
          提交试卷
        </button>
      </Drawer>
    </div>
    <div v-if="paperFlow && paperConfirmOpen" class="kt-dialog-backdrop">
      <section class="kt-dialog" role="dialog" aria-modal="true" aria-labelledby="paper-submit-dialog-title" aria-describedby="paper-submit-dialog-description" :data-paper-missing-confirm="missingAnswerCount > 0 ? '' : undefined" @keydown.tab="trapPaperConfirmationFocus" @keydown.esc="cancelPaperSubmit">
        <h2 id="paper-submit-dialog-title">确认提交试卷</h2>
        <p v-if="missingAnswerCount > 0">还有 {{ missingAnswerCount }} 道题未作答。仍要提交试卷吗？</p>
        <p id="paper-submit-dialog-description">提交后试卷将锁定，不能继续修改答案。</p>
        <div class="kt-dialog__actions">
          <button type="button" class="kt-button kt-button--secondary" data-paper-submit-cancel @click="cancelPaperSubmit">继续作答</button>
          <button type="button" class="kt-button" data-paper-submit-confirm :disabled="actionBusy || !captureReady" @click="submitPaper()">确认提交</button>
        </div>
      </section>
    </div>
  </main>
</template>
