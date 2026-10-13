import { createWriterLease } from './writer-lease'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  pendingEntry: vi.fn(), confirmLeave: vi.fn(), recoverClosedSubmission: vi.fn(), confirmEntry: vi.fn(), offlineBootstrapRecovery: false,
  api: {
    question: vi.fn(),
    enterQuestion: (...args: unknown[]) => mocks.api.question(...args),
    saveDraft: vi.fn(),
    saveAndLeave: vi.fn(),
    submit: vi.fn(),
    skip: vi.fn(),
    post: vi.fn(),
    recover: vi.fn(),
  },
  push: vi.fn(),
  replace: vi.fn(),
  beforeRouteUpdate: undefined as undefined | ((to: {
    params: { sessionId: string; assignmentId: string }
    query: Record<string, string>
  }) => Promise<unknown>),
  beforeRouteLeave: undefined as undefined | (() => Promise<unknown>),
  afterRouteEach: undefined as undefined | ((to: {
    params: { sessionId: string; assignmentId: string }
    query: Record<string, string>
  }) => void),
  removeAfterEach: vi.fn(),
  createProcessRuntime: vi.fn(),
  prepareOfflineQuestion: vi.fn(),
  runtime: {
    closesWriterLease: false,
    writerLease: undefined as import('./writer-lease').WriterLease | undefined,
    serializePointerWrite: vi.fn(),
    captureMutation: undefined as undefined | ReturnType<typeof vi.fn>,
    pointerWritesSettled: vi.fn(),
    clientInstanceId: '00000000-0000-4000-8000-000000000002',
    record: vi.fn(),
    checkpoint: vi.fn(),
    flushBounded: vi.fn(),
    flushConfirmed: vi.fn(),
    closeAttempt: vi.fn(),
    dispose: vi.fn(),
    saveSubmissionIntent: vi.fn(),
    saveClosureIntent: vi.fn(),
    freezeSubmissionSnapshot: vi.fn(),
    acknowledgeSubmission: vi.fn(),
    prepareNavigation: vi.fn(),
    freezeNavigationSnapshot: vi.fn(),
    acknowledgeNavigation: vi.fn(),
    saveNavigationIntent: vi.fn(),
  },
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { sessionId: '7', assignmentId: '11' }, query: {} }),
  useRouter: () => ({
    push: mocks.push,
    replace: mocks.replace,
    afterEach: (callback: typeof mocks.afterRouteEach) => {
      mocks.afterRouteEach = callback
      return mocks.removeAfterEach
    },
  }),
  onBeforeRouteUpdate: (callback: typeof mocks.beforeRouteUpdate) => {
    mocks.beforeRouteUpdate = callback
  },
  onBeforeRouteLeave: (callback: typeof mocks.beforeRouteLeave) => {
    mocks.beforeRouteLeave = callback
  },
}))
vi.mock('./api', () => ({ getStudentAssessmentApi: () => mocks.api }))
vi.mock('./session-controller', () => ({
  createProcessRuntime: (...args: unknown[]) => mocks.createProcessRuntime(...args),
  disposeSessionController: vi.fn(),
  getSessionController: async () => ({ freezeEntry: async (_session: number, assignmentId: number, reason: string, click?: unknown) => ({ assignmentId, reason,
    navigationId: crypto.randomUUID(), clientOccurredAt: new Date().toISOString(), clientMonotonicMs: performance.now(), clientTimeOrigin: new Date(performance.timeOrigin).toISOString(), ...(click as object || {}) }), pendingEntry: mocks.pendingEntry, confirmLeave: mocks.confirmLeave, recoverClosedSubmission: mocks.recoverClosedSubmission, validateEntry: vi.fn(), confirmEntry: mocks.confirmEntry, cancelEntry: vi.fn() }),
}))
vi.mock('./offline-cache', () => ({
  prepareOfflineQuestion: (...args: unknown[]) => mocks.prepareOfflineQuestion(...args),
}))
vi.mock('@/app/bootstrap', () => ({
  getBootstrapContext: () => ({ csrf_token: 'csrf-token' }),
  isOfflineBootstrapRecovery: () => mocks.offlineBootstrapRecovery,
}))

import StudentAssessment from './StudentAssessment.vue'
import { ApiError } from '@/api/client'

const questionState = {
  task_id: 19,
  session_id: 7,
  session_status: 'in_progress',
  experiment_name: '反馈策略实验',
  assignment: { assignment_id: 11, attempt_id: 13, status: 'visited', sequence_index: 1 },
  progress: { submitted: 0, total: 2 },
  remaining_seconds: 600,
  question: {
    snapshot_id: 23,
    stable_code: 'C000001-Q000001',
    code: 'Q1',
    question_type: 'single_choice',
    text: '2 + 2 等于多少？',
    primary_kc: '基础运算',
    options: [
      { key: 'A', text: '3' },
      { key: 'B', text: '4' },
    ],
  },
  draft_answer: null,
  initial_response_state: null,
  pending_confidence: null,
  navigation: [
    { assignment_id: 11, sequence_index: 1, status: 'visited', url: '/student/sessions/7/questions/11/', current: true },
    { assignment_id: 12, sequence_index: 2, status: 'unvisited', url: '/student/sessions/7/questions/12/', current: false },
  ],
  actions: {
    save_draft_url: '/student/attempts/13/draft/',
    submit_url: '/student/attempts/13/submit/',
    skip_url: '/student/attempts/13/skip/',
    finish_url: '/student/sessions/7/finish/',
  },
  process: {
    visit_id: 41,
    visit_index: 1,
    entry_event_id: '00000000-0000-4000-8000-000000000040',
    navigation_id: '00000000-0000-4000-8000-000000000041',
    session_id: 7,
    attempt_id: 13,
    event_batch_url: '/assessment/process/events/batch/',
    checkpoint_url: '/assessment/process/capture-checkpoints/',
    time_sync_create_url: '/assessment/process/time-sync/create/',
    time_sync_finalize_url: '/assessment/process/time-sync/finalize/',
    app_version: '2026.08.29.2',
    heartbeat_interval_seconds: 30,
  },
}

function captureV2Question(overrides: Record<string, unknown> = {}) {
  return {
    ...structuredClone(questionState),
    draft_answer: 'B',
    remaining_seconds: 10,
    process: {
      ...structuredClone(questionState.process),
      capture_version: 'capture-v2',
      response_version: 0,
      active_response_event_id: null as string | null,
      writer_lease_acquire_url: '/assessment/process/capture-v2/attempts/13/lease/acquire/',
      writer_lease_renew_url: '/assessment/process/capture-v2/attempts/13/lease/renew/',
      writer_lease_release_url: '/assessment/process/capture-v2/attempts/13/lease/release/',
    },
    ...overrides,
  }
}

function preparationFailure() {
  return Object.assign(
    new Error('离线恢复准备失败：Error: capture_contract_unavailable'),
    { code: 'capture_contract_unavailable' },
  )
}

const confidenceOnlyContract = {
  confidence_assignment_id: '31', scale_version: 'v2', prompt_version: 'v1', minimum: 0, maximum: 100, step: 1,
  prompt_shown_url: '/assessment/process/confidence/13/prompt-shown/',
  commit_url: '/assessment/process/confidence/13/commit/', skip_url: '/assessment/process/confidence/13/skip/',
}

