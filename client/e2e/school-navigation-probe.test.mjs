import test from 'node:test'
import assert from 'node:assert/strict'
import { performance } from 'node:perf_hooks'
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { probeAccounts, safeEndpoint, captureVisualLayouts, probeExpect } from './school-navigation-probe.mjs'
import * as probe from './school-navigation-probe.mjs'
import { chromium } from '@playwright/test'

test('confidence-only plan permits no optional tools and requires frozen legal confidence choices', () => {
  const student = { username: 'one', role: 'browser', arrival_offset_seconds: 0, speed_stratum: 'fast',
    item_dwell_seconds: Array(20).fill(45), modify_items: [], excursion_items: [], optional_tool_items: [],
    confidence_actions: Array(20).fill('fill'), confidence_values: Array(20).fill(57), confidence_think_seconds: Array(20).fill(2) }
  const plan = { workload_version: 'natural-180-v1', scenario: 'confidence-only-v1', synthetic_confidence: true,
    confidence_scale: { minimum: 0, maximum: 100, step: 1 }, item_count: 20, task_id: 4, students: [student] }
  assert.deepEqual(probe.naturalBrowserPlans(plan, [{ username: 'one', taskId: 4 }]), [student])
  assert.throws(() => probe.naturalBrowserPlans({ ...plan, students: [{ ...student, optional_tool_items: [2, 3] }] }, [{ username: 'one', taskId: 4 }]))
  assert.throws(() => probe.naturalBrowserPlans({ ...plan, students: [{ ...student, confidence_values: Array(20).fill(101) }] }, [{ username: 'one', taskId: 4 }]))
})

test('confidence actual UI honors planned fill and skip without counting its think time as submission', async () => {
  assert.equal(typeof probe.resolvePlannedConfidence, 'function')
  for (const action of ['fill', 'skip']) {
    const calls = []
    const page = { locator: selector => ({ fill: async value => calls.push(['fill', selector, value]), click: async () => calls.push(['click', selector]) }),
      getByRole: (_role, options) => ({ click: async () => calls.push(['click', options.name]) }) }
    await probe.resolvePlannedConfidence(page, { action, value: action === 'fill' ? 57 : null, thinkSeconds: 2 }, {
      pause: async seconds => calls.push(['think', seconds]), timed: async (name, operation) => { calls.push(['timed', name]); return operation() },
    })
    assert.equal(calls[0][0], 'think')
    assert.ok(!calls.some(call => /submit_ack|submit_next_ready|finish_ready/.test(String(call[1]))))
    if (action === 'fill') assert.deepEqual(calls.slice(2), [['fill', '[data-confidence-number]', '57'], ['click', '[data-confidence-submit]']])
    else assert.deepEqual(calls.slice(2), [['click', '跳过评价']])
  }
})

test('confidence thought records actual wall time before UI resolution and retains interrupted waits', async () => {
  for (const interrupted of [false, true]) {
    let wall = 100
    const waits = []
    const page = { getByRole: () => ({ click: async () => { wall += 5 } }) }
    const operation = probe.resolvePlannedConfidence(page, { action: 'skip', value: null, thinkSeconds: 2 }, {
      now: () => wall, recordThink: row => waits.push(row),
      pause: async () => { wall += interrupted ? 1 : 3; if (interrupted) throw new Error('explicit stop') },
      timed: async (_name, callback) => callback(),
    })
    if (interrupted) await assert.rejects(operation)
    else await operation
    assert.deepEqual(waits, [{ started_at: 100, completed_at: interrupted ? 101 : 103,
      planned_seconds: 2, actual_seconds: interrupted ? 1 : 3, completed: !interrupted }])
  }
})

test('submission phases measure ACK and next-ready inside total, distinguishing final completion', async () => {
  assert.equal(typeof probe.performSubmissionPhases, 'function')
  for (const last of [false, true]) {
    let clock = 2000 // Confidence input and thought already elapsed before this operation.
    const timings = []
    await probe.performSubmissionPhases({ last,
      ack: async () => { clock += 100; return 'receipt' }, ready: async receipt => { assert.equal(receipt, 'receipt'); clock += 400 },
      timed: async (name, operation) => { const start = clock; const result = await operation(); timings.push([name, clock - start]); return result },
    })
    assert.deepEqual(timings, [['submit_ack', 100], [last ? 'last_session_ready' : 'next_question_ready', 400], [last ? 'finish_ready' : 'submit_next_ready', 500]])
  }
})

function closeRecoveryPage({ target = '/student/sessions/7/questions/12/', readyAt = 0, enabledAt = 0, navigateOnClick = false } = {}) {
  const clock = { elapsed: 0, clicks: [], waits: [], path: readyAt === 0 ? target : '/student/sessions/7/questions/11/' }
  const page = { url: () => `https://host${clock.path}`, locator: selector => {
    assert.equal(selector, '[data-retry-close]')
    return { isVisible: async () => clock.elapsed >= readyAt, isEnabled: async () => clock.elapsed >= enabledAt,
      click: async ({ timeout }) => { clock.clicks.push(timeout); if (navigateOnClick) clock.path = target } }
  }, waitForTimeout: async timeout => { clock.waits.push(timeout); clock.elapsed += timeout } }
  return { page, clock, target }
}

