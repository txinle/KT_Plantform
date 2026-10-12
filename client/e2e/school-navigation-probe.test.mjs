import test from 'node:test'
import assert from 'node:assert/strict'
import { performance } from 'node:perf_hooks'
import { probeAccounts, safeEndpoint, captureVisualLayouts, probeExpect } from './school-navigation-probe.mjs'
import * as probe from './school-navigation-probe.mjs'
import { chromium } from '@playwright/test'

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
