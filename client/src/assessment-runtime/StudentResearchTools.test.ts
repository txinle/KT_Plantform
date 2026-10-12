import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, expect, it, vi } from 'vitest'
import { MemoryOutbox } from './fact-store'
import { ProcessEventCollector } from './process-sender'
const mocks = vi.hoisted(() => ({ post: vi.fn() }))
vi.mock('./api', () => ({ getStudentAssessmentApi: () => ({ post: mocks.post }) }))
import StudentResearchTools from './StudentResearchTools.vue'

afterEach(() => vi.clearAllMocks())

function runtime() {
  const record = vi.fn().mockResolvedValue({})
  let writes = Promise.resolve<unknown>(undefined)
  return {
    serializePointerWrite: <T,>(operation: () => Promise<T>) => { const result = writes.then(operation); writes = result.catch(() => undefined); return result },
    pointerWritesSettled: () => writes,
    record,
    checkpoint: record,
    flushBounded: record,
    flushConfirmed: vi.fn().mockResolvedValue(true),
    closeAttempt: record,
    dispose: vi.fn(),
  }
}

it('keeps an initial automatic rule pending through requested and presented HTTP confirmations', async () => {
  const toolsRuntime = runtime()
  let requested!: (value: unknown) => void
  let presented!: (value: unknown) => void
  let observed!: (value: unknown) => void
  mocks.post.mockImplementation((_url, body) => new Promise(resolve => {
    if (body.stage === 'requested') requested = resolve
    else presented = resolve
  }))
  toolsRuntime.record.mockImplementation((type) => type === 'research_tool_observed'
    ? new Promise(resolve => { observed = resolve }) : Promise.resolve({}))
  const wrapper = mount(StudentResearchTools, { props: {
    endpoint: '/tools/', answerEventId: 'target', waitForCapture: async () => undefined, runtime: toolsRuntime,
    initialConfiguration: toolsResponse({ rules: [{ id: 'initial-check', tool: 'check', required: true }] }),
  } })
  try {
    await flushPromises()
    expect(wrapper.emitted('pending')?.at(-1)).toEqual([true])
    requested({ event_id: 'requested', content_version: 'v1' })
    await flushPromises()
    expect(wrapper.emitted('pending')).not.toContainEqual([false])
    observed({})
    await flushPromises()
    expect(mocks.post).toHaveBeenLastCalledWith('/tools/', expect.objectContaining({ stage: 'presented' }))
    expect(wrapper.emitted('pending')?.at(-1)).toEqual([true])
    presented({ event_id: 'presented', content_version: 'v1' })
    await flushPromises()
    expect(wrapper.emitted('pending')?.at(-1)).toEqual([false])
  } finally { wrapper.unmount() }
})

it('does not treat a pending configuration refresh as a tool mutation barrier', async () => {
  const toolsRuntime = runtime()
  let resolveConfiguration!: (value: unknown) => void
  mocks.post.mockImplementation(() => new Promise(resolve => { resolveConfiguration = resolve }))
  const wrapper = mount(StudentResearchTools, { props: {
    endpoint: '/tools/', answerEventId: 'target', waitForCapture: async () => undefined, runtime: toolsRuntime,
  } })
  try {
    await flushPromises()
    expect(wrapper.emitted('pending') || []).not.toContainEqual([true])
    await expect(toolsRuntime.pointerWritesSettled()).resolves.toBeUndefined()
    resolveConfiguration(toolsResponse())
    await flushPromises()
  } finally { wrapper.unmount() }
})

it('keeps an in-flight tool mutation inside the controller write barrier and clears pending after failure', async () => {
  const toolsRuntime = runtime()
  let rejectTool!: (error: Error) => void
  mocks.post.mockImplementation(() => new Promise((_resolve, reject) => { rejectTool = reject }))
  const wrapper = mount(StudentResearchTools, { props: {
    endpoint: '/tools/', answerEventId: 'target', waitForCapture: async () => undefined, runtime: toolsRuntime,
    initialConfiguration: toolsResponse({ doubt_enabled: true }),
  } })
  try {
    await flushPromises()
    await buttonWithText(wrapper, '标记疑点').trigger('click')
    await flushPromises()
    let settled = false
    void toolsRuntime.pointerWritesSettled().then(() => { settled = true })
    await flushPromises()
    expect(settled).toBe(false)
    expect(wrapper.emitted('pending')?.at(-1)).toEqual([true])
    rejectTool(new Error('tool outcome unknown'))
    await flushPromises()
    expect(settled).toBe(true)
    expect(wrapper.emitted('pending')?.at(-1)).toEqual([false])
    expect(buttonWithText(wrapper, '标记疑点').attributes('aria-pressed')).toBe('false')
  } finally { wrapper.unmount() }
})