test('submitted destination already ready does not attempt close recovery', async () => {
  assert.equal(typeof probe.waitForSubmittedDestination, 'function')
  const { page, clock, target } = closeRecoveryPage()
  const report = {}
  await probe.waitForSubmittedDestination(page, target, async remaining => assert.equal(remaining, 35000), {
    report, sequence: 1, now: () => clock.elapsed,
  })
  assert.deepEqual(clock.clicks, [])
  assert.deepEqual(clock.waits, [])
  assert.equal(report.close_recovery_actions?.length || 0, 0)
})

test('destination failure distinguishes URL arrival from interactive readiness without extending its budget', async () => {
  const { page, clock, target } = closeRecoveryPage()
  const report = {}
  await assert.rejects(probe.waitForSubmittedDestination(page, target, async () => {
    clock.elapsed += 1000
    throw Object.assign(new Error('not interactive'), { name: 'TimeoutError' })
  }, { report, sequence: 2, timeoutMs: 1000, now: () => clock.elapsed }))
  assert.deepEqual(report.destination_waits, [{ sequence: 2, expected_path: target,
    observed_path: target, stage: 'interactive', completed: false, elapsed_ms: 1000 }])
  assert.equal(clock.elapsed, 1000)
})

test('transient close button disappearing during enabled lookup cannot stall an already successful navigation', async () => {
  const { page, clock, target } = closeRecoveryPage({ readyAt: 100 })
  const locator = page.locator
  page.locator = selector => {
    const button = locator(selector)
    button.isEnabled = async options => {
      assert.equal(options?.timeout, 100)
      clock.elapsed += 100
      clock.path = target
      throw Object.assign(new Error('button removed by successful navigation'), { name: 'TimeoutError' })
    }
    return button
  }
  const report = {}
  await probe.waitForSubmittedDestination(page, target, async () => {}, {
    report, sequence: 2, timeoutMs: 1000, now: () => clock.elapsed,
  })
  assert.equal(clock.elapsed, 300)
  assert.deepEqual(clock.clicks, [])
  assert.equal(report.destination_waits[0].completed, true)
})

test('pending close clicks only the visible enabled recovery once before next or final readiness', async () => {
  assert.equal(typeof probe.waitForSubmittedDestination, 'function')
  for (const target of ['/student/sessions/7/questions/12/', '/student/sessions/7/complete/']) {
    const { page, clock } = closeRecoveryPage({ target, readyAt: 100, enabledAt: 300, navigateOnClick: true })
    const report = {}
    let remaining
    await probe.waitForSubmittedDestination(page, target, async budget => { remaining = budget; clock.elapsed += 50 }, {
      report, sequence: 20, timeoutMs: 1000, now: () => clock.elapsed,
    })
    assert.equal(clock.clicks.length, 1)
    assert.equal(clock.clicks[0], 700)
    assert.equal(remaining, 700)
    assert.deepEqual(report.close_recovery_actions, [{ sequence: 20, attempt: 1, completed: true }])
    assert.equal(clock.elapsed, 350)
  }
})

test('close recovery exhaustion never exceeds three clicks or resets the original deadline', async () => {
  assert.equal(typeof probe.waitForSubmittedDestination, 'function')
  const { page, clock, target } = closeRecoveryPage({ readyAt: 100 })
  const report = {}
  await assert.rejects(probe.waitForSubmittedDestination(page, target, async () => assert.fail('never ready'), {
    report, sequence: 3, timeoutMs: 1000, now: () => clock.elapsed,
  }), error => error.name === 'TimeoutError')
  assert.equal(clock.clicks.length, 3)
  assert.deepEqual(clock.clicks, [900, 800, 700])
  assert.equal(clock.elapsed, 1000)
  assert.equal(report.close_recovery_actions.length, 3)
})

test('close recovery observes the actual stop file while waiting without clicking disabled UI', async () => {
  assert.equal(typeof probe.waitForSubmittedDestination, 'function')
  const temporary = mkdtempSync(join(tmpdir(), 'kt-close-recovery-'))
  try {
    const stopFile = join(temporary, 'stop')
    const { page, clock, target } = closeRecoveryPage({ readyAt: 100, enabledAt: 2000 })
    const wait = page.waitForTimeout
    page.waitForTimeout = async timeout => { await wait(timeout); writeFileSync(stopFile, '') }
    await assert.rejects(probe.waitForSubmittedDestination(page, target, async () => assert.fail('never ready'), {
      report: {}, sequence: 3, timeoutMs: 1000, stopFile, now: () => clock.elapsed,
    }), error => error.name === 'StoppedError')
    assert.deepEqual(clock.clicks, [])
    assert.equal(clock.elapsed, 100)
  } finally { rmSync(temporary, { recursive: true, force: true }) }
})

