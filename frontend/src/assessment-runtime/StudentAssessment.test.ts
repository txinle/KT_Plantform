import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  api: {
    question: vi.fn(),
    saveDraft: vi.fn(),
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
  afterRouteEach: undefined as undefined | ((to: {
    params: { sessionId: string; assignmentId: string }
    query: Record<string, string>
  }) => void),
  removeAfterEach: vi.fn(),
  createProcessRuntime: vi.fn(),
  prepareOfflineQuestion: vi.fn(),
  runtime: {
    clientInstanceId: '00000000-0000-4000-8000-000000000002',
    record: vi.fn(),
    checkpoint: vi.fn(),
    flushBounded: vi.fn(),
    closeAttempt: vi.fn(),
    dispose: vi.fn(),
    saveSubmissionIntent: vi.fn(),
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
}))
vi.mock('./api', () => ({ getStudentAssessmentApi: () => mocks.api }))
vi.mock('./process-bridge', () => ({
  createProcessRuntime: (...args: unknown[]) => mocks.createProcessRuntime(...args),
}))
vi.mock('./offline-cache', () => ({
  prepareOfflineQuestion: (...args: unknown[]) => mocks.prepareOfflineQuestion(...args),
}))
vi.mock('@/app/bootstrap', () => ({
  getBootstrapContext: () => ({ csrf_token: 'csrf-token' }),
}))

import StudentAssessment from './StudentAssessment.vue'

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
      active_response_event_id: null,
      writer_lease_acquire_url: '/assessment/process/capture-v2/attempts/13/lease/acquire/',
      writer_lease_renew_url: '/assessment/process/capture-v2/attempts/13/lease/renew/',
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

describe('StudentAssessment', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.beforeRouteUpdate = undefined
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
    mocks.api.recover.mockResolvedValue({ status: 'draft', can_continue: true, code: '', result: null })
    mocks.api.saveDraft.mockResolvedValue({ success: true, saved_at: '2026-08-30T08:00:00Z' })
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
    mocks.createProcessRuntime.mockResolvedValue(mocks.runtime)
    mocks.prepareOfflineQuestion.mockResolvedValue(undefined)
    mocks.runtime.record.mockResolvedValue({ recorded: true })
    mocks.runtime.saveSubmissionIntent.mockImplementation(async (intent) => { structuredClone(intent) })
    document.querySelector('[data-runtime-finish-form]')?.remove()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('renders an authoritative focus view without correctness state', async () => {
    const wrapper = mount(StudentAssessment)
    await flushPromises()

    expect(mocks.api.question).toHaveBeenCalledWith(7, 11, 'navigation_click')
    expect(wrapper.get('h1').text()).toBe('第 1/2 题')
    expect(wrapper.text()).toContain('反馈策略实验')
    expect(wrapper.text()).toContain('2 + 2 等于多少？')
    expect(wrapper.get('[aria-current="step"]').text()).toContain('第 1 题')
    expect(wrapper.findAll('input[name="answer"]')).toHaveLength(2)
    expect(wrapper.get('[role="timer"]').text()).toContain('10:00')
    expect(wrapper.text()).not.toContain('回答正确')
    expect(wrapper.text()).not.toContain('回答错误')
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
    const commitIndex = mocks.runtime.record.mock.calls.findIndex((call) => call[0] === 'response_commit_requested')
    expect(mocks.runtime.record.mock.invocationCallOrder[commitIndex]).toBeLessThan(mocks.api.saveDraft.mock.invocationCallOrder.at(-1)!)
    expect(mocks.runtime.flushBounded).toHaveBeenCalledWith(1500)
    expect(mocks.api.submit.mock.calls[0]?.slice(0, 2)).toEqual([
      '/student/attempts/13/submit/',
      '00000000-0000-4000-8000-000000000001',
    ])
    expect(mocks.runtime.closeAttempt).toHaveBeenCalledWith('submit')
    expect(mocks.push).toHaveBeenCalledWith('/student/sessions/7/questions/12/')
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
    expect(mocks.push).toHaveBeenCalledWith('/student/sessions/7/questions/12/')
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
    expect(mocks.push).toHaveBeenCalledWith('/student/sessions/7/questions/12/')
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
    expect(mocks.api.question).toHaveBeenLastCalledWith(7, 12, 'navigation_click')
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
    expect(mocks.push).toHaveBeenCalledWith('/student/sessions/7/questions/12/')
  })

  it('collects confidence before the final atomic submission without accepting an untouched default', async () => {
    mocks.api.question.mockResolvedValue({
      ...structuredClone(questionState),
      pending_confidence: {
        confidence_assignment_id: '31',
        scale_version: 'v1',
        prompt_version: 'v1',
        minimum: 0,
        maximum: 100,
        step: 10,
        prompt_shown_url: '/assessment/process/confidence/13/prompt-shown/',
        commit_url: '/assessment/process/confidence/13/commit/',
        skip_url: '/assessment/process/confidence/13/skip/',
      },
    })
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

    await wrapper.get<HTMLInputElement>('[data-confidence-input]').setValue(80)
    await wrapper.get('[data-confidence-submit]').trigger('click')
    await flushPromises()

    expect(mocks.push).not.toHaveBeenCalled()
    expect(mocks.api.submit).not.toHaveBeenCalled()
    await wrapper.get('[data-submit-confirm]').trigger('click')
    await flushPromises()
    expect(mocks.api.submit).toHaveBeenCalledWith(
      questionState.actions.submit_url,
      expect.any(String), expect.any(String), undefined, undefined,
      { status: 'committed', probability: 80 },
    )
    expect(mocks.api.post.mock.calls.every(call => !String(call[0]).endsWith('/commit/'))).toBe(true)
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
    expect(mocks.push).toHaveBeenCalledWith('/student/sessions/7/questions/12/')
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
    expect(mocks.push).toHaveBeenCalledWith('/student/sessions/7/questions/12/')
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
    const submit = vi.spyOn(globalThis.HTMLFormElement.prototype, 'submit').mockImplementation(() => undefined)
    const wrapper = mount(StudentAssessment)
    await flushPromises()

    await wrapper.get('[data-finish-request]').trigger('click')
    expect(wrapper.get('[role="dialog"]').text()).toContain('确认结束实验')
    await wrapper.get('[data-finish-confirm]').trigger('click')
    await flushPromises()

    expect(mocks.api.saveDraft).toHaveBeenCalled()
    expect(mocks.runtime.closeAttempt).toHaveBeenCalledWith('finish')
    const form = document.querySelector<HTMLFormElement>('[data-runtime-finish-form]')
    expect(form?.method).toBe('post')
    expect(form?.getAttribute('action')).toBe('/student/sessions/7/finish/')
    expect(form?.querySelector<HTMLInputElement>('[name="csrfmiddlewaretoken"]')?.value).toBe('csrf-token')
    expect(submit).toHaveBeenCalledOnce()
    submit.mockRestore()
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