it('waits for event confirmation before a tool request and retains failure for retry', async () => {
  const toolsRuntime = runtime()
  let confirm!: (value: boolean) => void
  toolsRuntime.flushConfirmed.mockImplementationOnce(() => new Promise<boolean>(resolve => { confirm = resolve }))
  mocks.post.mockResolvedValue({ event_id: 'confirmed-tool', content_version: 'v1' })
  const wrapper = mount(StudentResearchTools, { props: {
    endpoint: '/tools/', answerEventId: 'target', waitForCapture: async () => undefined, runtime: toolsRuntime,
    initialConfiguration: toolsResponse({ doubt_enabled: true }),
  } })
  try {
    await flushPromises()
    await buttonWithText(wrapper, '标记疑点').trigger('click')
    await flushPromises()
    expect(mocks.post).not.toHaveBeenCalled()
    confirm(false)
    await flushPromises()
    expect(mocks.post).not.toHaveBeenCalled()
    expect(wrapper.get('[role="alert"]').text()).toContain('尚未确认保存')
    await buttonWithText(wrapper, '标记疑点').trigger('click')
    await flushPromises()
    expect(mocks.post).toHaveBeenCalledOnce()
  } finally { wrapper.unmount() }
})

it('sends a locally frozen tool bundle in one business request without a prior network flush', async () => {
  const toolsRuntime = { ...runtime(), captureMutation: vi.fn(async (_url, body, send) => send({ ...body, capture_bundle: { version: 1, events: ['local-fact'], checkpoints: ['original-checkpoint'] } })) }
  mocks.post.mockResolvedValue({ event_id: 'confirmed-tool', content_version: 'v1', committed: true })
  const wrapper = mount(StudentResearchTools, { props: { endpoint: '/tools/', answerEventId: 'target', waitForCapture: async () => undefined,
    runtime: toolsRuntime, initialConfiguration: toolsResponse({ doubt_enabled: true }) } })
  try {
    await flushPromises()
    await buttonWithText(wrapper, '标记疑点').trigger('click')
    await flushPromises()
    expect(toolsRuntime.flushConfirmed).not.toHaveBeenCalled()
    expect(toolsRuntime.captureMutation).toHaveBeenCalledOnce()
    expect(mocks.post).toHaveBeenCalledExactlyOnceWith('/tools/', expect.objectContaining({ tool: 'doubt', stage: 'mark_added', capture_bundle: expect.objectContaining({ version: 1 }) }))
  } finally { wrapper.unmount() }
})

it('persists hint closure and subsequent answer events through the real collector', async () => {
  const outbox = new MemoryOutbox()
  const collector = new ProcessEventCollector({ sessionId: 7, attemptId: 13, endpoint: '/api/v1/assessment/process/events/batch/', outbox, autoSchedule: false })
  const toolsRuntime = runtime()
  toolsRuntime.record.mockImplementation((type: string, payload: Record<string, unknown>, options = {}) => collector.record(type, payload, options))
  toolsRuntime.flushBounded = vi.fn().mockResolvedValue(undefined)
  mockHints(toolsResponse({ hints: [{ id: 'h1', level: 1 }] }), { h1: '第一条提示' })
  const wrapper = mount(StudentResearchTools, { props: {
    endpoint: '/tools/', answerEventId: null, waitForCapture: async () => undefined, runtime: toolsRuntime,
  } })
  try {
    await flushPromises()
    await buttonWithText(wrapper, '查看第 1 级提示').trigger('click')
    await flushPromises()
    await buttonWithText(wrapper, '关闭提示').trigger('click')
    await flushPromises()
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    await expect(collector.record('response_input_started', { question_type: 'fill_blank' })).resolves.toBeDefined()
    const batch = await outbox.peekBatch(100)
    expect(batch!.events).toEqual(expect.arrayContaining([
      expect.objectContaining({ event_type: 'research_tool_observed', payload: expect.objectContaining({
        tool: 'hint', stage: 'closed', data: { hint_id: 'h1' }, server_event_id: 'request-h1',
      }) }),
      expect.objectContaining({ event_type: 'response_input_started' }),
    ]))
  } finally {
    wrapper.unmount()
    collector.dispose()
  }
})