test('submission readiness receives only the budget remaining after the actual ACK wait', async () => {
  let elapsed = 0
  let readyBudget
  await probe.performSubmissionPhases({ last: false, timeoutMs: 1000, now: () => elapsed,
    timed: async (_name, operation) => operation(), ack: async () => { elapsed = 600; return 'receipt' },
    ready: async (_receipt, remaining) => { readyBudget = remaining },
  })
  assert.equal(readyBudget, 400)
})

test('mobile navigation opens the real drawer and uses its owned URL within the same action budget', async () => {
  assert.equal(typeof probe.questionNavigationSteps, 'function')
  let elapsed = 0
  const order = []
  const targetPath = '/student/sessions/7/questions/12/'
  const trigger = { click: async ({ timeout }) => { order.push(['open', timeout]); elapsed += 2000 } }
  const link = { click: async ({ timeout }) => { order.push(['navigate', timeout]); elapsed += 1000 } }
  const drawer = { locator: selector => {
    assert.equal(selector, `a[href="${targetPath}"]:not([aria-disabled="true"])`)
    return link
  } }
  const page = { locator: selector => {
    assert.ok(!selector.includes('.kt-assessment-nav'), 'hidden desktop navigation cannot be used')
    if (selector === '[data-mobile-navigation-trigger]') return trigger
    assert.equal(selector, '#assessment-mobile-navigation')
    return drawer
  } }
  const steps = probe.questionNavigationSteps(page, '12', targetPath, async () => { elapsed += 500 }, {
    mobile: true, now: () => elapsed,
    waitVisible: async (locator, timeout) => { assert.equal(locator, drawer); order.push(['drawer', timeout]); elapsed += 1000 },
    waitEnabled: async (locator, timeout) => { assert.equal(locator, link); order.push(['enabled', timeout]); elapsed += 1000 },
    waitTarget: async (_path, timeout) => { order.push(['target', timeout]); elapsed += 1000 },
  })
  const report = { actions: [] }
  await probe.navigationProbeAction(report, 0, 'navigation_next_ready', 15, steps, { now: () => elapsed })
  assert.deepEqual(order, [['open', 35000], ['drawer', 33000], ['enabled', 32000], ['navigate', 31000], ['target', 30000]])
  assert.equal(report.actions[0].duration_ms, 6500)
  assert.equal(report.actions[0].budget_ms, 35000)
})

test('first-ready screenshots retain each existing viewport and report observed overflow without page mutations', async () => {
  assert.equal(typeof probe.captureFirstReadyLayout, 'function')
  for (const [index, width, height, overflow] of [[0, 1440, 900, false], [1, 390, 844, true]]) {
    const shots = []
    const page = { evaluate: async callback => {
      assert.doesNotMatch(callback.toString(), /scrollTo|requestAnimationFrame|fetch|style\s*=/)
      return { viewport_width: width, viewport_height: height, document_width: width + (overflow ? 20 : 0), overflow }
    }, screenshot: async options => shots.push(options),
    setViewportSize: async () => { throw new Error('existing viewport must remain unchanged') },
    goto: async () => { throw new Error('no additional request or navigation') } }
    const row = await probe.captureFirstReadyLayout(page, 'unused', index, { width, height })
    assert.equal(row.viewport_width, width)
    assert.equal(row.viewport_height, height)
    assert.equal(row.overflow, overflow)
    assert.equal(row.passed, !overflow)
    assert.equal(shots.length, 1)
    assert.equal(shots[0].fullPage, false)
    assert.equal(shots[0].animations, undefined)
    assert.match(row.filename, index === 0 ? /probe-1-active-desktop-1440/ : /probe-2-active-mobile-390/)
  }
})

test('architecture response-state evidence observes ordinary draft capture_bundle without retaining answer text', () => {
  assert.equal(typeof probe.browserResponseStates, 'function')
  const event = { event_type: 'response_transition', client_sequence_no: 3,
    payload: { after_state_hash: 'a'.repeat(64), after_state: { question_type: 'short_answer', value: 'PRIVATE ANSWER' } } }
  const states = probe.browserResponseStates({ capture_bundle: { client_instance_id: 'client', events: [event] } })
  assert.deepEqual(states, [{ client_instance_id: 'client', client_sequence_no: 3,
    question_type: 'short_answer', state_sha256: 'a'.repeat(64) }])
  assert.doesNotMatch(JSON.stringify(states), /PRIVATE/)
})