describe('StudentAssessment', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.runtime.captureMutation = undefined
    mocks.runtime.closesWriterLease = false
    mocks.offlineBootstrapRecovery = false
    mocks.pendingEntry.mockResolvedValue(null)
    mocks.beforeRouteUpdate = undefined
    mocks.beforeRouteLeave = undefined
    mocks.afterRouteEach = undefined
    vi.stubGlobal('indexedDB', {})
    Object.defineProperty(globalThis.navigator, 'onLine', { configurable: true, value: true })
    vi.stubGlobal('crypto', {
      randomUUID: () => '00000000-0000-4000-8000-000000000001',
      subtle: {
        digest: vi.fn().mockResolvedValue(new Uint8Array(32).buffer),
      },
    })
    mocks.api.question.mockResolvedValue(structuredClone(questionState))
    mocks.recoverClosedSubmission.mockResolvedValue(null)
    mocks.api.recover.mockResolvedValue({ status: 'draft', can_continue: true, code: '', result: null })
    mocks.api.saveDraft.mockResolvedValue({ success: true, saved_at: '2026-08-30T08:00:00Z' })
    mocks.api.saveAndLeave.mockResolvedValue({ success: true, committed: true,
      navigation_token: '00000000-0000-4000-8000-000000000001', to_assignment_id: 12,
      next_url: '/student/sessions/7/questions/12/', response_version: 1,
      active_response_event_id: '00000000-0000-4000-8000-000000000050', writer_lease_released: true,
      acknowledgements: [], checkpoint_ids: ['navigation-checkpoint'] })
    mocks.api.submit.mockResolvedValue({
      success: true,
      status: 'submitted',
      next_url: '/student/sessions/7/questions/12/',
      confidence_required: false,
    })
    mocks.api.skip.mockResolvedValue({
      success: true,
      next_url: '/student/sessions/7/questions/12/',
    })
    mocks.api.post.mockResolvedValue({
      success: true,
      next_url: '/student/sessions/7/questions/12/',
      feedback_mode: 'correctness',
      is_correct: true,
    })
    const leaseProcess = captureV2Question().process
    mocks.runtime.writerLease = createWriterLease({ client: mocks.api, clientInstanceId: mocks.runtime.clientInstanceId, acquireUrl: leaseProcess.writer_lease_acquire_url, renewUrl: leaseProcess.writer_lease_renew_url, releaseUrl: leaseProcess.writer_lease_release_url })
    mocks.createProcessRuntime.mockResolvedValue(mocks.runtime)
    mocks.prepareOfflineQuestion.mockResolvedValue(undefined)
    let pointerQueue = Promise.resolve<unknown>(undefined)
    mocks.runtime.serializePointerWrite.mockImplementation((operation) => { const result = pointerQueue.then(operation, operation); pointerQueue = result.catch(() => undefined); return result })
    mocks.runtime.pointerWritesSettled.mockImplementation(() => pointerQueue)
    mocks.runtime.record.mockResolvedValue({ recorded: true })
    mocks.runtime.saveSubmissionIntent.mockImplementation(async (intent) => { structuredClone(intent) })
    mocks.runtime.saveClosureIntent.mockImplementation(async (receipt) => { structuredClone(receipt) })
    mocks.runtime.freezeSubmissionSnapshot.mockImplementation(async (intent) => ({ ...structuredClone(intent), processSnapshot: {
      client_instance_id: mocks.runtime.clientInstanceId, event_boundary: 8, events: [],
      checkpoints: [{ checkpoint_id: 'checkpoint-one' }],
    } }))
    mocks.runtime.acknowledgeSubmission.mockResolvedValue(undefined)
    mocks.runtime.prepareNavigation.mockResolvedValue({ event_id: '00000000-0000-4000-8000-000000000060' })
    mocks.runtime.freezeNavigationSnapshot.mockImplementation(async (intent) => ({ ...structuredClone(intent), processSnapshot: {
      client_instance_id: mocks.runtime.clientInstanceId, event_boundary: 8, events: [],
      checkpoints: [{ checkpoint_id: 'navigation-checkpoint' }],
    } }))
    mocks.runtime.acknowledgeNavigation.mockResolvedValue(undefined)
    mocks.runtime.saveNavigationIntent.mockResolvedValue(undefined)
    mocks.push.mockResolvedValue(undefined)
    document.querySelector('[data-runtime-finish-form]')?.remove()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('does not mount or poll disabled tools across 29 seconds in a confidence-only question', async () => {
    vi.useFakeTimers()
    mocks.api.question.mockResolvedValue(captureV2Question({ remaining_seconds: 600,
      pending_confidence: confidenceOnlyContract,
      research_tools: { doubt_enabled: false, check_enabled: false, hints: [], rules: [] },
    }))
    const wrapper = mount(StudentAssessment)
    try {
      await flushPromises()
      expect(wrapper.findComponent({ name: 'StudentResearchTools' }).exists()).toBe(false)
      await vi.advanceTimersByTimeAsync(29_000)
      await flushPromises()
      expect(wrapper.findComponent({ name: 'StudentResearchTools' }).exists()).toBe(false)
      expect(wrapper.find('[aria-label="题目工具"]').exists()).toBe(false)
      expect(mocks.api.post.mock.calls.filter(call => /\/(tools|resolve)\//.test(String(call[0])))).toHaveLength(0)
      expect(mocks.runtime.record.mock.calls.filter(call => String(call[0]).includes('research_tool'))).toHaveLength(0)
      await wrapper.get('[data-submit-request]').trigger('click')
      await flushPromises()
      expect(wrapper.find('[data-confidence-panel]').exists()).toBe(true)
    } finally { wrapper.unmount() }
  })

  it('keeps skipped confidence bound to the final answer and ignores duplicate confirmation clicks', async () => {
    const captured = captureV2Question({ pending_confidence: confidenceOnlyContract })
    captured.process.active_response_event_id = '00000000-0000-4000-8000-000000000050'
    Object.assign(captured.process, { atomic_submission: { version: 1 } })
    mocks.api.question.mockResolvedValue(captured)
    mocks.api.post.mockResolvedValue({ success: true, client_instance_id: mocks.runtime.clientInstanceId,
      lease_token: '00000000-0000-4000-8000-000000000099', expires_at: '2099-01-01T00:00:00Z' })
    let commit!: (value: unknown) => void
    mocks.api.submit.mockImplementation(() => new Promise(resolve => { commit = resolve }))
    const wrapper = mount(StudentAssessment)
    try {
      await flushPromises()
      await wrapper.get('[data-submit-request]').trigger('click')
      await flushPromises()
      const skip = wrapper.findAll('button').find(button => button.text() === '跳过评价')!
      await Promise.all([skip.trigger('click'), skip.trigger('click')])
      await flushPromises()
      const confirm = wrapper.get('[data-submit-confirm]')
      await Promise.all([confirm.trigger('click'), confirm.trigger('click')])
      await flushPromises()
      expect(mocks.api.submit).toHaveBeenCalledOnce()
      expect(mocks.api.submit.mock.calls[0]?.[5]).toEqual({ status: 'skipped', answer_event_id: captured.process.active_response_event_id })
      expect(mocks.api.post.mock.calls.some(call => String(call[0]).endsWith('/skip/'))).toBe(false)
      commit({ success: true, status: 'submitted', committed: true, next_url: '/student/sessions/7/questions/12/',
        final_response_event_id: captured.process.active_response_event_id, response_version: 1, acknowledgements: [], checkpoint_ids: ['checkpoint-one'] })
      await flushPromises()
      expect(mocks.push).toHaveBeenCalledWith({ path: '/student/sessions/7/questions/12/', query: { reason: 'submit_next' } })
    } finally { wrapper.unmount() }
  })

  it.each(['recovered acknowledgement', 'last question completion'])('preserves filled final-answer confidence through %s', async mode => {
    const captured = captureV2Question({ pending_confidence: confidenceOnlyContract })
    captured.process.active_response_event_id = '00000000-0000-4000-8000-000000000050'
    Object.assign(captured.process, { atomic_submission: { version: 1 } })
    mocks.api.question.mockResolvedValue(captured)
    mocks.api.post.mockResolvedValue({ success: true, client_instance_id: mocks.runtime.clientInstanceId,
      lease_token: '00000000-0000-4000-8000-000000000099', expires_at: '2099-01-01T00:00:00Z' })
    const result = { success: true, status: 'submitted', committed: true,
      next_url: mode === 'last question completion' ? '/student/sessions/7/summary/' : '/student/sessions/7/questions/12/',
      final_response_event_id: captured.process.active_response_event_id, response_version: 1,
      acknowledgements: [], checkpoint_ids: ['checkpoint-one'] }
    if (mode === 'recovered acknowledgement') {
      mocks.api.submit.mockRejectedValueOnce(new TypeError('network lost after commit'))
      mocks.api.recover.mockResolvedValue({ status: 'submitted', submission_matches: true, can_continue: false, result })
    } else mocks.api.submit.mockResolvedValue(result)
    const originalLocation = globalThis.location
    const assign = vi.fn()
    if (mode === 'last question completion') vi.stubGlobal('location', { assign })
    const wrapper = mount(StudentAssessment)
    try {
      await flushPromises()
      await wrapper.get('[data-submit-request]').trigger('click')
      await flushPromises()
      await wrapper.get('[data-confidence-number]').setValue('57')
      await wrapper.get('[data-confidence-submit]').trigger('click')
      await flushPromises()
      await wrapper.get('[data-submit-confirm]').trigger('click')
      await flushPromises()
      expect(mocks.api.submit).toHaveBeenCalledOnce()
      expect(mocks.api.submit.mock.calls[0]?.[5]).toEqual({ status: 'committed', probability: 57,
        answer_event_id: captured.process.active_response_event_id })
      if (mode === 'last question completion') {
        expect(assign).toHaveBeenCalledWith(result.next_url)
        expect(mocks.push).not.toHaveBeenCalled()
      } else {
        expect(mocks.api.recover).toHaveBeenCalledWith(13, expect.any(String))
        expect(mocks.push).toHaveBeenCalledWith({ path: result.next_url, query: { reason: 'submit_next' } })
      }
    } finally {
      wrapper.unmount()
      if (mode === 'last question completion') vi.stubGlobal('location', originalLocation)
    }
  })

  it('blocks submit, skip, finish and navigation while a research tool is awaiting confirmation', async () => {
    const dto = captureV2Question()
    mocks.api.question.mockResolvedValue({ ...dto, actions: { ...dto.actions, research_tools_url: '/tools/' }, research_tools: { doubt_enabled: true, hints: [], rules: [] } })
    const wrapper = mount(StudentAssessment)
    try {
      await flushPromises()
      const tools = wrapper.findComponent({ name: 'StudentResearchTools' })
      tools.vm.$emit('pending', true)
      await flushPromises()
      for (const selector of ['[data-submit-request]', '[data-skip-request]', '[data-finish-request]']) expect(wrapper.get(selector).attributes('disabled')).toBeDefined()
      expect(wrapper.get('[data-nav-assignment="12"]').attributes('aria-disabled')).toBe('true')
      tools.vm.$emit('pending', false)
      await flushPromises()
      expect(wrapper.get('[data-submit-request]').attributes('disabled')).toBeUndefined()
    } finally { wrapper.unmount() }
  })

  it('keeps initial automatic tools and route guards closed through the presentation persistence gap', async () => {
    const dto = captureV2Question()
    mocks.api.question.mockResolvedValue({ ...dto, actions: { ...dto.actions, research_tools_url: '/tools/' },
      research_tools: { content_version: 'v1', hints: [], rules: [{ id: 'initial-check', tool: 'check', required: true }] } })
    let requested!: (value: unknown) => void
    let presented!: (value: unknown) => void
    let observed!: (value: unknown) => void
    mocks.api.post.mockImplementation((_url, body) => new Promise(resolve => {
      if (body.stage === 'requested') requested = resolve
      else presented = resolve
    }))
    mocks.runtime.record.mockImplementation((type) => type === 'research_tool_observed'
      ? new Promise(resolve => { observed = resolve }) : Promise.resolve({}))
    const wrapper = mount(StudentAssessment)
    try {
      await flushPromises()
      expect(wrapper.get('[name="answer"]').attributes('disabled')).toBeUndefined()
      expect(wrapper.get('[data-nav-assignment="12"]').attributes('aria-disabled')).toBe('true')
      requested({ event_id: 'requested', content_version: 'v1' })
      await flushPromises()
      expect(wrapper.get('[data-nav-assignment="12"]').attributes('aria-disabled')).toBe('true')
      await expect(mocks.beforeRouteUpdate!({ params: { sessionId: '7', assignmentId: '11' }, query: {} })).resolves.toBeUndefined()
      await expect(mocks.beforeRouteUpdate!({ params: { sessionId: '7', assignmentId: '12' }, query: {} })).resolves.toBe(false)
      await expect(mocks.beforeRouteLeave!()).resolves.toBe(false)
      expect(mocks.createProcessRuntime).toHaveBeenCalledOnce()
      observed({})
      await flushPromises()
      expect(wrapper.get('[data-submit-request]').attributes('disabled')).toBeDefined()
      presented({ event_id: 'presented', content_version: 'v1' })
      await flushPromises()
      expect(wrapper.get('[data-nav-assignment="12"]').attributes('aria-disabled')).toBeUndefined()
    } finally { wrapper.unmount() }
  })

  it('keeps an already-open submission confirmation disabled until a pending tool resolves', async () => {
    const dto = captureV2Question()
    mocks.api.question.mockResolvedValue({ ...dto, actions: { ...dto.actions, research_tools_url: '/tools/' }, research_tools: { doubt_enabled: true, hints: [], rules: [] } })
    const wrapper = mount(StudentAssessment)
    try {
      await flushPromises()
      await wrapper.get('[data-submit-request]').trigger('click')
      await flushPromises()
      const tools = wrapper.findComponent({ name: 'StudentResearchTools' })
      tools.vm.$emit('pending', true)
      await flushPromises()
      expect(wrapper.get('[data-submit-confirm]').attributes('disabled')).toBeDefined()
      tools.vm.$emit('pending', false)
      await flushPromises()
      expect(wrapper.get('[data-submit-confirm]').attributes('disabled')).toBeUndefined()
    } finally { wrapper.unmount() }
  })

  it('waits for the controller tool barrier before route departure closes capture', async () => {
    mocks.api.question.mockResolvedValue(captureV2Question())
    const wrapper = mount(StudentAssessment)
    try {
      await flushPromises()
      let release!: () => void
      mocks.runtime.pointerWritesSettled.mockImplementation(() => new Promise<void>(resolve => { release = resolve }))
      const moving = mocks.beforeRouteLeave!()
      await flushPromises()
      expect(mocks.runtime.closeAttempt).not.toHaveBeenCalled()
      release()
      await moving
      expect(mocks.runtime.closeAttempt).toHaveBeenCalledOnce()
    } finally { wrapper.unmount() }
  })

  it('waits for the controller tool barrier before route update loads the new formal entry', async () => {
    mocks.api.question.mockResolvedValue(captureV2Question())
    const wrapper = mount(StudentAssessment)
    try {
      await flushPromises()
      let release!: () => void
      mocks.runtime.pointerWritesSettled.mockImplementationOnce(() => new Promise<void>(resolve => { release = resolve }))
      const moving = mocks.beforeRouteUpdate!({ params: { sessionId: '7', assignmentId: '12' }, query: {} })
      await flushPromises()
      expect(mocks.createProcessRuntime).toHaveBeenCalledOnce()
      expect(mocks.runtime.dispose).not.toHaveBeenCalled()
      release()
      await moving
      expect(mocks.createProcessRuntime).toHaveBeenCalledTimes(2)
    } finally { wrapper.unmount() }
  })

  it('resumes a confirmed leave at its original target before recording source presentation', async () => {
    mocks.pendingEntry.mockResolvedValue({ assignmentId: 12, sourceAssignmentId: 11, targetUrl: '/student/sessions/7/questions/12/', leaveConfirmed: true, navigationId: 'original-entry' })
    const wrapper = mount(StudentAssessment)
    await flushPromises()
    expect(mocks.replace).toHaveBeenCalledWith('/student/sessions/7/questions/12/')
    expect(mocks.api.question).not.toHaveBeenCalled()
    expect(mocks.createProcessRuntime).not.toHaveBeenCalled()
    expect(mocks.runtime.record).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('does not start capture or unlock interaction when an entry replay reports server denial', async () => {
    mocks.api.question.mockResolvedValue(captureV2Question({ entry_allowed: false, entry_denial_code: 'DEADLINE_EXPIRED' }))
    const wrapper = mount(StudentAssessment)
    await flushPromises()
    expect(mocks.createProcessRuntime).not.toHaveBeenCalled()
    expect(mocks.prepareOfflineQuestion).not.toHaveBeenCalled()
    expect(mocks.runtime.record).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('作答时间已截止')
    wrapper.unmount()
  })

  it('recovers a committed submission after denied entry without reopening capture or answer controls', async () => {
    mocks.api.question.mockResolvedValue(captureV2Question({ entry_allowed: false, entry_denial_code: 'ATTEMPT_CLOSED', assignment: { ...questionState.assignment, status: 'submitted' } }))
    mocks.recoverClosedSubmission.mockResolvedValue({ next_url: '/student/sessions/7/questions/12/' })
    const wrapper = mount(StudentAssessment)
    await flushPromises()
    expect(mocks.recoverClosedSubmission).toHaveBeenCalledWith(expect.objectContaining({ session_id: 7, attempt_id: 13 }))
    expect(mocks.confirmEntry).toHaveBeenCalledWith(7, 11)
    expect(mocks.replace).toHaveBeenCalledWith('/student/sessions/7/questions/12/')
    expect(mocks.createProcessRuntime).not.toHaveBeenCalled()
    expect(mocks.runtime.record).not.toHaveBeenCalled()
    expect(mocks.api.submit).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('offers entry retry when closed-submission verification cannot reach the server', async () => {
    mocks.api.question.mockResolvedValue(captureV2Question({ entry_allowed: false, entry_denial_code: 'ATTEMPT_CLOSED' }))
    mocks.recoverClosedSubmission.mockRejectedValue(new Error('原提交结果暂时无法核验'))
    const wrapper = mount(StudentAssessment)
    await flushPromises()
    expect(wrapper.text()).toContain('原提交结果暂时无法核验')
    expect(wrapper.text()).toContain('重试进入原题目')
    expect(mocks.createProcessRuntime).not.toHaveBeenCalled()
    expect(mocks.confirmEntry).not.toHaveBeenCalled()
    expect(mocks.replace).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('ordinary navigation persists one bounded leave envelope without submitting or separate confirmation rounds', async () => {
    const captured = captureV2Question()
    captured.process.active_response_event_id = '00000000-0000-4000-8000-000000000050'
    Object.assign(captured.process, { save_and_leave: { version: 1, max_events: 100, max_checkpoints: 100, max_bytes: 8388608 } })
    mocks.api.question.mockResolvedValue(captured)
    const wrapper = mount(StudentAssessment)
    await flushPromises()
    await wrapper.get('[data-nav-assignment="12"]').trigger('click')
    await flushPromises()
    expect(mocks.runtime.freezeNavigationSnapshot).toHaveBeenCalledOnce()
    expect(mocks.api.saveAndLeave).toHaveBeenCalledWith(captured.actions.save_draft_url, expect.objectContaining({
      navigation_token: '00000000-0000-4000-8000-000000000001',
      leave_event_id: '00000000-0000-4000-8000-000000000060', to_assignment_id: 12,
      target_event_id: captured.process.active_response_event_id,
      save_and_leave: expect.objectContaining({ version: 1, expected_version: 0 }),
    }))
    expect(mocks.runtime.acknowledgeNavigation).toHaveBeenCalledOnce()
    expect(mocks.confirmLeave.mock.invocationCallOrder[0]).toBeGreaterThan(mocks.runtime.acknowledgeNavigation.mock.invocationCallOrder[0]!)
    expect(mocks.confirmLeave.mock.invocationCallOrder[0]).toBeLessThan(mocks.runtime.saveNavigationIntent.mock.invocationCallOrder[0]!)
    expect(mocks.runtime.flushConfirmed).not.toHaveBeenCalled()
    expect(mocks.runtime.closeAttempt).not.toHaveBeenCalled()
    expect(mocks.api.saveDraft).not.toHaveBeenCalled()
    expect(mocks.api.submit).not.toHaveBeenCalled()
    expect(mocks.api.post).not.toHaveBeenCalled()
    expect(mocks.push).toHaveBeenCalledOnce()
    wrapper.unmount()
  })

  it('keeps an unknown navigation result locked and replays the original token and material', async () => {
    const captured = captureV2Question()
    captured.process.active_response_event_id = '00000000-0000-4000-8000-000000000050'
    Object.assign(captured.process, { save_and_leave: { version: 1, max_events: 100, max_checkpoints: 100, max_bytes: 8388608 } })
    mocks.api.question.mockResolvedValue(captured)
    mocks.api.saveAndLeave.mockRejectedValueOnce(new Error('response lost'))
    const wrapper = mount(StudentAssessment)
    await flushPromises()
    await wrapper.get('[data-nav-assignment="12"]').trigger('click')
    await flushPromises()
    expect(mocks.push).not.toHaveBeenCalled()
    expect(wrapper.get('[name=answer]').attributes('disabled')).toBeDefined()
    expect(mocks.runtime.saveNavigationIntent).not.toHaveBeenCalledWith(null)
    await wrapper.get('[data-navigation-retry]').trigger('click')
    await flushPromises()
    expect(mocks.runtime.freezeNavigationSnapshot).toHaveBeenCalledOnce()
    expect(mocks.api.saveAndLeave.mock.calls[1]).toEqual(mocks.api.saveAndLeave.mock.calls[0])
    expect(mocks.runtime.saveNavigationIntent).toHaveBeenCalledWith(null)
    expect(mocks.push).toHaveBeenCalledOnce()
    wrapper.unmount()
  })

  it('subsumes queued drafts in route navigation while preserving every local transition', async () => {
    const captured = captureV2Question()
    Object.assign(captured.process, { save_and_leave: { version: 1 } })
    mocks.api.question.mockResolvedValue(captured)
    let release!: () => void
    let queue: Promise<unknown> = new Promise<void>(resolve => { release = resolve })
    mocks.runtime.serializePointerWrite.mockImplementation(operation => { const pending = queue.then(operation); queue = pending.catch(() => undefined); return pending })
    mocks.runtime.pointerWritesSettled.mockImplementation(() => queue)
    mocks.api.post.mockResolvedValue({ success: true, client_instance_id: mocks.runtime.clientInstanceId, lease_token: '00000000-0000-4000-8000-000000000099', expires_at: '2099-01-01T00:00:00Z' })
    const wrapper = mount(StudentAssessment)
    try {
      await flushPromises()
      await wrapper.get('input[value="A"]').setValue(true)
      await wrapper.get('input[value="B"]').setValue(true)
      await flushPromises()
      const navigating = mocks.beforeRouteUpdate!({ params: { sessionId: '7', assignmentId: '12' }, query: {} })
      await flushPromises()
      release()
      await navigating
      expect(mocks.api.saveDraft).not.toHaveBeenCalled()
      expect(mocks.runtime.record.mock.calls.filter(call => call[0] === 'response_transition')).toHaveLength(2)
      expect(mocks.api.saveAndLeave).toHaveBeenCalledOnce()
    } finally { wrapper.unmount() }
  })

  it('restores an uncertain navigation snapshot and replays it even after the capability is disabled', async () => {
    const captured = captureV2Question()
    const restored = { token: 'restored-navigation', clickedAt: '2026-10-11T14:00:00Z',
      toAssignmentId: 12, leaveEventId: 'left-event', head: 'old-head', expectedResponseVersion: 2,
      writerLeaseToken: 'old-lease', writerClientInstanceId: 'old-client',
      processSnapshot: { client_instance_id: 'old-client', event_boundary: 3, events: [], checkpoints: [] } }
    mocks.api.question.mockResolvedValue(captured)
    mocks.createProcessRuntime.mockResolvedValue({ ...mocks.runtime, restoredNavigation: restored })
    const wrapper = mount(StudentAssessment)
    await flushPromises()
    expect(wrapper.get('[name=answer]').attributes('disabled')).toBeDefined()
    expect(await mocks.beforeRouteLeave!()).toBe(false)
    await wrapper.get('[data-navigation-retry]').trigger('click')
    await flushPromises()
    expect(mocks.runtime.freezeNavigationSnapshot).not.toHaveBeenCalled()
    expect(mocks.api.saveAndLeave).toHaveBeenCalledWith(captured.actions.save_draft_url, expect.objectContaining({
      navigation_token: restored.token, target_event_id: restored.head, writer_lease_token: 'old-lease',
      save_and_leave: expect.objectContaining({ client_instance_id: 'old-client', expected_version: 2 }),
    }))
    expect(mocks.push).toHaveBeenCalledOnce()
    wrapper.unmount()
  })

  it('does not clear an immutable navigation on an unconfirmed rejection', async () => {
    const captured = captureV2Question()
    Object.assign(captured.process, { save_and_leave: { version: 1 } })
    mocks.api.question.mockResolvedValue(captured)
    mocks.api.saveAndLeave.mockRejectedValue(new ApiError(409, { error: { code: 'navigation_material_conflict' } }))
    const wrapper = mount(StudentAssessment)
    await flushPromises()
    await wrapper.get('[data-nav-assignment="12"]').trigger('click')
    await flushPromises()
    expect(mocks.runtime.saveNavigationIntent).not.toHaveBeenCalledWith(null)
    expect(wrapper.find('[data-navigation-retry]').exists()).toBe(true)
    expect(wrapper.get('[data-submit-request]').attributes('disabled')).toBeDefined()
    expect(mocks.push).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('uses a durable atomic snapshot and confirms its receipt before closing', async () => {
    const captured = captureV2Question()
    captured.process.active_response_event_id = '00000000-0000-4000-8000-000000000050'
    Object.assign(captured.process, { atomic_submission: { version: 1, max_events: 100, max_checkpoints: 100, max_bytes: 8388608 } })
    mocks.api.question.mockResolvedValue(captured)
    mocks.api.post.mockResolvedValue({ success: true, client_instance_id: mocks.runtime.clientInstanceId,
      lease_token: '00000000-0000-4000-8000-000000000099', expires_at: '2099-01-01T00:00:00Z' })
    const receipt = { success: true, status: 'submitted', next_url: '/student/sessions/7/questions/12/', confidence_required: false,
      committed: true, acknowledgements: [], checkpoint_ids: ['checkpoint-one'], response_version: 1,
      final_response_event_id: captured.process.active_response_event_id }
    mocks.api.submit.mockResolvedValue(receipt)
    const wrapper = mount(StudentAssessment)
    await flushPromises()
    await wrapper.get('[data-submit-request]').trigger('click')
    await flushPromises()
    await wrapper.get('[data-submit-confirm]').trigger('click')
    await flushPromises()
    expect(mocks.runtime.freezeSubmissionSnapshot).toHaveBeenCalledOnce()
    expect(mocks.api.saveDraft).not.toHaveBeenCalled()
    expect(mocks.runtime.checkpoint).not.toHaveBeenCalledWith('commit')
    expect(mocks.api.submit.mock.calls[0]?.[6]).toEqual({ version: 1, expected_version: 0,
      client_instance_id: mocks.runtime.clientInstanceId, event_boundary: 8, events: [], checkpoints: [{ checkpoint_id: 'checkpoint-one' }] })
    expect(mocks.runtime.acknowledgeSubmission).toHaveBeenCalledWith(expect.objectContaining({ event_boundary: 8 }), receipt)
    expect(mocks.runtime.acknowledgeSubmission.mock.invocationCallOrder[0]).toBeLessThan(mocks.runtime.closeAttempt.mock.invocationCallOrder[0]!)
    expect(mocks.runtime.flushBounded).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('does not flush again after a navigation has durably closed its runtime', async () => {
    const wrapper = mount(StudentAssessment)
    await flushPromises()
    await wrapper.get('[data-nav-assignment="12"]').trigger('click')
    await flushPromises()
    expect(mocks.runtime.closeAttempt).toHaveBeenCalledOnce()
    expect(mocks.runtime.flushBounded).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('recovers a lost atomic response using the committed receipt before removing local evidence', async () => {
    const captured = captureV2Question()
    captured.process.active_response_event_id = '00000000-0000-4000-8000-000000000050'
    Object.assign(captured.process, { atomic_submission: { version: 1 } })
    mocks.api.question.mockResolvedValue(captured)
    mocks.api.post.mockResolvedValue({ success: true, client_instance_id: mocks.runtime.clientInstanceId,
      lease_token: '00000000-0000-4000-8000-000000000099', expires_at: '2099-01-01T00:00:00Z' })
    mocks.api.submit.mockRejectedValueOnce(new TypeError('response lost'))
    const receipt = { success: true, status: 'submitted', next_url: '/student/sessions/7/questions/12/',
      confidence_required: false, committed: true, acknowledgements: [], checkpoint_ids: ['checkpoint-one'] }
    mocks.api.recover.mockResolvedValue({ result: receipt, can_continue: false })
    const wrapper = mount(StudentAssessment)
    await flushPromises()
    await wrapper.get('[data-submit-request]').trigger('click')
    await flushPromises()
    await wrapper.get('[data-submit-confirm]').trigger('click')
    await flushPromises()
    expect(mocks.api.submit).toHaveBeenCalledOnce()
    expect(mocks.runtime.acknowledgeSubmission).toHaveBeenCalledWith(expect.objectContaining({ event_boundary: 8 }), receipt)
    expect(mocks.runtime.acknowledgeSubmission.mock.invocationCallOrder[0]).toBeLessThan(mocks.runtime.saveSubmissionIntent.mock.invocationCallOrder[0]!)
    expect(mocks.runtime.saveSubmissionIntent).toHaveBeenLastCalledWith(null)
    wrapper.unmount()
  })

  it('reuses the persisted atomic envelope after reload even when the capability is no longer advertised', async () => {
    const captured = captureV2Question()
    captured.process.active_response_event_id = '00000000-0000-4000-8000-000000000050'
    mocks.api.question.mockResolvedValue(captured)
    mocks.api.post.mockResolvedValue({ success: true, client_instance_id: mocks.runtime.clientInstanceId,
      lease_token: '00000000-0000-4000-8000-000000000099', expires_at: '2099-01-01T00:00:00Z' })
    const snapshot = { client_instance_id: mocks.runtime.clientInstanceId, event_boundary: 8,
      events: [], checkpoints: [{ checkpoint_id: 'checkpoint-original' }] }
    mocks.createProcessRuntime.mockResolvedValueOnce({ ...mocks.runtime, restoredSubmission: {
      token: '11111111-1111-4111-8111-111111111111', clickedAt: '2026-10-11T00:00:00Z',
      answer: JSON.stringify('B'), head: captured.process.active_response_event_id,
      expectedResponseVersion: 0, processSnapshot: snapshot,
    } })
    mocks.api.submit.mockResolvedValue({ success: true, status: 'submitted', next_url: '/student/sessions/7/questions/12/',
      confidence_required: false, committed: true, acknowledgements: [], checkpoint_ids: ['checkpoint-original'] })
    const wrapper = mount(StudentAssessment)
    await flushPromises()
    await wrapper.get('[data-submit-confirm]').trigger('click')
    await flushPromises()
    expect(mocks.runtime.freezeSubmissionSnapshot).not.toHaveBeenCalled()
    expect(mocks.api.saveDraft).not.toHaveBeenCalled()
    expect(mocks.runtime.record.mock.calls.filter(([type]) => type === 'response_commit_requested')).toHaveLength(0)
    expect(mocks.api.submit.mock.calls[0]?.[1]).toBe('11111111-1111-4111-8111-111111111111')
    expect(mocks.api.submit.mock.calls[0]?.[6]).toEqual({ ...snapshot, version: 1, expected_version: 0 })
    wrapper.unmount()
  })

  it('retains the committed answer when navigation fails after atomic submission', async () => {
    const captured = captureV2Question()
    captured.process.active_response_event_id = '00000000-0000-4000-8000-000000000050'
    Object.assign(captured.process, { atomic_submission: { version: 1 } })
    mocks.api.question.mockResolvedValue(captured)
    mocks.api.post.mockResolvedValue({ success: true, client_instance_id: mocks.runtime.clientInstanceId,
      lease_token: '00000000-0000-4000-8000-000000000099', expires_at: '2099-01-01T00:00:00Z' })
    mocks.push.mockRejectedValueOnce(new Error('navigation unavailable'))
    mocks.api.submit.mockResolvedValue({ success: true, status: 'submitted', next_url: '/student/sessions/7/questions/12/',
      confidence_required: false, committed: true, acknowledgements: [], checkpoint_ids: ['checkpoint-one'] })
    const wrapper = mount(StudentAssessment)
    await flushPromises()
    await wrapper.get('[data-submit-request]').trigger('click')
    await flushPromises()
    await wrapper.get('[data-submit-confirm]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('上一题已提交，下一题加载失败')
    expect(mocks.api.recover).not.toHaveBeenCalled()
    expect(mocks.api.submit).toHaveBeenCalledOnce()
    expect(mocks.runtime.saveSubmissionIntent).toHaveBeenLastCalledWith(null)
    wrapper.unmount()
  })

  it('keeps unconfirmed close evidence on the current question', async () => {
    mocks.runtime.closeAttempt.mockResolvedValueOnce(false)
    const wrapper = mount(StudentAssessment)
    await flushPromises()
    await wrapper.get('[data-nav-assignment="12"]').trigger('click')
    await flushPromises()
    expect(mocks.push).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('作答轨迹尚未确认保存')
    wrapper.unmount()
  })

  it('retries a durably stored submitted close without submitting or showing feedback twice', async () => {
    mocks.runtime.closeAttempt.mockResolvedValueOnce(false).mockResolvedValue(true)
    const wrapper = mount(StudentAssessment)
    await flushPromises()
    await wrapper.findAll<HTMLInputElement>('input[name="answer"]')[1]!.setValue(true)
    await wrapper.get('[data-submit-request]').trigger('click')
    await flushPromises()
    await wrapper.get('[data-submit-confirm]').trigger('click')
    await flushPromises()
    expect(mocks.push).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('本题已提交，收尾轨迹尚未确认')
    await wrapper.get('[data-retry-close]').trigger('click')
    await flushPromises()
    expect(mocks.api.submit).toHaveBeenCalledOnce()
    expect(mocks.runtime.closeAttempt).toHaveBeenCalledTimes(2)
    expect(mocks.push).toHaveBeenCalledOnce()
    wrapper.unmount()
  })

  it('does not independently release a lease while a bundled close is unconfirmed on page hide', async () => {
    mocks.api.question.mockResolvedValue(captureV2Question())
    mocks.api.post.mockResolvedValue({ success: true, client_instance_id: mocks.runtime.clientInstanceId,
      lease_token: '00000000-0000-4000-8000-000000000099', expires_at: '2099-01-01T00:00:00Z' })
    mocks.runtime.closesWriterLease = true
    mocks.runtime.closeAttempt.mockResolvedValueOnce(false)
    const release = vi.spyOn(mocks.runtime.writerLease!, 'release')
    const wrapper = mount(StudentAssessment)
    await flushPromises()
    await wrapper.findAll<HTMLInputElement>('input[name="answer"]')[1]!.setValue(true)
    await wrapper.get('[data-submit-request]').trigger('click')
    await flushPromises()
    await wrapper.get('[data-submit-confirm]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-retry-close]').exists()).toBe(true)
    window.dispatchEvent(new Event('pagehide'))
    await flushPromises()
    expect(release).not.toHaveBeenCalled()
    wrapper.unmount()
    release.mockRestore()
  })

  it('bounds the entire submitted close even when pointer recovery is still waiting', async () => {
    vi.useFakeTimers()
    const wrapper = mount(StudentAssessment)
    await flushPromises()
    mocks.api.submit.mockImplementation(async () => {
      mocks.runtime.pointerWritesSettled.mockImplementation(() => new Promise(() => {}))
      return { success: true, status: 'submitted', confidence_required: false, next_url: '/student/sessions/7/questions/12/' }
    })
    await wrapper.findAll<HTMLInputElement>('input[name="answer"]')[1]!.setValue(true)
    await wrapper.get('[data-submit-request]').trigger('click')
    await flushPromises()
    await wrapper.get('[data-submit-confirm]').trigger('click')
    await flushPromises()
    await vi.advanceTimersByTimeAsync(1500)
    await flushPromises()
    expect(wrapper.text()).toContain('本题已提交，收尾轨迹尚未确认')
    expect(wrapper.get('[data-retry-close]').attributes('disabled')).toBeUndefined()
    expect(mocks.push).not.toHaveBeenCalled()
    wrapper.unmount()
    vi.useRealTimers()
  })

  it('includes submission feedback in the closing evidence before entering another attempt', async () => {
    const wrapper = mount(StudentAssessment)
    await flushPromises()
    await wrapper.findAll<HTMLInputElement>('input[name="answer"]')[1]!.setValue(true)
    await wrapper.get('[data-submit-request]').trigger('click')
    await wrapper.get('[data-submit-confirm]').trigger('click')
    await flushPromises()
    const feedbackIndex = mocks.runtime.record.mock.calls.findIndex(([type]) => type === 'feedback_shown')
    expect(feedbackIndex).toBeGreaterThanOrEqual(0)
    expect(mocks.runtime.record.mock.invocationCallOrder[feedbackIndex]).toBeLessThan(mocks.runtime.closeAttempt.mock.invocationCallOrder[0]!)
    expect(mocks.push).toHaveBeenCalled()
    wrapper.unmount()
  })

  it('records initialization stages without recording answer or identity data', async () => {
    const measure = vi.fn()
    const original = Object.getOwnPropertyDescriptor(performance, 'measure')
    Object.defineProperty(performance, 'measure', { configurable: true, value: measure })
    try {
      const wrapper = mount(StudentAssessment)
      await flushPromises()
      expect(measure).toHaveBeenCalledWith('kt-assessment:question_entry', expect.objectContaining({
        detail: expect.objectContaining({ operation_id: expect.any(String), stage: 'question_entry', outcome: 'success' }),
      }))
      const serialized = JSON.stringify(measure.mock.calls)
      expect(serialized).not.toContain('2 + 2')
      expect(serialized).not.toContain('assignment_id')
      wrapper.unmount()
    } finally {
      if (original) Object.defineProperty(performance, 'measure', original)
      else Reflect.deleteProperty(performance, 'measure')
    }
  })

  it('renders an authoritative focus view without correctness state', async () => {
    const wrapper = mount(StudentAssessment)
    await flushPromises()

    expect(mocks.api.question).toHaveBeenCalledWith(7, 11, 'navigation_click', expect.any(Object))
    expect(wrapper.get('h1').text()).toBe('第 1/2 题')
    expect(wrapper.text()).toContain('反馈策略实验')
    expect(wrapper.text()).toContain('2 + 2 等于多少？')
    expect(wrapper.get('[aria-current="step"]').text()).toContain('第 1 题')
    expect(wrapper.findAll('input[name="answer"]')).toHaveLength(2)
    expect(wrapper.get('[role="timer"]').text()).toContain('10:00')
    expect(wrapper.text()).not.toContain('回答正确')
    expect(wrapper.text()).not.toContain('回答错误')
  })

  it.each([
    ['single_choice', '单选题'],
    ['multiple_choice', '多选题'],
    ['numeric', '数值题'],
    ['short_answer', '简答题'],
    ['fill_blank', '填空题'],
    ['true_false', '判断题'],
  ])('shows the %s label instead of the internal question code', async (questionType, expectedLabel) => {
    mocks.api.question.mockResolvedValue({
      ...structuredClone(questionState),
      question: { ...structuredClone(questionState.question), question_type: questionType },
    })
    const wrapper = mount(StudentAssessment)
    await flushPromises()

    const context = wrapper.get('.kt-assessment-context').text()
    expect(context).toContain(`题型：${expectedLabel}`)
    expect(context).toContain('主 KC：基础运算')
    expect(context).not.toContain('C000001-Q000001')
    wrapper.unmount()
  })

  it('places the answer before the number card and moving the drawer does not collect an answer', async () => {
    const wrapper = mount(StudentAssessment)
    await flushPromises()
    expect(wrapper.get('.kt-assessment-topbar').text()).toContain('反馈策略实验')
    const question = wrapper.get('.kt-assessment-question').element
    const navigation = wrapper.get('.kt-assessment-nav').element
    expect(question.compareDocumentPosition(navigation) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(wrapper.get('[data-nav-assignment="11"]').attributes('data-answer-status')).toBe('visited')
    mocks.runtime.record.mockClear()
    await wrapper.get('[data-mobile-navigation-trigger]').trigger('click')
    await wrapper.get('[role="dialog"] button').trigger('click')
    expect(mocks.runtime.record).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('shows a local response on the number card without claiming formal submission', async () => {
    mocks.api.question.mockResolvedValue({ ...structuredClone(questionState), question: { ...questionState.question, question_type: 'multiple_choice' } })
    const wrapper = mount(StudentAssessment)
    await flushPromises()
    const item = wrapper.get('[data-nav-assignment="11"]')
    expect(item.attributes('data-has-response')).toBe('false')
    await wrapper.findAll<HTMLInputElement>('input[name="answer"]')[0]!.setValue(true)
    expect(item.attributes('data-has-response')).toBe('true')
    expect(item.text()).toContain('待提交')
    expect(item.attributes('data-answer-status')).toBe('visited')
    await wrapper.findAll<HTMLInputElement>('input[name="answer"]')[0]!.setValue(false)
    expect(item.attributes('data-has-response')).toBe('false')
    expect(mocks.api.submit).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('exposes N/M progress and an accessible question drawer for mobile navigation', async () => {
    const wrapper = mount(StudentAssessment)
    await flushPromises()

    const trigger = wrapper.get('[data-mobile-navigation-trigger]')
    expect(wrapper.get('[data-mobile-assessment-header]').text()).toContain('第 1/2 题')
    expect(trigger.attributes('aria-expanded')).toBe('false')
    expect(wrapper.find('[role="dialog"][aria-label="题目导航"]').exists()).toBe(false)

    await trigger.trigger('click')

    expect(trigger.attributes('aria-expanded')).toBe('true')
    const drawer = wrapper.get('[role="dialog"][aria-label="题目导航"]')
    expect(drawer.text()).toContain('第 1 题')
    expect(drawer.text()).toContain('未访问')

    await drawer.get('button').trigger('click')

    expect(wrapper.find('[role="dialog"][aria-label="题目导航"]').exists()).toBe(false)
    expect(trigger.attributes('aria-expanded')).toBe('false')
  })

  it('restores and autosaves a choice without creating a formal submission', async () => {
    mocks.api.question.mockResolvedValue({ ...structuredClone(questionState), draft_answer: 'B' })
    const wrapper = mount(StudentAssessment)
    await flushPromises()

    const answers = wrapper.findAll<HTMLInputElement>('input[name="answer"]')
    expect(answers[1]?.element.checked).toBe(true)

    await answers[0]?.setValue(true)
    await flushPromises()

    expect(mocks.api.saveDraft).toHaveBeenCalledOnce()
    expect(mocks.api.saveDraft.mock.calls[0]?.slice(0, 2)).toEqual([
      '/student/attempts/13/draft/',
      'A',
    ])
    expect(mocks.api.submit).not.toHaveBeenCalled()
    expect(wrapper.get('[data-save-status]').text()).toBe('草稿已保存')
    expect(mocks.runtime.record).toHaveBeenCalledWith(
      'response_state_changed',
      {
        question_type: 'single_choice',
        response_state_before: null,
        response_state_after: 'A',
        change_source: 'student',
      },
      {},
    )
  })

  it('saves a return to A while B is still in flight', async () => {
    const wrapper = mount(StudentAssessment)
    await flushPromises()
    const answers = wrapper.findAll<HTMLInputElement>('input[name="answer"]')
    await answers[0]!.setValue(true)
    await flushPromises()
    let complete!: (value: unknown) => void
    mocks.api.saveDraft.mockImplementationOnce(() => new Promise((resolve) => { complete = resolve }))
    await answers[1]!.setValue(true)
    await flushPromises()
    await answers[0]!.setValue(true)
    complete({ success: true })
    await flushPromises()
    expect(mocks.api.saveDraft.mock.calls.map((call) => call[1])).toEqual(['A', 'B', 'A'])
    wrapper.unmount()
  })

  it('records the durable server snapshot transition for a short answer autosave', async () => {
    vi.useFakeTimers()
    const priorState = {
      snapshot_id: '10000000-0000-4000-8000-000000000001',
      state_hash: 'prior-state',
      content_hash: 'prior-content',
      content_length: 2,
      schema_version: 1,
    }
    mocks.api.question.mockResolvedValue({
      ...structuredClone(questionState),
      question: {
        ...questionState.question,
        question_type: 'short_answer',
        options: [],
      },
      draft_answer: '旧答',
      initial_response_state: priorState,
    })
    mocks.api.saveDraft.mockResolvedValue({
      success: true,
      saved_at: '2026-08-30T08:00:00Z',
      response_snapshot_id: '20000000-0000-4000-8000-000000000002',
      state_hash: 'next-state',
      content_hash: 'next-content',
      content_length: 4,
    })
    const wrapper = mount(StudentAssessment)
    await flushPromises()

    await wrapper.get('textarea[name="answer"]').setValue('知识追踪')
    await vi.advanceTimersByTimeAsync(800)
    await flushPromises()

    expect(mocks.runtime.record).toHaveBeenCalledWith(
      'response_state_changed',
      {
        question_type: 'short_answer',
        response_state_before: priorState,
        response_state_after: {
          snapshot_id: '20000000-0000-4000-8000-000000000002',
          state_hash: 'next-state',
          content_hash: 'next-content',
          content_length: 4,
          schema_version: 1,
        },
        change_source: 'student',
      },
      {},
    )
    expect(mocks.runtime.record).toHaveBeenCalledWith(
      'text_edit_observed',
      expect.objectContaining({
        question_type: 'short_answer',
        content_length_before: 2,
        content_length_after: 4,
        content_hash_before: '0'.repeat(64),
        content_hash_after: '0'.repeat(64),
      }),
      {},
    )
  })

  it('uses an accessible confirmation and preserves the idempotent submission flow', async () => {
    const wrapper = mount(StudentAssessment)
    await flushPromises()
    await wrapper.findAll<HTMLInputElement>('input[name="answer"]')[1]?.setValue(true)
    await flushPromises()

    await wrapper.get('[data-submit-request]').trigger('click')
    expect(wrapper.get('[role="dialog"]').attributes('aria-modal')).toBe('true')
    expect(mocks.api.submit).not.toHaveBeenCalled()

    await wrapper.get('[data-submit-confirm]').trigger('click')
    await flushPromises()

    expect(mocks.runtime.record).toHaveBeenCalledWith(
      'response_commit_requested',
      { submission_token: '00000000-0000-4000-8000-000000000001' },
      { correlationId: '00000000-0000-4000-8000-000000000001', wallTime: expect.any(String), monotonicMs: expect.any(Number) },
    )
    expect(mocks.runtime.checkpoint).toHaveBeenCalledWith('commit')
    expect(mocks.api.saveDraft).toHaveBeenCalledOnce()
    expect(mocks.api.saveDraft.mock.invocationCallOrder[0]).toBeLessThan(mocks.api.submit.mock.invocationCallOrder[0]!)
    expect(mocks.runtime.flushConfirmed).toHaveBeenCalled()
    expect(mocks.api.submit.mock.calls[0]?.slice(0, 2)).toEqual([
      '/student/attempts/13/submit/',
      '00000000-0000-4000-8000-000000000001',
    ])
    expect(mocks.runtime.closeAttempt).toHaveBeenCalledWith('submit')
    expect(mocks.push).toHaveBeenCalledWith({
      path: '/student/sessions/7/questions/12/',
      query: { reason: 'submit_next' },
    })
  })

  it('forces a durable save before question navigation', async () => {
    const wrapper = mount(StudentAssessment)
    await flushPromises()

    await wrapper.get('[data-nav-assignment="12"]').trigger('click')
    await flushPromises()

    expect(mocks.api.saveDraft.mock.calls[0]?.slice(0, 2)).toEqual([
      '/student/attempts/13/draft/',
      null,
    ])
    expect(mocks.runtime.closeAttempt).toHaveBeenCalledWith('navigation', 12)
    expect(mocks.push).toHaveBeenCalledWith(expect.objectContaining({
      path: '/student/sessions/7/questions/12/',
      query: expect.objectContaining({ navigation_id: expect.any(String) }),
    }))
  })

  it('does not rewrite a server-confirmed capture answer and target when navigating', async () => {
    const captured = captureV2Question()
    captured.process.active_response_event_id = '00000000-0000-4000-8000-000000000050'
    mocks.api.question.mockResolvedValue(captured)
    mocks.api.post.mockResolvedValue({ success: true, client_instance_id: mocks.runtime.clientInstanceId, lease_token: '00000000-0000-4000-8000-000000000099', expires_at: '2099-01-01T00:00:00Z' })
    const wrapper = mount(StudentAssessment)
    await flushPromises()
    await wrapper.get('[data-nav-assignment="12"]').trigger('click')
    await flushPromises()
    expect(mocks.api.saveDraft).not.toHaveBeenCalled()
    expect(mocks.runtime.closeAttempt).toHaveBeenCalledWith('navigation', 12)
    expect(mocks.runtime.flushBounded).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('locks navigation immediately and coalesces a save already in flight before leaving', async () => {
    let completeSave!: (value: unknown) => void
    mocks.api.saveDraft.mockReturnValueOnce(new Promise(resolve => { completeSave = resolve }))
    const wrapper = mount(StudentAssessment)
    await flushPromises()
    await wrapper.findAll<HTMLInputElement>('input[name="answer"]')[0]!.setValue(true)
    await flushPromises()
    const navigation = wrapper.get('[data-nav-assignment="12"]')
    navigation.element.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    navigation.element.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await flushPromises()
    expect(navigation.attributes('aria-disabled')).toBe('true')
    expect(wrapper.get('[data-submit-request]').attributes('disabled')).toBeDefined()
    expect(wrapper.findAll<HTMLInputElement>('input[name="answer"]').every(input => input.element.disabled)).toBe(true)
    expect(mocks.push).not.toHaveBeenCalled()
    expect(mocks.runtime.closeAttempt).not.toHaveBeenCalled()
    completeSave({ success: true })
    await flushPromises()
    expect(mocks.api.saveDraft).toHaveBeenCalledOnce()
    expect(mocks.runtime.closeAttempt).toHaveBeenCalledOnce()
    expect(mocks.push).toHaveBeenCalledOnce()
    wrapper.unmount()
  })

  it('retries a failed in-flight save before navigation and keeps controls available after failure', async () => {
    mocks.api.saveDraft.mockRejectedValueOnce(new Error('sync failed')).mockRejectedValueOnce(new Error('still failed'))
    const wrapper = mount(StudentAssessment)
    await flushPromises()
    await wrapper.findAll<HTMLInputElement>('input[name="answer"]')[0]!.setValue(true)
    await flushPromises()
    await wrapper.get('[data-nav-assignment="12"]').trigger('click')
    await flushPromises()
    expect(mocks.api.saveDraft).toHaveBeenCalledTimes(2)
    expect(mocks.push).not.toHaveBeenCalled()
    expect(wrapper.get('[data-nav-assignment="12"]').attributes('aria-disabled')).toBeUndefined()
    expect(wrapper.findAll<HTMLInputElement>('input[name="answer"]').every(input => !input.element.disabled)).toBe(true)
    mocks.api.saveDraft.mockResolvedValueOnce({ success: true })
    await wrapper.get('[data-nav-assignment="12"]').trigger('click')
    await flushPromises()
    expect(mocks.api.saveDraft).toHaveBeenCalledTimes(3)
    expect(mocks.push).toHaveBeenCalledOnce()
    wrapper.unmount()
  })

  it('saves the new answer after an edit during an earlier save without marking the new edit confirmed', async () => {
    let completeSave!: (value: unknown) => void
    let completeSecond!: (value: unknown) => void
    mocks.api.saveDraft.mockReturnValueOnce(new Promise(resolve => { completeSave = resolve }))
      .mockReturnValueOnce(new Promise(resolve => { completeSecond = resolve }))
    const wrapper = mount(StudentAssessment)
    await flushPromises()
    const choices = wrapper.findAll<HTMLInputElement>('input[name="answer"]')
    await choices[0]!.setValue(true)
    await flushPromises()
    await choices[1]!.setValue(true)
    completeSave({ success: true })
    await flushPromises()
    expect(mocks.api.saveDraft.mock.calls.map(call => call[1])).toEqual(['A', 'B'])
    expect(wrapper.get('[data-save-status]').text()).not.toBe('草稿已保存')
    await wrapper.get('[data-nav-assignment="12"]').trigger('click')
    completeSecond({ success: true })
    await flushPromises()
    expect(mocks.api.saveDraft).toHaveBeenCalledTimes(2)
    expect(mocks.push).toHaveBeenCalledOnce()
    wrapper.unmount()
  })

  it('keeps every capture transition when an answer returns to server content with a different target', async () => {
    let nextId = 100
    vi.stubGlobal('crypto', { ...globalThis.crypto, randomUUID: () => `00000000-0000-4000-8000-${String(nextId++).padStart(12, '0')}` })
    const captured = captureV2Question()
    captured.process.active_response_event_id = '00000000-0000-4000-8000-000000000050'
    mocks.api.question.mockResolvedValue(captured)
    mocks.api.post.mockResolvedValue({ success: true, client_instance_id: mocks.runtime.clientInstanceId, lease_token: '00000000-0000-4000-8000-000000000099', expires_at: '2099-01-01T00:00:00Z' })
    mocks.api.saveDraft.mockResolvedValueOnce({ success: true, response_version: 1 }).mockResolvedValueOnce({ success: true, response_version: 2 })
    const wrapper = mount(StudentAssessment)
    await flushPromises()
    const choices = wrapper.findAll<HTMLInputElement>('input[name="answer"]')
    await choices[0]!.setValue(true)
    await choices[1]!.setValue(true)
    await flushPromises()
    expect(mocks.api.saveDraft.mock.calls.map(call => call[1])).toEqual(['A', 'B'])
    expect(mocks.api.saveDraft.mock.calls.map(call => call[3].expected_version)).toEqual([0, 1])
    expect(mocks.api.saveDraft.mock.calls[0]![3].target_event_id).not.toBe(mocks.api.saveDraft.mock.calls[1]![3].target_event_id)
    expect(mocks.runtime.record.mock.calls.filter(call => call[0] === 'response_transition')).toHaveLength(2)
    await wrapper.get('[data-nav-assignment="12"]').trigger('click')
    await flushPromises()
    expect(mocks.api.saveDraft).toHaveBeenCalledTimes(2)
    wrapper.unmount()
  })

  it('combines a draft pointer with its locally frozen capture facts without a prior event flush', async () => {
    const captured = captureV2Question()
    mocks.api.question.mockResolvedValue(captured)
    mocks.runtime.captureMutation = vi.fn(async (_url, body, send) => send({ ...body, capture_bundle: { version: 1, events: ['all-local-transitions'], checkpoints: [] } }))
    mocks.api.post.mockResolvedValue({ success: true, client_instance_id: mocks.runtime.clientInstanceId, lease_token: '00000000-0000-4000-8000-000000000099', expires_at: '2099-01-01T00:00:00Z' })
    const wrapper = mount(StudentAssessment)
    await flushPromises()
    mocks.runtime.flushConfirmed.mockClear()
    await wrapper.get('input[value="A"]').setValue(true)
    await flushPromises()
    expect(mocks.runtime.flushConfirmed).not.toHaveBeenCalled()
    expect(mocks.runtime.captureMutation).toHaveBeenCalledOnce()
    expect(mocks.api.saveDraft).toHaveBeenCalledWith(captured.actions.save_draft_url, 'A', expect.any(String), expect.objectContaining({ capture_bundle: expect.objectContaining({ version: 1 }) }))
    wrapper.unmount()
  })

  it('synchronizes a restored local target on reconnect even when its answer equals server content', async () => {
    const captured = captureV2Question()
    captured.process.active_response_event_id = '00000000-0000-4000-8000-000000000050'
    mocks.api.question.mockResolvedValue(captured)
    mocks.createProcessRuntime.mockResolvedValue({ ...mocks.runtime, restoredResponse: {
      event_id: '00000000-0000-4000-8000-000000000051', payload: { after_state: { value: 'B' }, branch_id: 'restored' },
    } })
    mocks.api.post.mockResolvedValue({ success: true, client_instance_id: mocks.runtime.clientInstanceId, lease_token: '00000000-0000-4000-8000-000000000099', expires_at: '2099-01-01T00:00:00Z' })
    const wrapper = mount(StudentAssessment)
    await flushPromises()
    globalThis.dispatchEvent(new Event('offline'))
    mocks.api.question.mockResolvedValue({ ...captured, process: { ...captured.process, response_version: 7 } })
    globalThis.dispatchEvent(new Event('online'))
    await flushPromises()
    expect(mocks.api.saveDraft).toHaveBeenCalledWith(captured.actions.save_draft_url, 'B', expect.any(String), expect.objectContaining({
      target_event_id: '00000000-0000-4000-8000-000000000051', expected_version: 7,
    }))
    wrapper.unmount()
  })

  it('retries an unacknowledged target without promoting a failed draft to server-confirmed', async () => {
    mocks.api.question.mockResolvedValue(captureV2Question())
    mocks.api.post.mockResolvedValue({ success: true, client_instance_id: mocks.runtime.clientInstanceId, lease_token: '00000000-0000-4000-8000-000000000099', expires_at: '2099-01-01T00:00:00Z' })
    mocks.api.saveDraft.mockResolvedValueOnce({ success: true, response_version: 1 })
    mocks.runtime.flushConfirmed.mockResolvedValueOnce(false).mockResolvedValueOnce(true)
    const wrapper = mount(StudentAssessment)
    await flushPromises()
    await wrapper.findAll<HTMLInputElement>('input[name="answer"]')[0]!.setValue(true)
    await flushPromises()
    expect(wrapper.get('[data-save-status]').text()).toBe('草稿保存失败')
    expect(mocks.api.saveDraft).not.toHaveBeenCalled()
    await wrapper.get('[data-nav-assignment="12"]').trigger('click')
    await flushPromises()
    expect(mocks.api.saveDraft).toHaveBeenCalledOnce()
    expect(mocks.api.saveDraft.mock.calls.every(call => call[3].expected_version === 0)).toBe(true)
    expect(mocks.push).toHaveBeenCalledOnce()
    wrapper.unmount()
  })

  it('forces a durable save before marking the current question skipped', async () => {
    const wrapper = mount(StudentAssessment)
    await flushPromises()

    await wrapper.get('[data-skip-request]').trigger('click')
    await flushPromises()

    expect(mocks.api.saveDraft.mock.calls[0]?.slice(0, 2)).toEqual([
      '/student/attempts/13/draft/',
      null,
    ])
    expect(mocks.api.skip).toHaveBeenCalledWith('/student/attempts/13/skip/')
    expect(mocks.runtime.closeAttempt).toHaveBeenCalledWith('skip')
    expect(mocks.push).toHaveBeenCalledWith({
      path: '/student/sessions/7/questions/12/',
      query: { reason: 'skip_next' },
    })
  })

  it('releases the capture-v2 writer lease after closing a navigation', async () => {
    const captured = captureV2Question()
    captured.process.active_response_event_id = '00000000-0000-4000-8000-000000000050'
    mocks.api.question.mockResolvedValue(captured)
    mocks.api.post.mockImplementation(async (url: string) => (
      url.endsWith('/lease/acquire/')
        ? {
            success: true,
            lease_token: '00000000-0000-4000-8000-000000000099',
            client_instance_id: mocks.runtime.clientInstanceId,
            expires_at: '2099-01-01T00:00:00Z',
          }
        : { success: true }
    ))
    const wrapper = mount(StudentAssessment)
    await flushPromises()
    await wrapper.findAll<HTMLInputElement>('input[name="answer"]')[0]!.setValue(true)
    await flushPromises()

    await wrapper.get('[data-nav-assignment="12"]').trigger('click')
    await flushPromises()

    expect(mocks.api.post).toHaveBeenCalledWith(
      captured.process.writer_lease_release_url,
      {
        lease_token: '00000000-0000-4000-8000-000000000099',
        client_instance_id: mocks.runtime.clientInstanceId,
      },
      {},
    )
    const releaseCall = mocks.api.post.mock.calls.findIndex((call) => String(call[0]).endsWith('/lease/release/'))
    expect(mocks.runtime.closeAttempt.mock.invocationCallOrder[0]).toBeLessThan(
      mocks.api.post.mock.invocationCallOrder[releaseCall]!,
    )
    expect(mocks.api.post.mock.invocationCallOrder[releaseCall]).toBeLessThan(
      mocks.push.mock.invocationCallOrder[0]!,
    )
    wrapper.unmount()
  })

  it('releases the capture-v2 writer lease after marking a question skipped', async () => {
    const captured = captureV2Question()
    captured.process.active_response_event_id = '00000000-0000-4000-8000-000000000050'
    mocks.api.question.mockResolvedValue(captured)
    mocks.api.post.mockImplementation(async (url: string) => (
      url.endsWith('/lease/acquire/')
        ? {
            success: true,
            lease_token: '00000000-0000-4000-8000-000000000099',
            client_instance_id: mocks.runtime.clientInstanceId,
            expires_at: '2099-01-01T00:00:00Z',
          }
        : { success: true }
    ))
    const wrapper = mount(StudentAssessment)
    await flushPromises()
    await wrapper.findAll<HTMLInputElement>('input[name="answer"]')[0]!.setValue(true)
    await flushPromises()

    await wrapper.get('[data-skip-request]').trigger('click')
    await flushPromises()

    expect(mocks.api.post).toHaveBeenCalledWith(
      captured.process.writer_lease_release_url,
      expect.objectContaining({
        lease_token: '00000000-0000-4000-8000-000000000099',
      }),
      {},
    )
    wrapper.unmount()
  })

  it('flushes and releases before leaving the assessment module', async () => {
    const captured = captureV2Question()
    captured.process.active_response_event_id = '00000000-0000-4000-8000-000000000050'
    mocks.api.question.mockResolvedValue(captured)
    mocks.api.post.mockImplementation(async (url: string) => (
      url.endsWith('/lease/acquire/')
        ? {
            success: true,
            lease_token: '00000000-0000-4000-8000-000000000099',
            client_instance_id: mocks.runtime.clientInstanceId,
            expires_at: '2099-01-01T00:00:00Z',
          }
        : { success: true }
    ))
    const wrapper = mount(StudentAssessment)
    await flushPromises()
    await wrapper.findAll<HTMLInputElement>('input[name="answer"]')[0]!.setValue(true)
    await flushPromises()

    await mocks.beforeRouteLeave?.()
    await flushPromises()

    expect(mocks.api.saveDraft).toHaveBeenCalled()
    expect(mocks.runtime.closeAttempt).toHaveBeenCalledWith('navigation')
    expect(mocks.api.post).toHaveBeenCalledWith(
      captured.process.writer_lease_release_url,
      expect.any(Object),
      {},
    )
    wrapper.unmount()
  })

  it('uses keepalive to best-effort release on pagehide', async () => {
    const captured = captureV2Question()
    captured.process.active_response_event_id = '00000000-0000-4000-8000-000000000050'
    mocks.api.question.mockResolvedValue(captured)
    mocks.api.post.mockImplementation(async (url: string) => (
      url.endsWith('/lease/acquire/')
        ? {
            success: true,
            lease_token: '00000000-0000-4000-8000-000000000099',
            client_instance_id: mocks.runtime.clientInstanceId,
            expires_at: '2099-01-01T00:00:00Z',
          }
        : { success: true }
    ))
    const wrapper = mount(StudentAssessment)
    await flushPromises()
    await wrapper.findAll<HTMLInputElement>('input[name="answer"]')[0]!.setValue(true)
    await flushPromises()

    globalThis.dispatchEvent(new Event('pagehide'))
    await flushPromises()

    expect(mocks.api.post).toHaveBeenCalledWith(
      captured.process.writer_lease_release_url,
      expect.any(Object),
      { keepalive: true },
    )
    wrapper.unmount()
  })

  it('replaces the assessment runtime when Vue Router reuses the component for another question', async () => {
    const secondQuestion = {
      ...structuredClone(questionState),
      assignment: { assignment_id: 12, attempt_id: 14, status: 'visited', sequence_index: 2 },
      question: {
        ...questionState.question,
        snapshot_id: 24,
        stable_code: 'C000001-Q000002',
        code: 'Q2',
        text: '3 + 3 等于多少？',
      },
      navigation: questionState.navigation.map((item) => ({
        ...item,
        current: item.assignment_id === 12,
      })),
      actions: {
        ...questionState.actions,
        save_draft_url: '/student/attempts/14/draft/',
        submit_url: '/student/attempts/14/submit/',
        skip_url: '/student/attempts/14/skip/',
      },
      process: { ...questionState.process, attempt_id: 14 },
    }
    mocks.api.question
      .mockResolvedValueOnce(structuredClone(questionState))
      .mockResolvedValueOnce(secondQuestion)
    const secondRuntime = {
      record: vi.fn(),
      checkpoint: vi.fn(),
      flushBounded: vi.fn(),
      flushConfirmed: vi.fn(),
      closeAttempt: vi.fn(),
      dispose: vi.fn(),
    }
    mocks.createProcessRuntime
      .mockResolvedValueOnce(mocks.runtime)
      .mockResolvedValueOnce(secondRuntime)

    const wrapper = mount(StudentAssessment)
    await flushPromises()

    await mocks.beforeRouteUpdate?.({
      params: { sessionId: '7', assignmentId: '12' },
      query: { reason: 'navigation_click' },
    })
    await flushPromises()

    expect(mocks.api.saveDraft.mock.calls[0]?.slice(0, 2)).toEqual([
      '/student/attempts/13/draft/',
      null,
    ])
    expect(mocks.runtime.closeAttempt).toHaveBeenCalledWith('navigation', 12)
    expect(mocks.runtime.dispose).toHaveBeenCalledOnce()
    expect(mocks.api.question).toHaveBeenLastCalledWith(7, 12, 'navigation_click', expect.any(Object))
    expect(wrapper.get('h1').text()).toBe('第 2/2 题')
    expect(wrapper.text()).toContain('3 + 3 等于多少？')
    expect(wrapper.get('[aria-current="step"]').text()).toContain('第 2 题')
  })

  it('defers routed-question offline preparation until the route guard has committed', async () => {
    const secondQuestion = captureV2Question({
      assignment: { assignment_id: 12, attempt_id: 14, status: 'visited', sequence_index: 2 },
      navigation: questionState.navigation.map(item => ({
        ...item,
        current: item.assignment_id === 12,
      })),
      process: {
        ...captureV2Question().process,
        attempt_id: 14,
      },
    })
    mocks.api.question
      .mockResolvedValueOnce(structuredClone(questionState))
      .mockResolvedValueOnce(secondQuestion)

    mount(StudentAssessment)
    await flushPromises()
    mocks.prepareOfflineQuestion.mockClear()

    await mocks.beforeRouteUpdate?.({
      params: { sessionId: '7', assignmentId: '12' },
      query: { reason: 'navigation_click' },
    })

    expect(mocks.prepareOfflineQuestion).not.toHaveBeenCalled()
    expect(mocks.afterRouteEach).toBeTypeOf('function')
    mocks.afterRouteEach?.({
      params: { sessionId: '7', assignmentId: '12' },
      query: { reason: 'navigation_click' },
    })
    await flushPromises()
    expect(mocks.prepareOfflineQuestion).toHaveBeenCalledOnce()
    expect(mocks.prepareOfflineQuestion).toHaveBeenCalledWith(secondQuestion)
  })

  it('keeps an unsaved answer in memory while offline and retries when connectivity returns', async () => {
    const wrapper = mount(StudentAssessment)
    await flushPromises()
    Object.defineProperty(globalThis.navigator, 'onLine', { configurable: true, value: false })
    globalThis.dispatchEvent(new Event('offline'))

    await wrapper.findAll<HTMLInputElement>('input[name="answer"]')[0]?.setValue(true)
    await flushPromises()

    expect(mocks.api.saveDraft).not.toHaveBeenCalled()
    expect(wrapper.get('[data-connectivity-status]').text()).toContain('当前离线')
    expect(wrapper.findAll<HTMLInputElement>('input[name="answer"]')[0]?.element.checked).toBe(true)

    Object.defineProperty(globalThis.navigator, 'onLine', { configurable: true, value: true })
    globalThis.dispatchEvent(new Event('online'))
    await flushPromises()

    expect(mocks.api.saveDraft).toHaveBeenCalledOnce()
    expect(wrapper.get('[data-save-status]').text()).toBe('草稿已保存')
  })

  it('keeps the visible answer in place when a draft save fails', async () => {
    mocks.api.saveDraft.mockRejectedValue(new Error('网络暂时不可用'))
    const wrapper = mount(StudentAssessment)
    await flushPromises()

    await wrapper.findAll<HTMLInputElement>('input[name="answer"]')[0]?.setValue(true)
    await flushPromises()

    const answers = wrapper.findAll<HTMLInputElement>('input[name="answer"]')
    expect(answers).toHaveLength(2)
    expect(answers[0]?.element.checked).toBe(true)
    expect(wrapper.get('[role="alert"]').text()).toContain('当前答案仍保留在本页')
  })

  it('locks controls for an already submitted attempt without exposing correctness', async () => {
    mocks.api.question.mockResolvedValue({
      ...structuredClone(questionState),
      assignment: { ...questionState.assignment, status: 'submitted' },
      draft_answer: 'B',
    })

    const wrapper = mount(StudentAssessment)
    await flushPromises()

    expect(wrapper.findAll<HTMLInputElement>('input[name="answer"]').every((item) => item.element.disabled)).toBe(true)
    expect(wrapper.find('[data-submit-request]').exists()).toBe(false)
    expect(wrapper.find('[data-skip-request]').exists()).toBe(false)
    expect(wrapper.text()).toContain('本题已提交并锁定')
    expect(wrapper.text()).not.toContain('回答正确')
    expect(wrapper.text()).not.toContain('回答错误')
  })

  it('navigates away from a locked attempt without trying to rewrite its draft', async () => {
    mocks.api.question.mockResolvedValue({
      ...structuredClone(questionState),
      assignment: { ...questionState.assignment, status: 'submitted' },
      draft_answer: 'B',
    })
    const wrapper = mount(StudentAssessment)
    await flushPromises()

    await wrapper.get('[data-nav-assignment="12"]').trigger('click')
    await flushPromises()

    expect(mocks.api.saveDraft).not.toHaveBeenCalled()
    expect(mocks.runtime.closeAttempt).toHaveBeenCalledWith('navigation', 12)
    expect(mocks.push).toHaveBeenCalledWith(expect.objectContaining({
      path: '/student/sessions/7/questions/12/',
      query: expect.objectContaining({
        reason: 'navigation_click',
        navigation_id: expect.any(String),
        client_occurred_at: expect.any(String),
        client_monotonic_ms: expect.any(String),
        client_time_origin: expect.any(String),
      }),
    }))
  })

  it('freezes navigation identity and click clocks before draft work and reuses them on retry', async () => {
    const calls: string[] = []
    mocks.api.saveDraft.mockImplementation(async () => { calls.push('save'); return { success: true, saved_at: '2026-08-30T08:00:00Z' } })
    mocks.runtime.closeAttempt.mockImplementation(async () => { calls.push('close') })
    mocks.push.mockImplementation(async () => { calls.push('push'); throw new Error('route failed') })
    const wrapper = mount(StudentAssessment)
    await flushPromises()

    await wrapper.get('[data-nav-assignment="12"]').trigger('click')
    await flushPromises()
    const firstRoute = structuredClone(mocks.push.mock.calls[0]?.[0])
    await wrapper.get('[data-nav-assignment="12"]').trigger('click')
    await flushPromises()
    const secondRoute = mocks.push.mock.calls[1]?.[0]

    expect(calls.slice(0, 3)).toEqual(['save', 'close', 'push'])
    expect(secondRoute.query).toEqual(firstRoute.query)
  })

  it('removes a consumed navigation id from history after authoritative entry succeeds', async () => {
    const secondQuestion = captureV2Question({
      assignment: { assignment_id: 12, attempt_id: 14, status: 'visited', sequence_index: 2 },
      navigation: questionState.navigation.map(item => ({
        ...item,
        current: item.assignment_id === 12,
      })),
      process: {
        ...captureV2Question().process,
        attempt_id: 14,
        visit_id: 42,
        navigation_id: '00000000-0000-4000-8000-000000000042',
      },
    })
    mocks.api.question
      .mockResolvedValueOnce(structuredClone(questionState))
      .mockResolvedValueOnce(secondQuestion)
    const wrapper = mount(StudentAssessment)
    await flushPromises()
    const to = {
      params: { sessionId: '7', assignmentId: '12' },
      query: {
        reason: 'navigation_click',
        navigation_id: '00000000-0000-4000-8000-000000000042',
        client_occurred_at: '2026-09-09T10:00:00.000Z',
        client_monotonic_ms: '1000.5',
        client_time_origin: '2026-09-09T09:59:59.000Z',
      },
    }

    await mocks.beforeRouteUpdate?.(to)
    mocks.afterRouteEach?.(to)
    await flushPromises()

    expect(mocks.replace).toHaveBeenCalledWith('/student/sessions/7/questions/12/')
    wrapper.unmount()
  })

  it('collects confidence before the final atomic submission without accepting an untouched default', async () => {
    mocks.api.post.mockImplementation(async (url: string) => {
      if (url.includes('/lease/')) {
        return {
          success: true,
          lease_token: '00000000-0000-4000-8000-000000000099',
          client_instance_id: mocks.runtime.clientInstanceId,
          expires_at: '2099-01-01T00:00:00Z',
        }
      }
      return { success: true }
    })
    mocks.api.question.mockResolvedValue(captureV2Question({
      pending_confidence: {
        confidence_assignment_id: '31',
        scale_version: 'v2',
        prompt_version: 'v1',
        minimum: 0,
        maximum: 100,
        step: 1,
        prompt_shown_url: '/assessment/process/confidence/13/prompt-shown/',
        commit_url: '/assessment/process/confidence/13/commit/',
        skip_url: '/assessment/process/confidence/13/skip/',
      },
    }))
    const wrapper = mount(StudentAssessment)
    await flushPromises()
    expect(wrapper.find('[data-confidence-panel]').exists()).toBe(false)
    await wrapper.findAll<HTMLInputElement>('input[name="answer"]')[1]?.setValue(true)
    await wrapper.get('[data-submit-request]').trigger('click')
    await flushPromises()

    expect(mocks.api.post).toHaveBeenCalledWith(
      '/assessment/process/confidence/13/prompt-shown/',
      {},
    )
    expect(mocks.push).not.toHaveBeenCalled()
    expect(mocks.api.submit).not.toHaveBeenCalled()
    expect(wrapper.get('[data-confidence-submit]').attributes('disabled')).toBeDefined()
    expect(wrapper.get('[data-confidence-panel]').text()).toContain('你对这次答案有多大把握？')

    const slider = wrapper.get<HTMLInputElement>('[data-confidence-input]')
    const numberInput = wrapper.get<HTMLInputElement>('[data-confidence-number]')
    expect(slider.attributes('step')).toBe('1')
    await slider.setValue(57)
    expect(numberInput.element.value).toBe('57')
    await numberInput.setValue(0)
    expect(slider.element.value).toBe('0')
    await numberInput.setValue(57)
    await wrapper.get('[data-confidence-submit]').trigger('click')
    await flushPromises()

    expect(mocks.push).not.toHaveBeenCalled()
    expect(mocks.api.submit).not.toHaveBeenCalled()
    await wrapper.get('[data-submit-confirm]').trigger('click')
    await flushPromises()
    expect(mocks.api.submit).toHaveBeenCalledWith(
      questionState.actions.submit_url,
      expect.any(String), expect.any(String), null,
      {
        writer_lease_token: '00000000-0000-4000-8000-000000000099',
        writer_client_instance_id: mocks.runtime.clientInstanceId,
      },
      { status: 'committed', probability: 57, answer_event_id: null },
    )
    expect(mocks.api.post).toHaveBeenCalledWith(
      captureV2Question().process.writer_lease_release_url,
      expect.objectContaining({
        lease_token: '00000000-0000-4000-8000-000000000099',
      }),
      {},
    )
    expect(mocks.api.post.mock.calls.every(call => !String(call[0]).endsWith('/commit/'))).toBe(true)
    const confidenceEvents = mocks.runtime.record.mock.calls.filter(call => (
      call[0] === 'confidence_observed' && call[1].stage === 'responded'
    ))
    expect(confidenceEvents).toHaveLength(0)
    expect(wrapper.text()).not.toContain('正确答案')
    expect(mocks.runtime.record).toHaveBeenCalledWith(
      'feedback_shown',
      {
        feedback_mode: 'none',
        included_correctness: false,
        included_correct_answer: false,
        included_explanation: false,
      },
      {},
    )
    expect(mocks.push).toHaveBeenCalledWith({
      path: '/student/sessions/7/questions/12/',
      query: { reason: 'submit_next' },
    })
  })

  it('keeps tentative confidence values local without recording research events', async () => {
    mocks.api.question.mockResolvedValue(captureV2Question({
      pending_confidence: {
        confidence_assignment_id: '31', scale_version: 'v2', prompt_version: 'v1',
        minimum: 0, maximum: 100, step: 1,
        prompt_shown_url: '/assessment/process/confidence/13/prompt-shown/',
        commit_url: '/assessment/process/confidence/13/commit/',
        skip_url: '/assessment/process/confidence/13/skip/',
      },
    }))
    const wrapper = mount(StudentAssessment)
    await flushPromises()
    await wrapper.findAll<HTMLInputElement>('input[name="answer"]')[1]?.setValue(true)
    await wrapper.get('[data-submit-request]').trigger('click')
    await flushPromises()
    const numberInput = wrapper.get<HTMLInputElement>('[data-confidence-number]')
    const respondedEvents = () => mocks.runtime.record.mock.calls.filter(call => (
      call[0] === 'confidence_observed' && call[1].stage === 'responded'
    ))

    for (const value of ['', '57.5', '101']) {
      await numberInput.setValue(value)
      expect(wrapper.get('[data-confidence-error]').text()).not.toBe('')
      expect(wrapper.get('[data-confidence-submit]').attributes('disabled')).toBeDefined()
      expect(respondedEvents()).toHaveLength(0)
    }

    await numberInput.setValue('100')
    expect(wrapper.find('[data-confidence-error]').exists()).toBe(false)
    expect(wrapper.get<HTMLInputElement>('[data-confidence-input]').element.value).toBe('100')
    expect(wrapper.get('[data-confidence-submit]').attributes('disabled')).toBeUndefined()
    expect(respondedEvents()).toHaveLength(0)
    wrapper.unmount()
  })

  it('cancels pending confidence resolution when returning to edit', async () => {
    let shown!: (value: unknown) => void
    mocks.api.post.mockImplementationOnce(() => new Promise(resolve => { shown = resolve }))
    mocks.api.question.mockResolvedValue({ ...structuredClone(questionState), pending_confidence: {
      confidence_assignment_id: '31', scale_version: 'v1', prompt_version: 'v1', minimum: 0, maximum: 100, step: 10,
      prompt_shown_url: '/assessment/process/confidence/13/prompt-shown/',
      commit_url: '/assessment/process/confidence/13/commit/', skip_url: '/assessment/process/confidence/13/skip/',
    } })
    const wrapper = mount(StudentAssessment)
    await flushPromises()
    await wrapper.findAll<HTMLInputElement>('input[name="answer"]')[1]?.setValue(true)
    await wrapper.get('[data-submit-request]').trigger('click')
    await flushPromises()
    await wrapper.get<HTMLInputElement>('[data-confidence-input]').setValue(80)
    await wrapper.get('[data-confidence-submit]').trigger('click')
    await wrapper.get('[data-return-answer]').trigger('click')
    shown({ success: true })
    await flushPromises()
    expect(wrapper.find('[data-submit-confirm]').exists()).toBe(false)
    expect(mocks.api.submit).not.toHaveBeenCalled()
    await wrapper.get('[data-submit-request]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-confidence-submit]').attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  it('recovers a lost final response without sending another submission', async () => {
    mocks.api.submit.mockRejectedValueOnce(new TypeError('network lost'))
    mocks.api.recover.mockResolvedValue({ status: 'submitted', submission_matches: true, can_continue: false,
      result: { success: true, status: 'submitted', confidence_required: false, next_url: '/student/sessions/7/questions/12/' },
    })
    const wrapper = mount(StudentAssessment)
    await flushPromises()
    await wrapper.findAll<HTMLInputElement>('input[name="answer"]')[1]?.setValue(true)
    await wrapper.get('[data-submit-request]').trigger('click')
    await wrapper.get('[data-submit-confirm]').trigger('click')
    await flushPromises()
    expect(mocks.api.recover).toHaveBeenCalledWith(13, expect.any(String))
    expect(mocks.api.submit).toHaveBeenCalledOnce()
    expect(mocks.runtime.saveSubmissionIntent).toHaveBeenLastCalledWith(null)
    expect(mocks.push).toHaveBeenCalledWith({
      path: '/student/sessions/7/questions/12/',
      query: { reason: 'submit_next' },
    })
  })

  it('restores an uncommitted intent after reload and retries with its original token', async () => {
    mocks.api.question.mockResolvedValue({ ...structuredClone(questionState), draft_answer: 'B' })
    mocks.createProcessRuntime.mockResolvedValue({ ...mocks.runtime, restoredSubmission: {
      token: '11111111-1111-4111-8111-111111111111', clickedAt: '2026-09-09T00:00:00Z', answer: JSON.stringify('B'),
    } })
    const wrapper = mount(StudentAssessment)
    await flushPromises()
    expect(mocks.api.recover).toHaveBeenCalledWith(13, '11111111-1111-4111-8111-111111111111')
    expect(mocks.api.submit).not.toHaveBeenCalled()
    await wrapper.get('[data-submit-confirm]').trigger('click')
    await flushPromises()
    expect(mocks.api.submit).toHaveBeenCalledWith(questionState.actions.submit_url, '11111111-1111-4111-8111-111111111111', '2026-09-09T00:00:00Z', undefined, undefined, undefined)
    wrapper.unmount()
  })

  it('confirms and posts finish only after the current draft is durable', async () => {
    const wrapper = mount(StudentAssessment)
    await flushPromises()

    await wrapper.get('[data-finish-request]').trigger('click')
    expect(wrapper.get('[role="dialog"]').text()).toContain('确认结束实验')
    await wrapper.get('[data-finish-confirm]').trigger('click')
    await flushPromises()

    expect(mocks.api.saveDraft).toHaveBeenCalled()
    expect(mocks.runtime.closeAttempt).toHaveBeenCalledWith('finish')
    expect(mocks.api.post).toHaveBeenCalledWith('/student/sessions/7/finish/', {})
    expect(document.querySelector('[data-runtime-finish-form]')).toBeNull()
  })

  it('treats the countdown as non-authoritative display and resynchronizes at zero', async () => {
    vi.useFakeTimers()
    mocks.api.question.mockResolvedValue({
      ...structuredClone(questionState),
      remaining_seconds: 1,
    })
    const wrapper = mount(StudentAssessment)
    await flushPromises()

    await vi.advanceTimersByTimeAsync(1_000)

    expect(wrapper.get('[role="alert"]').text()).toContain('页面将刷新并同步服务器状态')
    const answers = wrapper.findAll<HTMLInputElement>('input[name="answer"]')
    expect(answers).toHaveLength(2)
    expect(answers.every((item) => item.element.disabled)).toBe(true)
    expect(mocks.runtime.closeAttempt).toHaveBeenCalledWith('timeout')
  })

  it('blocks entry with a business explanation when durable browser storage is unavailable', async () => {
    vi.stubGlobal('indexedDB', undefined)

    const wrapper = mount(StudentAssessment)
    await flushPromises()

    expect(mocks.api.question).not.toHaveBeenCalled()
    expect(wrapper.get('[role="alert"]').text()).toContain('当前浏览器无法安全保存作答过程')
    expect(wrapper.text()).toContain('IndexedDB')
  })

  it('blocks assessment writes and question navigation when capture preparation fails', async () => {
    mocks.api.question.mockResolvedValue(captureV2Question())
    mocks.prepareOfflineQuestion.mockRejectedValueOnce(preparationFailure())

    const wrapper = mount(StudentAssessment)
    await flushPromises()

    expect(wrapper.get('[data-init-state]').attributes('data-init-state')).toBe('failed')
    expect(wrapper.get('[data-init-failure]').text()).toContain('安全作答环境准备失败')
    expect(wrapper.get('[data-init-failure]').text()).toContain('作答环境准备失败，当前暂不能作答。请重试；持续失败请联系教师。')
    expect(wrapper.get('[data-init-technical-details]').text()).toContain('capture_contract_unavailable')
    expect(wrapper.get('[data-init-return]').attributes('href')).toBe('/student/')
    expect(wrapper.findAll<HTMLInputElement>('input[name="answer"]').every(input => input.element.disabled)).toBe(true)
    expect(wrapper.get('[data-clear-response]').attributes('disabled')).toBeDefined()
    expect(wrapper.get('[data-submit-request]').attributes('disabled')).toBeDefined()
    expect(wrapper.get('[data-skip-request]').attributes('disabled')).toBeDefined()
    expect(wrapper.get('[data-finish-request]').attributes('disabled')).toBeDefined()
    expect(wrapper.get('[data-nav-assignment="12"]').attributes('aria-disabled')).toBe('true')
    expect(wrapper.get('[data-nav-assignment="12"]').attributes('href')).toBeUndefined()

    await wrapper.findAll('input[name="answer"]')[0]!.trigger('change')
    await wrapper.get('[data-clear-response]').trigger('click')
    await wrapper.get('[data-submit-request]').trigger('click')
    await wrapper.get('[data-skip-request]').trigger('click')
    const finishButton = wrapper.get('[data-finish-request]')
    finishButton.element.removeAttribute('disabled')
    await finishButton.trigger('click')
    await wrapper.get('[data-nav-assignment="12"]').trigger('click')
    const routeResult = await mocks.beforeRouteUpdate?.({
      params: { sessionId: '7', assignmentId: '12' },
      query: { reason: 'navigation_click' },
    })

    expect(routeResult).toBe(false)
    expect(mocks.api.saveDraft).not.toHaveBeenCalled()
    expect(mocks.api.submit).not.toHaveBeenCalled()
    expect(mocks.api.skip).not.toHaveBeenCalled()
    expect(mocks.push).not.toHaveBeenCalled()
    expect(mocks.runtime.closeAttempt).not.toHaveBeenCalled()
    expect(mocks.runtime.record).not.toHaveBeenCalled()
    expect(wrapper.find('[aria-labelledby="submit-dialog-title"]').exists()).toBe(false)
    expect(wrapper.find('[aria-labelledby="finish-dialog-title"]').exists()).toBe(false)
  })

  it('retries only offline preparation as a single flight without resetting the deadline timer', async () => {
    vi.useFakeTimers()
    let finishRetry!: () => void
    const retry = new Promise<void>(resolve => { finishRetry = resolve })
    mocks.api.question.mockResolvedValue(captureV2Question())
    mocks.prepareOfflineQuestion
      .mockRejectedValueOnce(preparationFailure())
      .mockReturnValueOnce(retry)

    const wrapper = mount(StudentAssessment)
    await flushPromises()
    expect(wrapper.get('[role="timer"]').text()).toContain('00:10')

    await vi.advanceTimersByTimeAsync(2_000)
    const retryButton = wrapper.get('[data-init-retry]')
    retryButton.element.dispatchEvent(new MouseEvent('click'))
    retryButton.element.dispatchEvent(new MouseEvent('click'))
    await flushPromises()

    expect(wrapper.get('[data-init-state]').attributes('data-init-state')).toBe('preparing')
    expect(mocks.prepareOfflineQuestion).toHaveBeenCalledTimes(2)
    expect(mocks.api.question).toHaveBeenCalledOnce()
    expect(mocks.createProcessRuntime).toHaveBeenCalledOnce()
    expect(mocks.runtime.record).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(1_000)
    finishRetry()
    await flushPromises()

    expect(wrapper.get('[data-init-state]').attributes('data-init-state')).toBe('ready')
    expect(wrapper.get('[role="timer"]').text()).toContain('00:07')
    expect(mocks.runtime.record).toHaveBeenCalledOnce()
    expect(mocks.runtime.record).toHaveBeenCalledWith('item_rendered', { presentation_state: 'interactive' })
    expect(wrapper.findAll<HTMLInputElement>('input[name="answer"]').every(input => !input.element.disabled)).toBe(true)
  })

  it('enables the answer controls before recording interactive item rendering', async () => {
    mocks.api.question.mockResolvedValue(captureV2Question())
    let controlsEnabled = () => false
    mocks.runtime.record.mockImplementationOnce(async (eventType: string) => {
      expect(eventType).toBe('item_rendered')
      expect(controlsEnabled()).toBe(true)
      return { recorded: true }
    })

    const wrapper = mount(StudentAssessment)
    controlsEnabled = () => wrapper.findAll<HTMLInputElement>('input[name="answer"]').every(input => !input.element.disabled)
    await flushPromises()

    expect(wrapper.get('[data-init-state]').attributes('data-init-state')).toBe('ready')
  })

  it('withdraws readiness when recording interactive item rendering fails', async () => {
    mocks.api.question.mockResolvedValue(captureV2Question())
    mocks.runtime.record.mockRejectedValueOnce(new Error('item_rendered_persistence_failed'))

    const wrapper = mount(StudentAssessment)
    await flushPromises()

    expect(wrapper.get('[data-init-state]').attributes('data-init-state')).toBe('failed')
    expect(wrapper.findAll<HTMLInputElement>('input[name="answer"]').every(input => input.element.disabled)).toBe(true)
    expect(wrapper.get('[data-init-technical-details]').text()).toContain('item_rendered_persistence_failed')
    expect(wrapper.find('[data-init-retry]').exists()).toBe(true)
  })

  it('does not let a disposed preparation promise record rendering or unlock interaction', async () => {
    let finishPreparation!: () => void
    mocks.api.question.mockResolvedValue(captureV2Question())
    mocks.prepareOfflineQuestion.mockReturnValueOnce(new Promise<void>(resolve => { finishPreparation = resolve }))

    const wrapper = mount(StudentAssessment)
    await flushPromises()
    expect(wrapper.get('[data-init-state]').attributes('data-init-state')).toBe('preparing')

    wrapper.unmount()
    finishPreparation()
    await flushPromises()

    expect(mocks.runtime.record).not.toHaveBeenCalled()
  })

  it('does not start a runtime when the question response arrives after unmount', async () => {
    let finishQuestion!: (value: typeof questionState) => void
    mocks.api.question.mockReturnValueOnce(new Promise(resolve => { finishQuestion = resolve }))

    const wrapper = mount(StudentAssessment)
    await flushPromises()
    wrapper.unmount()
    finishQuestion(structuredClone(questionState))
    await flushPromises()

    expect(mocks.createProcessRuntime).not.toHaveBeenCalled()
    expect(mocks.prepareOfflineQuestion).not.toHaveBeenCalled()
    expect(mocks.runtime.record).not.toHaveBeenCalled()
  })

  it('disposes a runtime that finishes initializing after unmount', async () => {
    let finishRuntime!: (value: typeof mocks.runtime) => void
    mocks.api.question.mockResolvedValue(captureV2Question())
    mocks.createProcessRuntime.mockReturnValueOnce(new Promise(resolve => { finishRuntime = resolve }))

    const wrapper = mount(StudentAssessment)
    await flushPromises()
    wrapper.unmount()
    finishRuntime(mocks.runtime)
    await flushPromises()

    expect(mocks.runtime.dispose).toHaveBeenCalledOnce()
    expect(mocks.prepareOfflineQuestion).not.toHaveBeenCalled()
    expect(mocks.runtime.record).not.toHaveBeenCalled()
  })

  it('requires formal entry when recovery bootstrap receives a live read-only DTO', async () => {
    mocks.offlineBootstrapRecovery = true
    const enter = vi.spyOn(mocks.api, 'enterQuestion')
    const readOnly = captureV2Question({ process: { ...captureV2Question().process, visit_id: null, visit_index: null, entry_event_id: null, navigation_id: null } })
    mocks.api.question.mockResolvedValueOnce(readOnly).mockResolvedValueOnce(captureV2Question())
    const wrapper = mount(StudentAssessment)
    try {
      await flushPromises()
      expect(enter).toHaveBeenCalledOnce()
      expect(mocks.createProcessRuntime).toHaveBeenCalledWith(expect.objectContaining({ visit_id: 41 }), 'visited', 'csrf-token')
      expect(wrapper.get('[data-init-state]').attributes('data-init-state')).toBe('ready')
    } finally {
      wrapper.unmount()
      enter.mockRestore()
    }
  })

  it('restores the authorized offline DTO without an entry POST when navigator reports online', async () => {
    mocks.offlineBootstrapRecovery = true
    const enter = vi.spyOn(mocks.api, 'enterQuestion')
    mocks.api.question.mockResolvedValue(captureV2Question({ offline_recovery: true }))
    const wrapper = mount(StudentAssessment)
    try {
      await flushPromises()
      expect(enter).not.toHaveBeenCalled()
      expect(mocks.api.question).toHaveBeenCalledWith(7, 11)
      expect(wrapper.get('[data-init-state]').attributes('data-init-state')).toBe('ready')
    } finally {
      wrapper.unmount()
      enter.mockRestore()
    }
  })

  it('keeps an offline-recovered capture question answerable from its cached trajectory', async () => {
    Object.defineProperty(globalThis.navigator, 'onLine', { configurable: true, value: false })
    mocks.api.question.mockResolvedValue(captureV2Question({ offline_recovery: true }))

    const wrapper = mount(StudentAssessment)
    await flushPromises()

    expect(wrapper.get('[data-init-state]').attributes('data-init-state')).toBe('ready')
    expect(wrapper.findAll<HTMLInputElement>('input[name="answer"]').every(input => !input.element.disabled)).toBe(true)
    await wrapper.findAll<HTMLInputElement>('input[name="answer"]')[0]!.setValue(true)
    await flushPromises()
    expect(mocks.api.saveDraft).not.toHaveBeenCalled()
    expect(wrapper.get('[data-save-status]').text()).toContain('已保存到本机')
  })
})