function hintRequests() {
  return mocks.post.mock.calls
    .map(([, body]) => body as { tool?: string; stage?: string; data?: { hint_id?: string } })
    .filter((body) => body?.tool === 'hint' && body.stage === 'requested')
    .map((body) => body.data?.hint_id)
}

function buttonWithText(wrapper: ReturnType<typeof mount>, text: string) {
  const button = wrapper.findAll('button').find((element) => element.text() === text)
  if (!button) throw new Error(`Missing button: ${text}`)
  return button
}

function toolsResponse(overrides: Record<string, unknown> = {}) {
  return {
    content_version: 'v1',
    doubt_enabled: false,
    check_enabled: false,
    hints: [],
    rules: [],
    ...overrides,
  }
}

function mockHints(response: ReturnType<typeof toolsResponse>, texts: Record<string, string>) {
  mocks.post.mockImplementation((_endpoint, body: { tool?: string; stage?: string; data?: { hint_id?: string } } = {}) => {
    if (!body.tool) return Promise.resolve(response)
    if (body.tool === 'hint' && body.stage === 'requested') {
      const hintId = body.data?.hint_id || ''
      return Promise.resolve({ event_id: `request-${hintId}`, content_version: 'v1', text: texts[hintId] })
    }
    return Promise.resolve({ event_id: `event-${body.stage}`, content_version: 'v1' })
  })
}

it('uses the authorized question DTO for initial tools and still refreshes time-dependent rules', async () => {
  vi.useFakeTimers()
  const configuration = toolsResponse({ check_enabled: true, state: {
    doubt: false, pending_check: true, checking: false, doubt_note: '',
  } })
  mocks.post.mockResolvedValue(toolsResponse({ check_enabled: true }))
  const wrapper = mount(StudentResearchTools, { props: { endpoint: '/tools/', answerEventId: null,
    waitForCapture: async () => undefined, runtime: runtime(), initialConfiguration: configuration,
  } })
  try {
    await flushPromises()
    expect(wrapper.text()).toContain('解除待检查')
    expect(mocks.post).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(10000)
    expect(mocks.post).toHaveBeenCalledOnce()
  } finally { wrapper.unmount(); vi.useRealTimers() }
})

it('restores marks and a previously presented required rule without requesting another occurrence', async () => {
  mocks.post.mockResolvedValue({ doubt_enabled: true, check_enabled: true, content_version: 'v1',
    state: { doubt: true, pending_check: true, checking: false, doubt_note: '核对定义' },
    rules: [{ id: 'c1', tool: 'check', required: true, pending_request: { event_id: 'prior', content_version: 'v1' }, already_presented: true }] })
  const toolsRuntime = runtime()
  const wrapper = mount(StudentResearchTools, { props: { endpoint: '/tools/', answerEventId: null,
    waitForCapture: async () => undefined,
    runtime: toolsRuntime } })
  await flushPromises()
  expect(wrapper.text()).toContain('解除疑点标记')
  expect(wrapper.text()).toContain('解除待检查')
  expect(mocks.post).toHaveBeenCalledTimes(1)
  expect(toolsRuntime.record).toHaveBeenCalledWith('research_tool_observed', expect.objectContaining({ server_event_id: 'prior' }))
  wrapper.unmount()
})

it('shows due hints in order and starts the next one only after the current hint closes', async () => {
  mockHints(toolsResponse({
    rules: [
      { id: 'hint-1', tool: 'hint', required: false, hint_id: 'h1' },
      { id: 'hint-2', tool: 'hint', required: false, hint_id: 'h2' },
    ],
  }), { h1: '第一条提示', h2: '第二条提示' })
  const wrapper = mount(StudentResearchTools, { props: {
    endpoint: '/tools/', answerEventId: null, waitForCapture: async () => undefined, runtime: runtime(),
  } })

  await flushPromises()
  expect(wrapper.text()).toContain('第一条提示')
  expect(wrapper.text()).not.toContain('第二条提示')
  expect(hintRequests()).toEqual(['h1'])

  await buttonWithText(wrapper, '关闭提示').trigger('click')
  await flushPromises()
  expect(wrapper.text()).toContain('第二条提示')
  expect(hintRequests()).toEqual(['h1', 'h2'])
  wrapper.unmount()
})