test('natural text input is segmented within the full dwell without compressing slow operations', async () => {
  assert.equal(typeof probe.performNaturalAnswer, 'function')
  let elapsed = 0
  const fills = []
  const pauses = []
  await probe.performNaturalAnswer({ fill: async value => { fills.push({ value, elapsed }); elapsed += 5 } }, null, 'text', {
    dwellSeconds: 100, modify: false, pause: async seconds => { pauses.push(seconds); elapsed += seconds },
    timed: async (_action, operation) => operation(),
  })
  assert.deepEqual(fills, [{ value: '浏览器', elapsed: 30 }, { value: '浏览器探针作答', elapsed: 65 }])
  assert.equal(pauses.reduce((sum, value) => sum + value, 0), 100)
  assert.equal(elapsed, 110)
})

test('natural multi-choice modification keeps a valid answer through both witnessed option changes', async () => {
  assert.equal(typeof probe.performNaturalAnswer, 'function')
  const selected = new Set()
  const changes = []
  const option = key => ({ check: async () => { selected.add(key); changes.push([...selected]) },
    uncheck: async () => { selected.delete(key); changes.push([...selected]) } })
  const pauses = []
  await probe.performNaturalAnswer(option('first'), option('second'), 'checkbox', {
    dwellSeconds: 100, modify: true, pause: async seconds => pauses.push(seconds), timed: async (_action, operation) => operation(),
  })
  assert.deepEqual(changes, [['first'], ['first', 'second'], ['second']])
  assert.ok(changes.every(answer => answer.length > 0))
  assert.equal(pauses.reduce((sum, value) => sum + value, 0), 100)
})

test('natural fixture explicitly binds two browser accounts within a larger cohort', () => {
  const fixture = { usernames: ['one', 'two', 'three', 'four'], browser_usernames: ['one', 'two'], password: 'private', task_id: 4 }
  assert.deepEqual(probeAccounts(fixture).map(row => row.username), ['one', 'two'])
  assert.throws(() => probeAccounts({ ...fixture, browser_usernames: ['one', 'outside'] }))
  assert.throws(() => probeAccounts({ ...fixture, browser_usernames: ['one', 'one'] }))
})

test('natural plan keeps the frozen dwell and sparse excursions for each explicit browser account', () => {
  assert.equal(typeof probe.naturalBrowserPlans, 'function')
  const students = ['one', 'two'].map(username => ({ username, role: 'browser', arrival_offset_seconds: 5,
    speed_stratum: 'fast', item_dwell_seconds: Array(20).fill(45), modify_items: [3, 9], excursion_items: [5, 15], optional_tool_items: [] }))
  const plan = { workload_version: 'natural-180-v1', item_count: 20, task_id: 4, students }
  assert.deepEqual(probe.naturalBrowserPlans(plan, [{ username: 'one', taskId: 4 }, { username: 'two', taskId: 4 }]), students)
  assert.throws(() => probe.naturalBrowserPlans({ ...plan, students: [students[0]] }, [{ username: 'one', taskId: 4 }, { username: 'two', taskId: 4 }]))
})

test('browser architecture answers require an observed final ACK and retain witnessed state hashes', () => {
  assert.equal(typeof probe.buildBrowserArchitectureSessions, 'function')
  const report = { probes: [{ completed: true, response_states: [{ client_instance_id: 'client', client_sequence_no: 1,
    question_type: 'short_answer', state_sha256: 'a'.repeat(64) }], protocol_witness: { session_ids: [7], requests: [
    { client_instance_id: 'client', acknowledgements: [{ client_sequence_no: 1, event_id: 'event' }] },
    { path: '/api/v1/student/attempts/9/submit/', status: 200, submission: { token: 'token' },
      receipt: { committed: true, final_response_event_id: 'event', response_version: 2 } },
  ] } }] }
  const sessions = probe.buildBrowserArchitectureSessions(report, [{ username: 'one' }])
  assert.equal(sessions['7'].complete, true)
  assert.equal(sessions['7'].answers['9'].state_sha256, 'a'.repeat(64))
  assert.equal(sessions['7'].answers['9'].target_event_id, 'event')
  report.probes[0].protocol_witness.requests[0].acknowledgements = []
  assert.equal(Object.keys(probe.buildBrowserArchitectureSessions(report, [{ username: 'one' }])['7'].answers).length, 0)
})

test('navigation waits for href restoration when a busy anchor has lost its link role', async () => {
  const browser = await chromium.launch()
  try {
    const page = await browser.newPage()
    await page.setContent('<nav class="kt-assessment-nav"><a data-nav-assignment="12" aria-disabled="true">Next</a></nav>')
    assert.equal(await page.locator('a').isEnabled(), true)
    const link = probe.navigableQuestionLink(page, '12')
    await assert.rejects(probeExpect(link).toBeEnabled({ timeout: 100 }))
    await page.evaluate(() => {
      const anchor = globalThis.document.querySelector('a')
      anchor.href = '/next/'
      anchor.removeAttribute('aria-disabled')
      anchor.addEventListener('click', event => { event.preventDefault(); anchor.dataset.clicked = 'true' })
    })
    await probeExpect(link).toBeEnabled({ timeout: 1000 })
    await link.click({ timeout: 1000 })
    assert.equal(await link.getAttribute('data-clicked'), 'true')
  } finally { await browser.close() }
})

