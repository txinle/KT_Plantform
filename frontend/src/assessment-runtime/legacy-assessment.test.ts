import { beforeEach, describe, expect, it, vi } from 'vitest'

import assessmentSource from '../../../static/js/assessment.js?raw'

const TOKEN = '00000000-0000-4000-8000-000000000011'

type Intent = {
  token: string
  clickedAt: string
  answer: string
  confidence?: { status: string; probability?: number } | null
}

function response(document: unknown, status = 200) {
  return new Response(JSON.stringify(document), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function page(pendingConfidence: unknown = null) {
  document.body.innerHTML = `
    <form id="question-form"
      data-question-type="single_choice"
      data-save-url="/save/"
      data-submit-url="/submit/"
      data-skip-url="/skip/"
      data-recovery-url="/api/v1/student/attempts/11/recovery/"
      data-process-session-id="7"
      data-process-attempt-id="11"
      data-attempt-status="draft"
      data-process-event-url="/events/"
      data-capture-checkpoint-url="/checkpoint/"
      data-time-sync-create-url="/sync/create/"
      data-time-sync-finalize-url="/sync/finalize/"
      data-process-app-version="test"
      data-heartbeat-interval-seconds="30">
      <input name="csrfmiddlewaretoken" value="csrf">
      <input type="radio" name="answer" value="A" checked>
      <input type="radio" name="answer" value="B">
      <div id="operation-error"></div>
      <div id="feedback"></div>
      <section id="confidence-panel" hidden>
        <input id="confidence-probability" value="50">
        <output id="confidence-value"></output>
        <div id="confidence-status"></div>
        <button id="confidence-submit-btn" type="button"></button>
        <button id="confidence-skip-btn" type="button"></button>
        <button id="confidence-return-btn" type="button"></button>
      </section>
      <div class="sticky-actions">
        <button id="skip-btn" type="button"></button>
        <button id="submit-btn" type="button"></button>
      </div>
    </form>
    <div id="save-status"></div>
    <div id="submit-confirm-dialog">
      <button type="button" data-bs-dismiss="modal">取消</button>
      <button type="button" data-confirm-action>提交并锁定</button>
    </div>
    <script id="draft-data" type="application/json">"A"</script>
    <script id="initial-response-state-data" type="application/json">"A"</script>
    <script id="pending-confidence-data" type="application/json">${JSON.stringify(pendingConfidence)}</script>
  `
  HTMLElement.prototype.scrollIntoView = vi.fn()
}

async function settle(iterations = 16) {
  for (let index = 0; index < iterations; index += 1) await Promise.resolve()
}

function click(selector: string) {
  document.querySelector<HTMLButtonElement>(selector)?.click()
}

function install(options: {
  restoredIntent?: Intent | null
  saveIntent?: (intent: Intent | null) => Promise<void>
  fetch: typeof fetch
}) {
  const saveSubmissionIntent = vi.fn(async (_sessionId: number, _attemptId: number, intent: Intent | null) => {
    await options.saveIntent?.(intent)
  })
  const restoreSubmissionIntent = vi.fn(async () => options.restoredIntent ?? null)
  const outbox = { saveSubmissionIntent, restoreSubmissionIntent }
  const collector = {
    record: vi.fn(async () => ({})),
    checkpoint: vi.fn(async () => ({})),
    flush: vi.fn(async () => true),
    flushBounded: vi.fn(async () => true),
    synchronizeClock: vi.fn(async () => null),
    startSynchronization: vi.fn(),
  }
  const closeAttempt = vi.fn(async () => undefined)
  const show = vi.fn()
  const hide = vi.fn()
  Object.assign(window, {
    KTProcessData: {
      DEFAULT_SYNC_INTERVAL_MS: 1000,
      defaultOutbox: vi.fn(async () => outbox),
      ProcessEventCollector: vi.fn(() => collector),
      bindLifecycle: vi.fn(() => ({ ready: Promise.resolve(), closeAttempt })),
    },
    bootstrap: { Modal: { getOrCreateInstance: vi.fn(() => ({ show, hide })) } },
  })
  vi.stubGlobal('fetch', vi.fn(options.fetch))
  vi.spyOn(window.crypto, 'randomUUID').mockReturnValue(TOKEN)
  new Function(assessmentSource)()
  return { saveSubmissionIntent, restoreSubmissionIntent, show, closeAttempt }
}

describe('legacy assessment submission recovery', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    page()
  })

  it('persists one token and reuses it after GET confirms the lost POST was not committed', async () => {
    const submitBodies: Array<Record<string, unknown>> = []
    let submitCount = 0
    const runtime = install({
      fetch: async (input, init) => {
        const url = String(input)
        if (url === '/save/') return response({ success: true })
        if (url === '/submit/') {
          submitBodies.push(JSON.parse(String(init?.body)))
          submitCount += 1
          if (submitCount === 1) throw new TypeError('response lost')
          return response({ success: true, next_url: '/next/', feedback_mode: 'none' })
        }
        if (url === `/api/v1/student/attempts/11/recovery/?submission_token=${TOKEN}`) {
          return response({ status: 'draft', code: '', can_continue: true, question_url: '/question/', submission_matches: false, result: null })
        }
        throw new Error(`unexpected ${url}`)
      },
    })
    await settle()

    click('#submit-btn')
    click('[data-confirm-action]')
    await settle(30)

    expect(document.querySelector('#operation-error')?.textContent).toContain('可以重试本次提交')
    click('[data-confirm-action]')
    await settle(30)

    expect(submitBodies).toHaveLength(2)
    expect(submitBodies.map(body => body.submission_token)).toEqual([TOKEN, TOKEN])
    expect(runtime.saveSubmissionIntent).toHaveBeenCalledWith(7, 11, expect.objectContaining({ token: TOKEN, answer: '"A"' }))
    expect(runtime.saveSubmissionIntent).toHaveBeenLastCalledWith(7, 11, null)
  })

  it('restores an intent after process readiness and accepts a matching committed result without writing a draft', async () => {
    document.querySelector('#submit-btn')?.remove()
    const intent: Intent = { token: TOKEN, clickedAt: '2026-09-08T00:00:00.000Z', answer: '"A"', confidence: null }
    const requests: Array<{ url: string; method: string }> = []
    const runtime = install({
      restoredIntent: intent,
      fetch: async (input, init) => {
        const url = String(input)
        requests.push({ url, method: init?.method || 'GET' })
        if (url === `/api/v1/student/attempts/11/recovery/?submission_token=${TOKEN}`) {
          return response({
            status: 'submitted', code: '', can_continue: false, question_url: '/question/', submission_matches: true,
            result: { success: true, status: 'submitted', next_url: '/next/', feedback_mode: 'none' },
          })
        }
        throw new Error(`unexpected ${url}`)
      },
    })

    await settle(30)

    expect(runtime.restoreSubmissionIntent).toHaveBeenCalledWith(7, 11)
    expect(requests).toEqual([{
      url: `/api/v1/student/attempts/11/recovery/?submission_token=${TOKEN}`,
      method: 'GET',
    }])
    expect(runtime.saveSubmissionIntent).toHaveBeenLastCalledWith(7, 11, null)
    expect(document.querySelector<HTMLInputElement>('[name="answer"]')?.disabled).toBe(true)
  })

  it('does not write another draft or create another token while the result remains unverified', async () => {
    const requests: string[] = []
    const runtime = install({
      fetch: async (input) => {
        const url = String(input)
        requests.push(url)
        if (url === '/save/') return response({ success: true })
        if (url === '/submit/') throw new TypeError('response lost')
        if (url.startsWith('/api/v1/student/attempts/11/recovery/')) throw new TypeError('offline')
        throw new Error(`unexpected ${url}`)
      },
    })
    await settle()

    click('#submit-btn')
    click('[data-confirm-action]')
    await settle(30)
    click('[data-confirm-action]')
    await settle(30)
    click('#skip-btn')
    await settle(20)

    expect(requests.filter(url => url === '/save/')).toHaveLength(1)
    expect(requests.filter(url => url === '/submit/')).toHaveLength(1)
    expect(requests.filter(url => url === '/skip/')).toHaveLength(0)
    expect(requests.filter(url => url.includes('/recovery/')).length).toBeGreaterThanOrEqual(2)
    expect(runtime.saveSubmissionIntent.mock.calls.filter(call => call[2] !== null)).toHaveLength(1)
    expect(document.querySelector('#operation-error')?.textContent).toContain('无法核验提交结果')
    expect(runtime.show.mock.calls.length).toBeGreaterThanOrEqual(2)
    expect(document.querySelector<HTMLInputElement>('[name="answer"]')?.disabled).toBe(true)
  })

  it('clears the verified uncommitted intent when the student cancels the retry dialog to edit', async () => {
    const intent: Intent = { token: TOKEN, clickedAt: '2026-09-08T00:00:00.000Z', answer: '"A"', confidence: { status: 'skipped' } }
    const runtime = install({
      restoredIntent: intent,
      fetch: async (input) => {
        if (String(input) === `/api/v1/student/attempts/11/recovery/?submission_token=${TOKEN}`) {
          return response({ status: 'draft', code: '', can_continue: true, question_url: '/question/', submission_matches: false, result: null })
        }
        throw new Error(`unexpected ${String(input)}`)
      },
    })
    await settle(30)

    click('[data-bs-dismiss="modal"]')
    await settle(20)

    expect(runtime.saveSubmissionIntent).toHaveBeenLastCalledWith(7, 11, null)
    expect(document.querySelector<HTMLInputElement>('[name="answer"]')?.disabled).toBe(false)
  })

  it('stops a delayed confidence action after the student returns to the answer', async () => {
    page({
      minimum: 0, maximum: 100, step: 10,
      prompt_shown_url: '/confidence/prompt/', commit_url: '/confidence/commit/', skip_url: '/confidence/skip/',
    })
    let releasePrompt: (() => void) | undefined
    const prompt = new Promise<void>(resolve => { releasePrompt = resolve })
    const runtime = install({
      fetch: async (input) => {
        if (String(input) === '/confidence/prompt/') {
          await prompt
          return response({ success: true })
        }
        throw new Error(`unexpected ${String(input)}`)
      },
    })
    await settle()

    click('#submit-btn')
    click('#confidence-skip-btn')
    click('#confidence-return-btn')
    releasePrompt?.()
    await settle(30)

    expect(runtime.show).not.toHaveBeenCalled()
  })

  it('does not surface a rejected prompt request after its confidence action was cancelled', async () => {
    page({
      minimum: 0, maximum: 100, step: 10,
      prompt_shown_url: '/confidence/prompt/', commit_url: '/confidence/commit/', skip_url: '/confidence/skip/',
    })
    let rejectPrompt: ((reason?: unknown) => void) | undefined
    const prompt = new Promise<void>((_resolve, reject) => { rejectPrompt = reject })
    const runtime = install({
      fetch: async (input) => {
        if (String(input) === '/confidence/prompt/') {
          await prompt
          return response({ success: true })
        }
        throw new Error(`unexpected ${String(input)}`)
      },
    })
    await settle()

    click('#submit-btn')
    click('#confidence-skip-btn')
    click('#confidence-return-btn')
    rejectPrompt?.(new Error('late failure'))
    await settle(30)

    expect(runtime.show).not.toHaveBeenCalled()
    expect(document.querySelector('#operation-error')?.textContent).toBe('')
  })

  it('shows a Chinese deadline result and clears a restored uncommitted intent', async () => {
    const intent: Intent = { token: TOKEN, clickedAt: '2026-09-08T00:00:00.000Z', answer: '"A"', confidence: null }
    const runtime = install({
      restoredIntent: intent,
      fetch: async (input) => {
        if (String(input) === `/api/v1/student/attempts/11/recovery/?submission_token=${TOKEN}`) {
          return response({ status: 'timed_out', code: 'DEADLINE_EXPIRED', can_continue: false, question_url: '/question/', submission_matches: false, result: null })
        }
        throw new Error(`unexpected ${String(input)}`)
      },
    })

    await settle(30)

    expect(document.querySelector('#operation-error')?.textContent).toContain('作答时间已截止，本次答案未正式提交')
    expect(runtime.saveSubmissionIntent).toHaveBeenLastCalledWith(7, 11, null)
    expect(document.querySelector<HTMLInputElement>('[name="answer"]')?.disabled).toBe(true)
  })

  it('surfaces intent persistence failure and does not contact draft or submit endpoints', async () => {
    const requests: string[] = []
    install({
      saveIntent: async intent => {
        if (intent) throw new Error('storage failed')
      },
      fetch: async input => {
        requests.push(String(input))
        return response({ success: true })
      },
    })
    await settle()

    click('#submit-btn')
    click('[data-confirm-action]')
    await settle(30)

    expect(requests).toEqual([])
    expect(document.querySelector('#operation-error')?.textContent).toContain('本地提交记录保存失败')
  })
})