it('queues a manual hint behind the current due hint instead of replacing it', async () => {
  mockHints(toolsResponse({
    hints: [{ id: 'h1', level: 1 }, { id: 'manual-hint', level: 2 }],
    rules: [{ id: 'hint-1', tool: 'hint', required: false, hint_id: 'h1' }],
  }), { h1: '自动提示', 'manual-hint': '手动提示' })
  const wrapper = mount(StudentResearchTools, { props: {
    endpoint: '/tools/', answerEventId: null, waitForCapture: async () => undefined, runtime: runtime(),
  } })

  await flushPromises()
  await buttonWithText(wrapper, '查看第 2 级提示').trigger('click')
  await flushPromises()
  expect(wrapper.text()).toContain('自动提示')
  expect(wrapper.text()).not.toContain('手动提示')
  expect(hintRequests()).toEqual(['h1'])

  await buttonWithText(wrapper, '关闭提示').trigger('click')
  await flushPromises()
  expect(wrapper.text()).toContain('手动提示')
  expect(hintRequests()).toEqual(['h1', 'manual-hint'])
  wrapper.unmount()
})

it('does not start a queued hint after the component unmounts', async () => {
  let resolveClose!: (value: { event_id: string; content_version: string }) => void
  const closeResponse = new Promise<{ event_id: string; content_version: string }>((resolve) => {
    resolveClose = resolve
  })
  mocks.post.mockImplementation((_endpoint, body: { tool?: string; stage?: string; data?: { hint_id?: string } } = {}) => {
    if (!body.tool) {
      return Promise.resolve(toolsResponse({
        rules: [
          { id: 'hint-1', tool: 'hint', required: false, hint_id: 'h1' },
          { id: 'hint-2', tool: 'hint', required: false, hint_id: 'h2' },
        ],
      }))
    }
    if (body.tool === 'hint' && body.stage === 'requested') {
      const hintId = body.data?.hint_id || ''
      return Promise.resolve({ event_id: `request-${hintId}`, content_version: 'v1', text: `提示 ${hintId}` })
    }
    if (body.tool === 'hint' && body.stage === 'responded') return closeResponse
    return Promise.resolve({ event_id: `event-${body.stage}`, content_version: 'v1' })
  })
  const wrapper = mount(StudentResearchTools, { props: {
    endpoint: '/tools/', answerEventId: null, waitForCapture: async () => undefined, runtime: runtime(),
  } })

  await flushPromises()
  expect(hintRequests()).toEqual(['h1'])
  await buttonWithText(wrapper, '关闭提示').trigger('click')
  await flushPromises()
  expect(mocks.post).toHaveBeenCalledWith('/tools/', expect.objectContaining({ stage: 'responded' }))
  wrapper.unmount()
  resolveClose({ event_id: 'closed-h1', content_version: 'v1' })
  await flushPromises()
  expect(hintRequests()).toEqual(['h1'])
})

it('uses the frozen v2 scale and blocks invalid confidence input', async () => {
  mocks.post.mockImplementation((_endpoint, body: { tool?: string; stage?: string; data?: Record<string, unknown> } = {}) => {
    if (!body.tool) return Promise.resolve(toolsResponse({
      confidence_scale: { scale_version: 'v2', minimum: 0, maximum: 100, step: 1 },
      rules: [{ id: 'confidence-1', tool: 'confidence', required: true }],
    }))
    return Promise.resolve({ event_id: `event-${body.stage}`, content_version: 'v1' })
  })
  const wrapper = mount(StudentResearchTools, { props: {
    endpoint: '/tools/', answerEventId: null, waitForCapture: async () => undefined, runtime: runtime(),
  } })
  await flushPromises()
  const numberInput = wrapper.get<HTMLInputElement>('[data-tool-confidence-number]')
  const rangeInput = wrapper.get<HTMLInputElement>('[data-tool-confidence-range]')
  const submit = buttonWithText(wrapper, '记录信心')

  expect(rangeInput.attributes('step')).toBe('1')
  expect(submit.attributes('disabled')).toBeDefined()
  await numberInput.setValue('57.5')
  expect(wrapper.get('[data-tool-confidence-error]').text()).toContain('整数')
  expect(submit.attributes('disabled')).toBeDefined()
  expect(mocks.post.mock.calls.some(([, body]) => body?.stage === 'responded')).toBe(false)

  await numberInput.setValue('57')
  expect(rangeInput.element.value).toBe('57')
  expect(submit.attributes('disabled')).toBeUndefined()
  await rangeInput.setValue('58')
  expect(numberInput.element.value).toBe('58')
  await submit.trigger('click')
  await flushPromises()
  expect(mocks.post).toHaveBeenCalledWith('/tools/', expect.objectContaining({
    stage: 'responded', data: expect.objectContaining({ probability: 58 }),
  }))
  wrapper.unmount()
})