test('final readonly storage evidence precedes observer settlement and unknown storage cannot pass the probe', async () => {
  assert.equal(typeof probe.settleProbeEvidence, 'function')
  for (const complete of [true, false]) {
    const order = []
    const page = { evaluate: async () => { order.push('readonly-snapshot'); return { read_complete: complete, scope_complete: complete, status: complete ? 'observed' : 'unknown' } } }
    const protocol = { evidence: { session_ids: [7], requests: [] }, settle: async () => { order.push('network-settle'); protocol.evidence.settlement = { complete: true } } }
    const waterfall = { settle: async () => { order.push('waterfall-settle'); return { complete: true } } }
    const report = { completed: true, actions: [{ duration_ms: 35000 }] }
    await probe.settleProbeEvidence(page, report, protocol, waterfall)
    assert.equal(order[0], 'readonly-snapshot')
    assert.equal(report.flow_completed, true)
    assert.equal(report.completed, complete)
    assert.equal(report.evidence_complete, complete)
    assert.equal(protocol.evidence.local_storage.status, complete ? 'observed' : 'unknown')
    assert.equal(report.actions[0].duration_ms, 35000)
  }
})

test('the task action uses the real start button and waits for the first interactive question', async () => {
  assert.equal(typeof probe.startFixtureTask, 'function')
  const order = []
  const button = { click: async () => { order.push('click') } }
  const page = {
    waitForResponse: async (predicate) => {
      assert.equal(predicate({ url: () => 'https://host/api/v1/student/courses/7/tasks/' }), true)
      return { ok: () => true, json: async () => ({ data: { items: [{ task_id: 4, experiment_name: 'fixture', primary_action: 'start' }] }, meta: { request_id: 'test' } }) }
    },
    goto: async (url) => { assert.equal(url, '/student/courses/7/'); order.push('task-list') },
    locator: () => ({ filter: () => ({ getByRole: () => button }) }),
    getByRole: () => 'fixture heading',
  }
  const ready = async () => { order.push('ready') }
  const operation = await probe.startFixtureTask(page, { courseId: 7, taskId: 4 }, ready)
  assert.deepEqual(order, ['task-list'])
  await operation()
  assert.deepEqual(order, ['task-list', 'click', 'ready'])
})

test('a previously started fixture follows the server resume action and labels the timing as resume', async () => {
  let clicked = false
  let ready = false
  const page = {
    waitForResponse: async () => ({ ok: () => true, json: async () => ({ data: { items: [{
      task_id: 4, experiment_name: 'fixture', primary_action: 'resume',
    }] } }) }),
    goto: async () => {},
    getByRole: () => 'fixture heading',
    locator: () => ({ filter: () => ({ getByRole: (_role, options) => {
      assert.equal(options.name, '继续作答')
      return { click: async () => { clicked = true } }
    } }) }),
  }
  const operation = await probe.startFixtureTask(page, { courseId: 7, taskId: 4 }, async () => { ready = true })
  assert.equal(operation.entryAction, 'resume')
  await operation()
  assert.equal(clicked && ready, true)
})

test('completion readiness waits for the rendered summary and the authoritative completed state', async () => {
  assert.equal(typeof probe.waitForSessionCompletion, 'function')
  const order = []
  const page = {
    locator: (selector) => { assert.equal(selector, '.kt-session-summary'); return 'summary' },
    request: { get: async (url) => {
      order.push('recovery')
      assert.equal(url, '/api/v1/student/tasks/4/recovery/')
      return { ok: () => true, json: async () => ({ data: { status: 'completed' }, meta: { request_id: 'test' } }) }
    } },
  }
  assert.equal(await probe.waitForSessionCompletion(page, 4, async (locator) => {
    assert.equal(locator, 'summary')
    order.push('summary')
  }), true)
  assert.deepEqual(order, ['summary', 'recovery'])
  page.request.get = async () => ({ ok: () => true, json: async () => ({ data: { status: 'in_progress' }, meta: { request_id: 'test' } }) })
  await assert.rejects(probe.waitForSessionCompletion(page, 4, async () => {}), /not completed/)
})

test('failed actions keep their full waiting time and failure outcome', async () => {
  assert.equal(typeof probe.timedProbeAction, 'function')
  const report = { actions: [] }
  await assert.rejects(probe.timedProbeAction(report, performance.now(), 'navigation_next_ready', 1,
    async () => { throw new Error('failed navigation') }))
  assert.equal(report.actions.length, 1)
  assert.equal(report.actions[0].completed, false)
  assert.ok(report.actions[0].duration_ms >= 0)
})

test('navigation splits enabled waiting without moving the user action start or enlarging its budget', async () => {
  assert.equal(typeof probe.navigationProbeAction, 'function')
  let elapsed = 0
  const report = { actions: [] }
  const timeouts = []
  await probe.navigationProbeAction(report, 0, 'navigation_next_ready', 1, {
    enabled: async timeout => { timeouts.push(timeout); elapsed += 31000 },
    click: async timeout => { timeouts.push(timeout); elapsed += 1000 },
    target: async timeout => { timeouts.push(timeout); elapsed += 1000 },
    ready: async timeout => { timeouts.push(timeout); elapsed += 500 },
  }, { now: () => elapsed })
  assert.deepEqual(timeouts, [35000, 4000, 3000, 2000])
  assert.equal(report.actions[0].duration_ms, 33500)
  assert.equal(report.actions[0].segments[0].duration_ms, 31000)
  assert.equal(report.actions[0].segments[0].phase, 'waiting_navigation_enabled')
  assert.equal(report.actions[0].completed, true)
})

test('navigation enabled timeout retains the entire failed duration and never dispatches a forced click', async () => {
  assert.equal(typeof probe.navigationProbeAction, 'function')
  let elapsed = 0
  let clicks = 0
  const report = { actions: [] }
  await assert.rejects(probe.navigationProbeAction(report, 0, 'navigation_next_ready', 1, {
    enabled: async () => { elapsed = 35000; throw new Error('PRIVATE MESSAGE') },
    click: async () => { clicks++ }, target: async () => {}, ready: async () => {},
  }, { now: () => elapsed }))
  assert.equal(clicks, 0)
  assert.equal(report.actions[0].duration_ms, 35000)
  assert.equal(report.actions[0].failure_phase, 'waiting_navigation_enabled')
  assert.equal(report.actions[0].completed, false)
  assert.doesNotMatch(JSON.stringify(report), /PRIVATE/)
})

test('input timing reports only the elapsed frame boundary and excludes the answer', () => {
  let listener
  let frame
  const timings = []
  const originals = Object.fromEntries(['document', 'requestAnimationFrame', '__ktProbeTiming'].map((key) => [key, globalThis[key]]))
  try {
    globalThis.document = { addEventListener: (_type, handler) => { listener = handler } }
    globalThis.requestAnimationFrame = (callback) => { frame = callback }
    globalThis.__ktProbeTiming = (row) => { timings.push(row) }
    probe.installInputFrameTiming()
    listener({ target: { value: 'PRIVATE ANSWER', matches: (selector) => selector === '[name="answer"]' } })
    frame()
    assert.equal(timings.length, 1)
    assert.equal(timings[0].stage, 'input_frame')
    assert.ok(timings[0].duration_ms >= 0)
    assert.ok(!JSON.stringify(timings).includes('PRIVATE'))
  } finally {
    for (const [key, value] of Object.entries(originals)) {
      if (value === undefined) delete globalThis[key]
      else globalThis[key] = value
    }
  }
})

test('browser readiness tolerates a response beyond the default five second assertion limit', async () => {
  const started = Date.now()
  await probeExpect.poll(() => Date.now() - started, { intervals: [250] }).toBeGreaterThan(5100)
})

test('uses three unique fixture accounts without exporting identities', () => {
  const accounts = probeAccounts({ usernames: ['one', 'two', 'three'], password: 'private', task_id: 4 })
  assert.equal(accounts.length, 3)
  assert.equal(accounts[0].taskId, 4)
  assert.throws(() => probeAccounts({ usernames: ['one', 'one', 'three'], password: 'private', task_id: 4 }))
})

test('waterfall endpoint labels omit IDs, queries and unknown private paths', () => {
  assert.equal(safeEndpoint('https://host/api/v1/student/sessions/17/questions/23/?navigation_id=secret'), '/api/v1/student/sessions/:id/questions/:id/')
  assert.equal(safeEndpoint('https://host/assessment/process/capture-v2/attempts/23/lease/acquire/'), '/assessment/process/capture-v2/attempts/:id/lease/acquire/')
  assert.equal(safeEndpoint('https://host/private/student-name'), 'other')
})

test('visual capture restores desktop dimensions before timed actions even after failure', async () => {
  const viewports = []
  const page = {
    async setViewportSize(size) { viewports.push(size) },
    async evaluate() { return { viewport_width: 1440, document_width: 1440, overflow: false } },
    async screenshot() { throw new Error('capture failed') },
  }
  await assert.rejects(captureVisualLayouts(page, 'unused'))
  assert.deepEqual(viewports.at(-1), { width: 1440, height: 900 })
  assert.equal(viewports.length, 2)
})

const requestId = '11111111111141118111111111111111'
const responseId = '22222222222242228222222222222222'
const operationId = '33333333-3333-4333-8333-333333333333'

function waterfallFixture({ code, requestHeaders = {}, responseHeaders = {}, body, parseFailure = false } = {}) {
  const handlers = {}
  const report = { waterfall: [] }
  let phase = 'navigation_next_ready'
  const page = { on: (event, handler) => { handlers[event] = handler } }
  const request = {
    headers: () => requestHeaders,
    url: () => 'https://host/student/attempts/23/save-draft/',
    method: () => 'POST', resourceType: () => 'fetch',
  }
  const response = {
    request: () => request, status: () => 409, headers: () => responseHeaders,
    json: async () => {
      if (parseFailure) throw new Error('PRIVATE ERROR MESSAGE')
      return body || { error: { code, message: 'PRIVATE ERROR MESSAGE', fields: { answer: 'PRIVATE ANSWER' } }, username: 'PRIVATE ACCOUNT' }
    },
  }
  return { page, report, handlers, request, response, getPhase: () => phase, setPhase: value => { phase = value } }
}

test('response diagnostics retain correlation and action phase without private body fields', async () => {
  assert.equal(typeof probe.installProbeWaterfall, 'function')
  const fixture = waterfallFixture({ code: 'writer_lease_conflict',
    requestHeaders: { 'x-request-id': requestId, 'x-kt-operation-id': operationId },
    responseHeaders: { 'x-request-id': responseId },
  })
  const recorder = probe.installProbeWaterfall(fixture.page, fixture.report, performance.now(), fixture.getPhase)
  fixture.handlers.request(fixture.request)
  fixture.setPhase('research_tools')
  fixture.handlers.response(fixture.response)
  fixture.handlers.requestfinished(fixture.request)
  await recorder.settle()
  const [row] = fixture.report.waterfall
  assert.equal(row.status, 409)
  assert.equal(row.request_id, requestId)
  assert.equal(row.response_request_id, responseId)
  assert.equal(row.operation_id, operationId)
  assert.equal(row.phase, 'navigation_next_ready')
  assert.equal(row.response_phase, 'research_tools')
  assert.equal(row.error_code, 'writer_lease_conflict')
  assert.equal(row.endpoint, '/student/attempts/:id/save-draft/')
  assert.ok(row.duration_ms >= 0)
  assert.ok(!JSON.stringify(row).includes('PRIVATE'))
})

for (const code of ['RESPONSE_VERSION_CONFLICT', 'response_version_conflict', 'navigation_response_version_conflict', 'writer_lease_conflict', 'navigation_material_conflict', 'ENTRY_MATERIAL_CONFLICT', 'submission_material_conflict', 'task_terminated', 'TASK_TERMINATED']) {
  test(`409 ${code} is distinguished using its fixed business code and response request ID`, async () => {
    assert.equal(typeof probe.installProbeWaterfall, 'function')
    const fixture = waterfallFixture({ code, responseHeaders: { 'x-request-id': responseId, 'x-kt-operation-id': operationId } })
    const recorder = probe.installProbeWaterfall(fixture.page, fixture.report, performance.now(), fixture.getPhase)
    fixture.handlers.request(fixture.request)
    fixture.handlers.response(fixture.response)
    await recorder.settle()
    const [row] = fixture.report.waterfall
    assert.equal(row.request_id, responseId)
    assert.equal(row.response_request_id, responseId)
    assert.equal(row.operation_id, operationId)
    assert.equal(row.error_code, code)
    assert.equal(row.retry, undefined)
  })
}

test('unknown codes, non-JSON bodies and arbitrary identity headers are excluded', async () => {
  assert.equal(typeof probe.installProbeWaterfall, 'function')
  for (const options of [{ code: 'PRIVATE ACCOUNT' }, { parseFailure: true }, { body: { error: 'PRIVATE ERROR', code: 'TASK_TERMINATED', username: 'PRIVATE ACCOUNT' } }]) {
    const fixture = waterfallFixture({ ...options, responseHeaders: { 'x-request-id': 'PRIVATE ACCOUNT', 'x-kt-operation-id': 'PRIVATE ACCOUNT' } })
    const recorder = probe.installProbeWaterfall(fixture.page, fixture.report, performance.now(), fixture.getPhase)
    fixture.handlers.request(fixture.request)
    fixture.handlers.response(fixture.response)
    await recorder.settle()
    const [row] = fixture.report.waterfall
    assert.equal(row.error_code, options.body ? 'TASK_TERMINATED' : null)
    assert.equal(row.request_id, null)
    assert.equal(row.operation_id, null)
    assert.ok(!JSON.stringify(row).includes('PRIVATE'))
  }
})

test('waterfall finalization cannot hang on an interrupted response body', async () => {
  const fixture = waterfallFixture()
  let release
  fixture.response.json = () => new Promise(resolve => { release = resolve })
  const recorder = probe.installProbeWaterfall(fixture.page, fixture.report, performance.now(), fixture.getPhase)
  fixture.handlers.request(fixture.request)
  fixture.handlers.response(fixture.response)
  const settled = await Promise.race([recorder.settle(15), new Promise(resolve => globalThis.setTimeout(() => resolve(null), 100))])
  assert.notEqual(settled, null)
  assert.equal(settled.complete, false)
  const frozen = JSON.stringify(fixture.report)
  release({ error: { code: 'writer_lease_conflict' } })
  await Promise.resolve()
  assert.equal(JSON.stringify(fixture.report), frozen)
})

test('200 percent zoom requires both devicePixelRatio and innerWidth measurements', async () => {
  assert.equal(typeof probe.attemptBrowserZoom200, 'function')
  const presses = []
  const metrics = [{ device_pixel_ratio: 1, inner_width: 1440, inner_height: 900 }, { device_pixel_ratio: 2, inner_width: 720, inner_height: 450 }]
  const page = { keyboard: { press: async key => presses.push(key) }, evaluate: async () => metrics.shift() }
  const zoom = await probe.attemptBrowserZoom200(page)
  assert.equal(zoom.status, 'verified')
  assert.equal(zoom.device_pixel_ratio_ratio, 2)
  assert.equal(zoom.inner_width_ratio, 2)
  assert.equal(presses.filter(key => key.endsWith('+Equal')).length, 5)
})

test('headless zoom shortcuts with unchanged metrics remain unsupported and fail visual acceptance', async () => {
  assert.equal(typeof probe.attemptBrowserZoom200, 'function')
  const viewportSizes = []
  let screenshots = 0
  const page = {
    keyboard: { press: async () => {} },
    setViewportSize: async size => viewportSizes.push(size),
    evaluate: async () => ({ device_pixel_ratio: 1, inner_width: 1440, inner_height: 900, viewport_width: 1440, document_width: 1440, overflow: false }),
    screenshot: async () => { screenshots++ },
  }
  assert.equal((await probe.attemptBrowserZoom200(page)).status, 'unsupported')
  const visual = await captureVisualLayouts(page, 'unused')
  assert.equal(visual.passed, false)
  assert.equal(visual.layouts.at(-1).status, 'unsupported')
  assert.ok(!viewportSizes.some(size => size.width === 720))
  assert.equal(screenshots, 2)
  assert.ok(!JSON.stringify(visual).includes('equivalent'))
})

test('extension zoom requires getZoom confirmation as well as independent window measurements', async () => {
  const factors = []
  const metrics = [{ device_pixel_ratio: 1, inner_width: 1440, inner_height: 900 }, { device_pixel_ratio: 2, inner_width: 720, inner_height: 450 }]
  const page = { evaluate: async () => metrics.shift() }
  const zoom = await probe.attemptBrowserZoom200(page, async factor => { factors.push(factor); return factor })
  assert.equal(zoom.status, 'verified')
  assert.equal(zoom.browser_reported_zoom, 2)
  assert.match(zoom.method, /chrome.tabs.setZoom/)
  assert.deepEqual(factors, [1, 2])
})

test('natural visual precheck captures one real 200 percent screenshot and restores zoom', async () => {
  let factor = 1
  const shots = []
  const page = { setViewportSize: async () => {}, evaluate: async () => ({ device_pixel_ratio: factor,
    inner_width: 1440 / factor, inner_height: 900 / factor, viewport_width: 1440 / factor,
    document_width: 1440 / factor, overflow: false }), screenshot: async options => shots.push(options.path) }
  const visual = await captureVisualLayouts(page, 'unused', async zoom => { factor = zoom; return factor }, { onlyZoom: true })
  assert.equal(visual.passed, true)
  assert.equal(shots.length, 1)
  assert.equal(visual.layouts[0].label, 'zoom200')
  assert.equal(factor, 1)
})

test('visual extension capture confirms real zoom then restores the original browser factor', async () => {
  let factor = 1
  let viewport = { width: 1440, height: 900 }
  const screenshots = []
  const page = {
    setViewportSize: async size => { viewport = size },
    evaluate: async () => ({ device_pixel_ratio: factor, inner_width: viewport.width / factor,
      inner_height: viewport.height / factor, viewport_width: viewport.width / factor,
      document_width: viewport.width / factor, overflow: false }),
    screenshot: async options => { screenshots.push(options.path) },
  }
  const visual = await captureVisualLayouts(page, 'unused', async zoom => { factor = zoom; return factor })
  assert.equal(visual.passed, true)
  assert.equal(visual.zoom.status, 'verified')
  assert.equal(visual.zoom.reset_status, 'verified')
  assert.equal(visual.layouts.at(-1).viewport_width, 720)
  assert.equal(screenshots.length, 3)
  assert.equal(factor, 1)
  assert.deepEqual(viewport, { width: 1440, height: 900 })
})
