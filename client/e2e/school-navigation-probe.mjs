import { existsSync, mkdirSync, mkdtempSync, readFileSync, renameSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'
import { parseArgs } from 'node:util'
import process from 'node:process'
import { chromium, expect as baseExpect } from '@playwright/test'
import { observeBrowserProtocol } from './browser-network-witness.mjs'
import { captureBrowserStorage } from './browser-storage-witness.mjs'
import { digest } from './capture-protocol-witness.mjs'

export const probeExpect = baseExpect.configure({ timeout: 35000 })
const expect = probeExpect

export function firstLoginPost(report) {
  const row = report.waterfall.find(request => request.endpoint === '/api/v1/auth/login/' && request.method === 'POST')
  return row ? { username_sha256: report.username_sha256, sent_at: row.sent_at, http_status: row.status,
    successful: row.status >= 200 && row.status < 300 && !row.failed, request_id: row.request_id } : null
}

export async function performLoginOnly(page, account, report, {
  startAt, pause, timed, now = () => Date.now() / 1000,
  waitForHome = async () => {
    await expect(page).toHaveURL(/\/student\/$/)
    await page.waitForLoadState('domcontentloaded')
    await expect(page.locator('[data-vue-shell]')).toBeVisible()
    await expect(page.locator(`a[href="/student/courses/${account.courseId}/"]`).first()).toBeVisible()
  },
}) {
  report.mode = 'login-only'; report.started_at = now(); report.planned_arrival_at = startAt
  report.submitted_count = 0; report.session_completed = false
  const preparationStarted = globalThis.performance.now()
  report.auth_preparation_started_at = now()
  await page.goto('/login/')
  await page.getByLabel('账号或学号').fill(account.username)
  await page.getByLabel('密码').fill(account.password)
  report.auth_preparation_completed_at = now()
  report.auth_preparation_seconds = (globalThis.performance.now() - preparationStarted) / 1000
  report.login_measurement_boundary = 'Shared login POST release through authentication/home; auth preparation and release waiting excluded'
  await pause(Math.max(0, startAt - now()))
  report.actual_arrival_at = now()
  await timed('task_list', async () => {
    await timed('login', async () => {
      const authentication = page.waitForResponse(response => response.url().endsWith('/api/v1/auth/login/') && response.request().method() === 'POST')
      await page.getByRole('button', { name: '登录', exact: true }).click()
      const response = await authentication
      if (!response.ok()) throw new Error('Login-only authentication failed')
      if (await response.finished()) throw new Error('Login-only authentication response did not complete')
      report.authenticated_at = now()
    })
    await waitForHome()
    report.home_ready_at = now()
  })
  report.completed = true; report.completed_at = now()
}

export function navigableQuestionLink(page, assignment) {
  return page.locator(`.kt-assessment-nav [data-nav-assignment="${assignment}"][href]:not([aria-disabled="true"])`)
}

export function questionNavigationSteps(page, assignment, targetPath, ready, {
  mobile = false, now = () => globalThis.performance.now(),
  waitVisible = (locator, timeout) => expect(locator).toBeVisible({ timeout }),
  waitEnabled = (locator, timeout) => expect(locator).toBeEnabled({ timeout }),
  waitTarget = (path, timeout) => expect(page).toHaveURL(url => url.pathname === path, { timeout }),
} = {}) {
  const drawer = mobile ? page.locator('#assessment-mobile-navigation') : null
  const link = mobile ? drawer.locator(`a[href="${targetPath}"]:not([aria-disabled="true"])`) : navigableQuestionLink(page, assignment)
  return {
    enabled: async timeout => {
      const started = now()
      const remaining = () => {
        const value = timeout - (now() - started)
        if (value <= 0) throw new Error('Navigation action budget exhausted')
        return value
      }
      if (mobile) {
        await page.locator('[data-mobile-navigation-trigger]').click({ timeout: remaining() })
        await waitVisible(drawer, remaining())
      }
      await waitEnabled(link, remaining())
    },
    click: timeout => link.click({ timeout }),
    target: timeout => waitTarget(targetPath, timeout),
    ready,
  }
}

export async function settleProbeEvidence(page, report, protocol, waterfall) {
  report.flow_completed = report.completed
  protocol.evidence.local_storage = await captureBrowserStorage(page, protocol.evidence)
  const [waterfallSettlement] = await Promise.all([waterfall.settle(), protocol.settle()])
  report.waterfall_settlement = waterfallSettlement
  const local = protocol.evidence.local_storage
  report.evidence_complete = local.read_complete === true && local.scope_complete === true && local.status === 'observed'
    && protocol.evidence.settlement?.complete === true && waterfallSettlement.complete === true
  report.completed = report.flow_completed && report.evidence_complete
}

export function probeAccounts(manifest) {
  const names = manifest.browser_usernames ?? manifest.usernames
  const count = manifest.browser_usernames ? 2 : 3
  if (!Array.isArray(manifest.usernames) || !Array.isArray(names) || names.length !== count || new Set(names).size !== count
    || manifest.usernames.some((value) => typeof value !== 'string' || !value)
    || names.some(value => !manifest.usernames.includes(value))
    || typeof manifest.password !== 'string' || !manifest.password || !Number.isInteger(manifest.task_id)) throw new Error('Three unique fixture accounts and a task are required')
  return names.map((username) => ({ username, password: manifest.password, taskId: manifest.task_id, courseId: manifest.course_id }))
}

export function naturalBrowserPlans(plan, accounts) {
  if (plan.workload_version !== 'natural-180-v1' || plan.item_count !== 20 || !Array.isArray(plan.students)) throw new Error('Frozen natural workload plan required')
  return accounts.map(account => {
    const rows = plan.students.filter(row => row.username === account.username && row.role === 'browser')
    const row = rows[0]
    const range = { fast: [35, 55], medium: [45, 75], slow: [65, 95] }[row?.speed_stratum]
    if (rows.length !== 1 || plan.task_id !== account.taskId || !range
      || !Number.isFinite(row.arrival_offset_seconds) || row.arrival_offset_seconds < 0 || row.arrival_offset_seconds > 60
      || !Array.isArray(row.item_dwell_seconds) || row.item_dwell_seconds.length !== 20
      || row.item_dwell_seconds.some(value => !Number.isFinite(value) || value < range[0] || value > range[1])
      || ['modify_items', 'excursion_items', 'optional_tool_items'].some(key => !Array.isArray(row[key])
        || ![0, 2].includes(row[key].length) || new Set(row[key]).size !== row[key].length
        || row[key].some(item => !Number.isInteger(item) || item < 0 || item > (key === 'excursion_items' ? 18 : 19)))) throw new Error('Browser account plan is missing or invalid')
    if (plan.scenario === 'confidence-only-v1') {
      const scale = plan.confidence_scale
      if (!scale || row.optional_tool_items.length || ['confidence_actions', 'confidence_values', 'confidence_think_seconds']
        .some(key => !Array.isArray(row[key]) || row[key].length !== 20)
        || row.confidence_actions.some((action, item) => !['fill', 'skip'].includes(action)
          || action === 'skip' && row.confidence_values[item] !== null
          || action === 'fill' && (!Number.isInteger(row.confidence_values[item]) || row.confidence_values[item] < scale.minimum
            || row.confidence_values[item] > scale.maximum || (row.confidence_values[item] - scale.minimum) % scale.step !== 0))
        || row.confidence_think_seconds.some(seconds => !Number.isFinite(seconds) || seconds < 0)) throw new Error('Frozen confidence-only plan is invalid')
    }
    return row
  })
}

export async function resolvePlannedConfidence(page, selection, { pause, timed, recordThink = () => {}, now = () => Date.now() / 1000 }) {
  const started = now()
  let completed = false
  try { await pause(selection.thinkSeconds); completed = true }
  finally {
    const ended = now()
    recordThink({ started_at: started, completed_at: ended, planned_seconds: selection.thinkSeconds,
      actual_seconds: ended - started, completed })
  }
  await timed(selection.action === 'fill' ? 'confidence_input_ready' : 'confidence_skip_ready', async () => {
    if (selection.action === 'fill') {
      await page.locator('[data-confidence-number]').fill(String(selection.value))
      await page.locator('[data-confidence-submit]').click()
    } else await page.getByRole('button', { name: '跳过评价', exact: true }).click()
  })
}

export async function performSubmissionPhases({ ack, ready, timed, last, timeoutMs = 35000, now = () => globalThis.performance.now() }) {
  return timed(last ? 'finish_ready' : 'submit_next_ready', async () => {
    const started = now()
    const receipt = await timed('submit_ack', ack)
    const remaining = timeoutMs - (now() - started)
    if (remaining <= 0) throw Object.assign(new Error('Submission operation budget exhausted'), { name: 'TimeoutError' })
    await timed(last ? 'last_session_ready' : 'next_question_ready', () => ready(receipt, remaining))
    return receipt
  })
}

export async function waitForSubmittedDestination(page, targetPath, ready, {
  report, sequence, stopFile, timeoutMs = 35000, now = () => globalThis.performance.now(),
}) {
  const started = now()
  let attempts = 0
  const observation = { sequence, expected_path: targetPath, observed_path: null,
    stage: 'destination', completed: false, elapsed_ms: 0 }
  ;(report.destination_waits ||= []).push(observation)
  const remaining = () => {
    if (stopFile && existsSync(stopFile)) throw Object.assign(new Error('Explicit stop file observed'), { name: 'StoppedError' })
    const budget = timeoutMs - (now() - started)
    if (budget <= 0) throw Object.assign(new Error('Submission operation budget exhausted'), { name: 'TimeoutError' })
    return budget
  }
  try {
  while (new globalThis.URL(page.url()).pathname !== targetPath) {
    remaining()
    const retry = page.locator('[data-retry-close]')
    const enabled = attempts < 3 && await retry.isVisible()
      && await retry.isEnabled({ timeout: Math.min(100, remaining()) }).catch(error => {
        if (error.name === 'TimeoutError') return false
        throw error
      })
    if (enabled) {
      const row = { sequence, attempt: ++attempts, completed: false }
      ;(report.close_recovery_actions ||= []).push(row)
      await retry.click({ timeout: remaining() })
      row.completed = true
      if (new globalThis.URL(page.url()).pathname === targetPath) break
    }
    await page.waitForTimeout(Math.min(100, remaining()))
  }
  observation.stage = 'interactive'
  await ready(remaining())
  if (now() - started > timeoutMs) throw Object.assign(new Error('Submission operation budget exhausted'), { name: 'TimeoutError' })
  observation.completed = true
  } finally {
    observation.observed_path = new globalThis.URL(page.url()).pathname
    observation.elapsed_ms = now() - started
  }
}

export async function performNaturalAnswer(input, secondOption, type, { dwellSeconds, modify, pause, timed }) {
  const choice = ['radio', 'checkbox'].includes(type)
  const answer = type === 'number' ? '42' : '浏览器探针作答'
  if (!choice) {
    await pause(dwellSeconds * .3)
    await timed('answer_input_segment', () => input.fill(type === 'number' ? '4' : answer.slice(0, 3)))
    await pause(dwellSeconds * .3)
  } else await pause(dwellSeconds * .6)
  await timed('answer_input', async () => {
    if (choice) await input.check()
    else await input.fill(answer)
  })
  if (modify) {
    await pause(dwellSeconds * .15)
    await timed('answer_modify', async () => {
      if (type === 'checkbox') { await secondOption.check(); await input.uncheck() }
      else if (type === 'radio') await secondOption.check()
      else await input.fill(type === 'number' ? '5' : '浏览器探针修改作答')
    })
    await pause(dwellSeconds * .25)
  } else await pause(dwellSeconds * .4)
}

export function browserResponseStates(body) {
  const bundle = body?.atomic_submission ?? body?.save_and_leave ?? body?.capture_bundle ?? body
  return (bundle?.events || []).filter(event => event.event_type === 'response_transition'
    && typeof event.payload?.after_state_hash === 'string' && typeof event.payload?.after_state?.question_type === 'string')
    .map(event => ({ client_instance_id: bundle.client_instance_id, client_sequence_no: event.client_sequence_no,
      question_type: event.payload.after_state.question_type, state_sha256: event.payload.after_state_hash }))
}

export function buildBrowserArchitectureSessions(report, accounts) {
  const sessions = {}
  report.probes.forEach((probe, index) => {
    const evidence = probe.protocol_witness
    if (evidence?.session_ids?.length !== 1) return
    const states = new Map((probe.response_states || []).map(row => [`${row.client_instance_id}:${row.client_sequence_no}`, row]))
    const acknowledged = new Map()
    for (const request of evidence.requests) for (const ack of request.acknowledgements || []) {
      const state = states.get(`${request.client_instance_id}:${ack.client_sequence_no}`)
      if (state) acknowledged.set(ack.event_id, state)
    }
    const answers = {}
    for (const request of evidence.requests) {
      const attempt = /^\/api\/v1\/student\/attempts\/(\d+)\/submit\/$/.exec(request.path || '')
      const state = acknowledged.get(request.receipt?.final_response_event_id)
      if (!attempt || !state || !request.receipt?.committed || request.status < 200 || request.status >= 300) continue
      answers[attempt[1]] = { question_type: state.question_type, state_sha256: state.state_sha256,
        target_event_id: request.receipt.final_response_event_id, submission_token: request.submission.token,
        response_version: request.receipt.response_version }
    }
    sessions[String(evidence.session_ids[0])] = { username_sha256: digest(accounts[index].username), complete: probe.completed === true, answers }
  })
  return sessions
}

export function safeEndpoint(url) {
  const path = new globalThis.URL(url).pathname
  return /^\/(api\/v1\/(student|auth|assessment)\/|assessment\/process\/|student\/(attempts|sessions)\/)/.test(path)
    ? path.replace(/\b\d+\b/g, ':id') : 'other'
}

const diagnosticCodes = new Set([
  'RESPONSE_VERSION_CONFLICT', 'response_version_conflict', 'navigation_response_version_conflict',
  'navigation_response_conflict', 'writer_lease_conflict', 'navigation_material_conflict',
  'submission_material_conflict', 'ENTRY_MATERIAL_CONFLICT', 'task_terminated', 'TASK_TERMINATED',
])
const correlationId = value => typeof value === 'string'
  && /^(?:[a-f0-9]{32}|[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})$/i.test(value) ? value : null

export function installProbeWaterfall(page, report, origin, currentPhase) {
  const requests = new Map()
  const pendingResponses = new Set()
  let closed = false
  page.on('request', (request) => {
    if (closed) return
    const headers = request.headers()
    const row = { endpoint: safeEndpoint(request.url()), method: request.method(), resource_type: request.resourceType(), start_ms: globalThis.performance.now() - origin,
      sent_at: Date.now() / 1000, request_id: correlationId(headers['x-request-id']), operation_id: correlationId(headers['x-kt-operation-id']), phase: currentPhase() }
    requests.set(request, row)
    report.waterfall.push(row)
  })
  page.on('requestfinished', (request) => {
    if (closed) return
    const row = requests.get(request)
    if (row) { row.end_ms = globalThis.performance.now() - origin; row.duration_ms = row.end_ms - row.start_ms; row.terminal_state = 'finished' }
  })
  page.on('requestfailed', (request) => {
    if (closed) return
    const row = requests.get(request)
    if (!row) return
    row.failed = true; row.terminal_state = 'failed'
    row.end_ms = globalThis.performance.now() - origin; row.duration_ms = row.end_ms - row.start_ms
    const errorText = request.failure()?.errorText
    row.failure_code = typeof errorText === 'string' && /^net::ERR_[A-Z_]+$/.test(errorText) ? errorText : 'transport_failure'
  })
  page.on('response', (response) => {
    if (closed) return
    const row = requests.get(response.request())
    if (!row) return
    row.status = response.status()
    row.response_phase = currentPhase()
    const headers = response.headers()
    row.response_request_id = correlationId(headers['x-request-id'])
    row.request_id ||= row.response_request_id
    row.operation_id ||= correlationId(headers['x-kt-operation-id'])
    row.error_code = null
    if (row.status < 400 || row.endpoint === 'other') return
    const pending = (async () => {
      try {
        const body = await response.json()
        if (closed) return
        const code = typeof body?.error === 'object' ? body.error?.code : body?.code
        if (diagnosticCodes.has(code)) row.error_code = code
      } catch { /* A non-JSON or interrupted error response has no safe business code. */ }
    })()
    pendingResponses.add(pending)
    void pending.finally(() => pendingResponses.delete(pending))
  })
  return { async settle(timeoutMs = 2000) {
    const deadline = globalThis.performance.now() + timeoutMs
    const unfinished = () => [...requests.values()].filter(row => !row.terminal_state)
    while ((pendingResponses.size || unfinished().length) && globalThis.performance.now() < deadline) {
      await new Promise(resolve => globalThis.setTimeout(resolve, Math.min(10, Math.max(0, deadline - globalThis.performance.now()))))
    }
    const unfinishedRequests = unfinished()
    for (const row of unfinishedRequests) row.terminal_state = 'not_observed'
    closed = true
    return { complete: pendingResponses.size === 0 && unfinishedRequests.length === 0,
      pending_response_count: pendingResponses.size, unfinished_request_count: unfinishedRequests.length, budget_ms: timeoutMs }
  } }
}

export async function timedProbeAction(report, origin, action, sequence, operation) {
  const started = globalThis.performance.now()
  let completed = false
  try {
    const result = await operation()
    completed = true
    return result
  } finally {
    report.actions.push({ action, sequence, start_ms: started - origin,
      duration_ms: globalThis.performance.now() - started, completed })
  }
}

export async function navigationProbeAction(report, origin, action, sequence, steps, { timeoutMs = 35000, now = () => globalThis.performance.now() } = {}) {
  const started = now()
  const row = { action, sequence, start_ms: started - origin, completed: false, segments: [], budget_ms: timeoutMs }
  try {
    for (const [key, phase] of [['enabled', 'waiting_navigation_enabled'], ['click', 'dispatch_navigation_click'],
      ['target', 'waiting_target_url'], ['ready', 'waiting_target_ready']]) {
      const began = now()
      const segment = { phase, start_ms: began - origin, completed: false }
      row.segments.push(segment)
      try {
        const remaining = timeoutMs - (began - started)
        if (remaining <= 0) throw new Error('Navigation action budget exhausted')
        await steps[key](remaining)
        if (now() - started > timeoutMs) throw new Error('Navigation action budget exhausted')
        segment.completed = true
      } catch (error) { row.failure_phase = phase; throw error }
      finally { segment.duration_ms = now() - began }
    }
    row.completed = true
  } finally { row.duration_ms = now() - started; report.actions.push(row) }
}

export function installInputFrameTiming() {
  globalThis.document.addEventListener('input', (event) => {
    if (!event.target?.matches?.('[name="answer"]')) return
    const started = globalThis.performance.now()
    globalThis.requestAnimationFrame(() => {
      void globalThis.__ktProbeTiming({ stage: 'input_frame', start_ms: started,
        duration_ms: Math.max(0, globalThis.performance.now() - started), outcome: 'success' })
    })
  }, true)
}

const zoomModifier = process.platform === 'darwin' ? 'Meta' : 'Control'

async function zoomMetrics(page) {
  return page.evaluate(async () => {
    await new Promise(resolve => globalThis.requestAnimationFrame(() => globalThis.requestAnimationFrame(resolve)))
    return { device_pixel_ratio: globalThis.devicePixelRatio, inner_width: globalThis.innerWidth, inner_height: globalThis.innerHeight }
  })
}

export async function createVisualProbeContext(baseURL) {
  const temporary = mkdtempSync(join(tmpdir(), 'kt-probe-visual-'))
  const extension = join(temporary, 'extension')
  mkdirSync(extension)
  writeFileSync(join(extension, 'manifest.json'), JSON.stringify({ manifest_version: 3,
    name: 'Temporary probe tab zoom', version: '1.0', permissions: ['tabs'], background: { service_worker: 'worker.js' } }))
  writeFileSync(join(extension, 'worker.js'), 'chrome.runtime.onInstalled.addListener(() => {});')
  let context
  try {
    context = await chromium.launchPersistentContext(join(temporary, 'profile'), {
      channel: 'chromium', headless: true, baseURL, viewport: null,
      args: [`--disable-extensions-except=${extension}`, `--load-extension=${extension}`, '--window-size=1440,900'],
    })
    const worker = context.serviceWorkers()[0] || await context.waitForEvent('serviceworker', { timeout: 10000 })
    return {
      context,
      setZoom: (page, factor) => worker.evaluate(async ({ url, zoom }) => {
        const tab = (await globalThis.chrome.tabs.query({})).find(tab => tab.url === url)
        if (!tab) throw new Error('Visual probe tab is unavailable')
        await globalThis.chrome.tabs.setZoom(tab.id, zoom)
        return globalThis.chrome.tabs.getZoom(tab.id)
      }, { url: page.url(), zoom: factor }),
      close: async () => { await context.close(); rmSync(temporary, { recursive: true, force: true }) },
    }
  } catch (error) {
    if (context) await context.close()
    rmSync(temporary, { recursive: true, force: true })
    throw error
  }
}

export async function attemptBrowserZoom200(page, setZoom) {
  const zoom = { status: 'unsupported', target: 2, method: setZoom
    ? 'MV3 chrome.tabs.setZoom with measured devicePixelRatio and innerWidth'
    : 'browser keyboard zoom with measured devicePixelRatio and innerWidth' }
  try {
    if (setZoom) await setZoom(1)
    else await page.keyboard.press(`${zoomModifier}+0`)
    zoom.before = await zoomMetrics(page)
    if (setZoom) zoom.browser_reported_zoom = await setZoom(2)
    else for (let step = 0; step < 5; step++) await page.keyboard.press(`${zoomModifier}+Equal`)
    zoom.after = await zoomMetrics(page)
    zoom.device_pixel_ratio_ratio = zoom.after.device_pixel_ratio / zoom.before.device_pixel_ratio
    zoom.inner_width_ratio = zoom.before.inner_width / zoom.after.inner_width
    if ((!setZoom || zoom.browser_reported_zoom === 2)
        && Math.abs(zoom.device_pixel_ratio_ratio - 2) <= 0.05 && Math.abs(zoom.inner_width_ratio - 2) <= 0.05) zoom.status = 'verified'
    else zoom.reason = 'browser_zoom_not_verified'
  } catch { zoom.reason = 'browser_zoom_control_unavailable' }
  return zoom
}

export async function captureFirstReadyLayout(page, outputDirectory, index, viewport) {
  const started = Date.now() / 1000
  const layout = await page.evaluate(() => {
    const documentWidth = Math.max(globalThis.document.documentElement.scrollWidth, globalThis.document.body.scrollWidth)
    return { viewport_width: globalThis.innerWidth, viewport_height: globalThis.innerHeight,
      document_width: documentWidth, overflow: documentWidth > globalThis.innerWidth + 1 }
  })
  const label = index === 0 ? 'desktop-1440' : 'mobile-390'
  const filename = `probe-${index + 1}-active-${label}.png`
  await page.screenshot({ path: join(outputDirectory, filename), fullPage: false })
  return { label, width: viewport.width, height: viewport.height, ...layout, filename, status: 'captured',
    captured_at: started, passed: !layout.overflow && layout.viewport_width === viewport.width }
}

export async function captureVisualLayouts(page, outputDirectory, setZoom, { onlyZoom = false } = {}) {
  const rows = []
  let zoom
  const layoutMetrics = () => page.evaluate(() => {
    globalThis.scrollTo(0, 0)
    const documentWidth = Math.max(globalThis.document.documentElement.scrollWidth, globalThis.document.body.scrollWidth)
    return { viewport_width: globalThis.innerWidth, document_width: documentWidth, overflow: documentWidth > globalThis.innerWidth + 1 }
  })
  try {
    for (const viewport of (onlyZoom ? [] : [
      { label: 'desktop-1440', width: 1440, height: 900 },
      { label: 'mobile-390', width: 390, height: 844 },
    ])) {
      await page.setViewportSize({ width: viewport.width, height: viewport.height })
      const layout = await layoutMetrics()
      const filename = `probe-1-active-${viewport.label}.png`
      await page.screenshot({ path: join(outputDirectory, filename), fullPage: true, animations: 'disabled' })
      rows.push({ ...viewport, ...layout, filename, status: 'captured' })
    }
    await page.setViewportSize({ width: 1440, height: 900 })
    zoom = await attemptBrowserZoom200(page, setZoom)
    if (zoom.status === 'verified') {
      const layout = await layoutMetrics()
      const filename = 'probe-1-active-zoom200.png'
      await page.screenshot({ path: join(outputDirectory, filename), fullPage: true, animations: 'disabled' })
      rows.push({ label: 'zoom200', width: 1440, height: 900, ...layout, filename, status: 'captured' })
    } else rows.push({ label: 'zoom200', status: 'unsupported', reason: zoom.reason })
  } finally {
    if (zoom) {
      try {
        if (setZoom) {
          zoom.browser_reported_reset = await setZoom(1)
          if (zoom.browser_reported_reset !== 1) zoom.reset_status = 'unsupported'
        } else await page.keyboard.press(`${zoomModifier}+0`)
      }
      catch { zoom.reset_status = 'unsupported' }
    }
    await page.setViewportSize({ width: 1440, height: 900 })
    if (zoom?.before && !zoom.reset_status) {
      const restored = await zoomMetrics(page)
      zoom.reset_status = Math.abs(restored.device_pixel_ratio / zoom.before.device_pixel_ratio - 1) <= 0.05
        && Math.abs(restored.inner_width / zoom.before.inner_width - 1) <= 0.05 ? 'verified' : 'unsupported'
    }
  }
  return { passed: rows.every(row => !row.overflow) && zoom.status === 'verified' && zoom.reset_status === 'verified',
    layouts: rows, zoom_method: zoom.method, zoom }
}

export async function waitForSessionCompletion(page, taskId, waitForSummary = async (locator, timeout) => { await expect(locator).toBeVisible({ timeout }) }, timeoutMs = 35000) {
  const started = globalThis.performance.now()
  await waitForSummary(page.locator('.kt-session-summary'), timeoutMs)
  const remaining = timeoutMs - (globalThis.performance.now() - started)
  if (remaining <= 0) throw Object.assign(new Error('Submission operation budget exhausted'), { name: 'TimeoutError' })
  const recovered = await page.request.get(`/api/v1/student/tasks/${taskId}/recovery/`, { timeout: remaining })
  if (!recovered.ok()) throw new Error('Fixture completion check failed')
  if ((await recovered.json()).data.status !== 'completed') throw new Error('Fixture session is not completed')
  return true
}

export async function waitForStudentHomeCourse(page, courseId) {
  await expect(page.locator(`a[href="/student/courses/${courseId}/"]`).first()).toBeVisible()
}

export async function startFixtureTask(page, account, ready) {
  if (!Number.isInteger(account.courseId)) throw new Error('Fixture course is required')
  const response = page.waitForResponse((row) => new globalThis.URL(row.url()).pathname === `/api/v1/student/courses/${account.courseId}/tasks/`)
  await page.goto(`/student/courses/${account.courseId}/`)
  const tasks = await response
  if (!tasks.ok()) throw new Error('Fixture task list failed')
  const task = (await tasks.json()).data.items.find((row) => row.task_id === account.taskId)
  if (!task) throw new Error('Fixture task was not authorized')
  if (!['start', 'resume'].includes(task.primary_action)) throw new Error('Fixture task has no answer entry action')
  const button = page.locator('.kt-student-task-row').filter({ has: page.getByRole('heading', { name: task.experiment_name, exact: true }) }).getByRole('button', { name: task.primary_action === 'resume' ? '继续作答' : '开始作业', exact: true })
  const operation = async () => {
    await button.click()
    await ready()
  }
  operation.entryAction = task.primary_action
  return operation
}

async function runAccount(browser, account, baseURL, index, itemLimit, visualOutput, candidate, natural = null) {
  const loginOnly = natural?.mode === 'login-only'
  const visualContext = visualOutput && index === 0 && !loginOnly ? await createVisualProbeContext(baseURL) : null
  const viewport = natural && index === 1 ? { width: 390, height: 844 } : { width: 1440, height: 900 }
  const context = visualContext?.context || await browser.newContext({ baseURL, viewport })
  const page = await context.newPage()
  page.setDefaultTimeout(35000)
  const origin = globalThis.performance.now()
  const report = { probe: index + 1, username_sha256: digest(account.username), viewport, started_at: null, ready_at: null, completed_at: null,
    completed: false, submitted_count: 0, session_completed: false, question_types: [], actions: [], waterfall: [], longtasks: [], runtime_timings: [], page_error_count: 0, response_states: [],
    input_latency_boundary: 'Answer input event dispatch through the next animation-frame callback; physical screen paint is not directly observed.' }
  let stage = 'login'
  let firstReadyVisual = null
  if (natural) { report.natural_plan = natural.plan; natural.publish(report) }
  const budgetTimer = natural ? globalThis.setTimeout(() => { report.total_timeout = true; void context.close() }, loginOnly ? 300000 : 2700000) : null
  await page.exposeBinding('__ktProbeLongtask', (_, task) => report.longtasks.push({ ...task, received_ms: globalThis.performance.now() - origin }))
  await page.exposeBinding('__ktProbeTiming', (_, timing) => report.runtime_timings.push(timing))
  await page.addInitScript(installInputFrameTiming)
  await page.addInitScript(() => {
    new globalThis.PerformanceObserver((list) => {
      for (const entry of list.getEntries()) void globalThis.__ktProbeLongtask({ start_ms: entry.startTime, duration_ms: entry.duration })
    }).observe({ type: 'longtask', buffered: true })
    new globalThis.PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        if (!entry.name.startsWith('kt-assessment:')) continue
        const detail = entry.detail || {}
        void globalThis.__ktProbeTiming({ stage: detail.stage, operation_id: detail.operation_id,
          outcome: detail.outcome, start_ms: entry.startTime, duration_ms: entry.duration })
      }
    }).observe({ type: 'measure', buffered: true })
  })
  page.on('pageerror', () => { report.page_error_count++ })
  const waterfall = installProbeWaterfall(page, report, origin, () => stage)
  const protocol = loginOnly ? null : observeBrowserProtocol(page, { candidate, taskId: account.taskId, probe: index + 1 })
  if (protocol) report.protocol_witness = protocol.evidence
  page.on('request', request => {
    if (request.method() !== 'POST') return
    let body
    try { body = request.postDataJSON() } catch { return }
    report.response_states.push(...browserResponseStates(body))
  })
  async function ready(timeoutMs = 35000) {
    const started = globalThis.performance.now()
    await expect(page.locator('[data-init-state="ready"]')).toBeVisible({ timeout: timeoutMs })
    await expect(page.locator('[name=answer]').first()).toBeEnabled({ timeout: Math.max(1, timeoutMs - (globalThis.performance.now() - started)) })
  }
  async function timed(action, sequence, operation) {
    stage = action
    return timedProbeAction(report, origin, action, sequence, async () => {
      if (!natural) return operation()
      let timer
      try {
        return await Promise.race([operation(), new Promise((_, reject) => {
          timer = globalThis.setTimeout(() => { const error = new Error('Operation exceeded 35 seconds'); error.name = 'TimeoutError'; reject(error) }, 35000)
        })])
      } finally { globalThis.clearTimeout(timer); natural.publish(report) }
    })
  }
  async function pause(seconds) {
    const deadline = Date.now() + seconds * 1000
    while (Date.now() < deadline) {
      if (natural?.stopFile && existsSync(natural.stopFile)) { const error = new Error('Explicit stop file observed'); error.name = 'StoppedError'; throw error }
      await page.waitForTimeout(Math.min(1000, deadline - Date.now()))
    }
  }
  async function navigation(action, sequence, assignment, targetPath) {
    stage = action
    await navigationProbeAction(report, origin, action, sequence,
      questionNavigationSteps(page, assignment, targetPath, ready, { mobile: viewport.width < 768 }))
  }
  try {
    if (loginOnly) {
      await performLoginOnly(page, account, report, { startAt: natural.startAt, pause,
        timed: (action, operation) => timed(action, 0, operation) })
      return report
    }
    if (natural) await pause(Math.max(0, natural.startAt + natural.plan.arrival_offset_seconds - Date.now() / 1000))
    report.started_at = Date.now() / 1000
    await timed('login', 0, async () => {
      await page.goto('/login/')
      await page.getByLabel('账号或学号').fill(account.username)
      await page.getByLabel('密码').fill(account.password)
      await page.getByRole('button', { name: '登录', exact: true }).click()
      await expect(page).toHaveURL(/\/student\/$/)
      await page.waitForLoadState('domcontentloaded')
      await expect(page.locator('[data-vue-shell]')).toBeVisible()
      await waitForStudentHomeCourse(page, account.courseId)
    })
    stage = 'research_consent'
    const consent = page.getByRole('dialog', { name: '研究参与说明与知情同意' })
    if (await consent.isVisible()) {
      await consent.getByRole('checkbox').check()
      await consent.getByRole('button', { name: '同意并继续', exact: true }).click()
      await expect(consent).toHaveCount(0)
    }
    stage = 'task_list'
    const startTask = await timed('task_list', 0, () => startFixtureTask(page, account, ready))
    await timed(startTask.entryAction === 'resume' ? 'resume_first_ready' : 'start_first_ready', 0, startTask)
    report.ready_at = Date.now() / 1000
    if (natural) natural.publish(report)
    if (natural && visualOutput) {
      firstReadyVisual = captureFirstReadyLayout(page, visualOutput, index, viewport)
        .then(row => { report.first_ready_visual = row })
        .catch(() => { report.first_ready_visual = { status: 'failed', passed: false } })
    }
    if (visualOutput && index === 0) {
      stage = 'visual_layout_capture'
      const started = globalThis.performance.now()
      if (firstReadyVisual) await firstReadyVisual
      report.visual = await captureVisualLayouts(page, visualOutput, factor => visualContext.setZoom(page, factor), { onlyZoom: Boolean(natural) })
      report.visual.start_ms = started - origin
      report.visual.duration_ms = globalThis.performance.now() - started
      report.visual.setup_overlaps_other_probes = true
      await ready()
    }
    const links = await page.locator('.kt-assessment-nav [data-nav-assignment]').evaluateAll((nodes) => nodes.map((node) => ({ id: node.getAttribute('data-nav-assignment'), href: node.getAttribute('href') })))
    if (links.length < itemLimit) throw new Error('Fixture has insufficient questions')
    const sessionPath = new globalThis.URL(page.url()).pathname.replace(/questions\/\d+\/$/, '')
    for (let item = 0; item < itemLimit; item++) {
      report.current_item = item
      if (natural?.scenario === 'confidence-only-v1') await expect(page.getByRole('region', { name: '题目工具', exact: true })).toHaveCount(0)
      const input = page.locator('[name=answer]').first()
      const type = await input.getAttribute('type')
      if (natural) {
        stage = 'think'
        const began = Date.now() / 1000
        await performNaturalAnswer(input, page.locator('[name=answer]').nth(1), type, {
          dwellSeconds: natural.plan.item_dwell_seconds[item], modify: natural.plan.modify_items.includes(item),
          pause: async seconds => { stage = 'think'; await pause(seconds) },
          timed: (action, operation) => timed(action, item + 1, operation),
        })
        ;(report.think_waits ||= []).push({ item, started_at: began, completed_at: Date.now() / 1000,
          planned_seconds: natural.plan.item_dwell_seconds[item] })
      } else {
        await timed('answer_input', item + 1, async () => {
          if (['radio', 'checkbox'].includes(type)) await input.check()
          else await input.fill(type === 'number' ? '4' : '浏览器探针作答')
        })
      }
      if (item + 1 < itemLimit && (!natural || natural.plan.excursion_items.includes(item))) {
        await navigation('navigation_next_ready', item + 1, links[item + 1].id, `${sessionPath}questions/${links[item + 1].id}/`)
        await navigation('navigation_return_ready', item + 1, links[item].id, `${sessionPath}questions/${links[item].id}/`)
      }
      stage = 'research_tools'
      const questionType = await page.locator('.kt-assessment-context').innerText()
      for (const [label, kind] of [['单选题', 'single_choice'], ['多选题', 'multiple_choice'], ['填空题', 'fill_blank'], ['简答题', 'short_answer'], ['判断题', 'true_false']]) {
        if (questionType.includes(label) && !report.question_types.includes(kind)) report.question_types.push(kind)
      }
      const tools = page.getByRole('region', { name: '题目工具', exact: true })
      const required = page.locator('[aria-label="题目工具"] .kt-alert').filter({ hasText: '本题要求完成' })
      if ((!natural && questionType.includes('单选题')) || (natural && await required.count())) {
        await expect(required).toBeVisible()
        await timed('required_tool', item + 1, async () => {
          await required.getByRole('button', { name: '开始检查', exact: true }).click()
          await required.getByRole('button', { name: '完成检查', exact: true }).click()
          await expect(required).toHaveCount(0)
        })
      }
      if ((!natural && questionType.includes('填空题')) || natural?.plan.optional_tool_items.includes(item)) {
        await timed('optional_tool', item + 1, async () => {
          const hint = tools.getByRole('button', { name: '查看第 1 级提示', exact: true })
          if (await hint.count()) {
            await hint.click()
            await tools.getByRole('button', { name: '关闭提示', exact: true }).click()
          } else {
            await tools.getByRole('button', { name: '开始检查', exact: true }).click()
            await tools.getByRole('button', { name: '结束检查', exact: true }).click()
          }
        })
      }
      if (natural?.scenario === 'confidence-only-v1') {
        await timed('submit_request_confidence_ready', item + 1, async () => {
          await page.locator('[data-submit-request]').click()
          await expect(page.locator('[data-confidence-panel]')).toBeVisible()
        })
        await resolvePlannedConfidence(page, { action: natural.plan.confidence_actions[item],
          value: natural.plan.confidence_values[item], thinkSeconds: natural.plan.confidence_think_seconds[item] }, {
          pause: async seconds => { stage = 'confidence_think'; await pause(seconds) },
          recordThink: row => { (report.confidence_think_waits ||= []).push({ item, ...row }) },
          timed: (action, operation) => timed(action, item + 1, async () => {
            await operation()
            await expect(page.locator('[data-submit-confirm]')).toBeVisible()
          }),
        })
      } else await timed('submit_confirmation_ready', item + 1, async () => {
        await page.locator('[data-submit-request]').click()
        if (await page.locator('[data-confidence-panel]').isVisible()) await page.getByRole('button', { name: '跳过评价', exact: true }).click()
        await expect(page.locator('[data-submit-confirm]')).toBeVisible()
      })
      await performSubmissionPhases({ last: item + 1 === itemLimit,
        timed: (action, operation) => action === 'submit_next_ready' || action === 'finish_ready'
          ? timed(action, item + 1, operation)
          : timedProbeAction(report, origin, action, item + 1, operation),
        ack: async () => {
        const submitted = page.waitForResponse((response) => response.url().endsWith('/submit/') && response.request().method() === 'POST')
        await page.locator('[data-submit-confirm]').click()
        const response = await submitted
        if (!response.ok()) throw new Error('Fixture submission failed')
        const result = (await response.json()).data
        if (result.success !== true || result.status !== 'submitted') throw new Error('Fixture submission was not confirmed')
        report.submitted_count++
        return result
        }, ready: async (result, remaining) => {
        await waitForSubmittedDestination(page, new globalThis.URL(result.next_url, page.url()).pathname, async budget => {
          if (item + 1 < itemLimit) await ready(budget)
          else report.session_completed = await waitForSessionCompletion(page, account.taskId, undefined, budget)
        }, { report, sequence: item + 1, stopFile: natural?.stopFile, timeoutMs: remaining })
        },
      })
    }
    report.completed = report.submitted_count === itemLimit && report.session_completed
    if (report.completed) report.completed_at = Date.now() / 1000
  } catch (error) {
    report.failure_stage = stage
    report.error_class = error instanceof Error ? error.name : 'UnknownError'
    report.error_code = typeof error.code === 'string' && /^[A-Z0-9_]+$/.test(error.code) ? error.code : null
    report.error_message = error instanceof Error ? error.message.replaceAll(account.password, '[redacted]').replaceAll(account.username, '[redacted]') : null
    report.timeout = report.error_class === 'TimeoutError' || report.total_timeout === true
    report.not_reached_items = Array.from({ length: itemLimit - report.submitted_count }, (_, index) => report.submitted_count + index)
    report.last_failed_http_status = report.waterfall.findLast((row) => row.status >= 400)?.status || null
    report.failure_ui = await page.evaluate(() => ({
      path: globalThis.location.pathname,
      initialization_state: globalThis.document.querySelector('[data-init-state]')?.getAttribute('data-init-state') || null,
      answer_inputs: [...globalThis.document.querySelectorAll('[name=answer]')].map(node => ({
        tag: node.tagName, type: node.getAttribute('type'), disabled: node.disabled,
      })),
      retry_close_present: Boolean(globalThis.document.querySelector('[data-retry-close]')),
      submit_disabled: globalThis.document.querySelector('[data-submit-request]')?.disabled ?? null,
      submitted_notice_present: globalThis.document.body.textContent.includes('本题已提交并锁定'),
    })).catch(() => ({ observed: false }))
    if (visualOutput) await page.screenshot({ path: join(visualOutput, `probe-${index + 1}-failure.png`), fullPage: true }).catch(() => undefined)
  } finally {
    globalThis.clearTimeout(budgetTimer)
    if (firstReadyVisual) await firstReadyVisual
    try {
      if (loginOnly) {
        report.flow_completed = report.completed
        report.waterfall_settlement = await waterfall.settle()
        report.first_login_post = firstLoginPost(report)
        report.evidence_complete = report.waterfall_settlement.complete === true && report.first_login_post !== null
        report.completed = report.flow_completed && report.evidence_complete && report.first_login_post.successful && report.page_error_count === 0
      } else await settleProbeEvidence(page, report, protocol, waterfall)
    }
    catch { report.flow_completed = report.completed; report.completed = false; report.evidence_complete = false }
    if (natural?.scenario === 'confidence-only-v1') {
      report.scenario = natural.scenario
      report.synthetic_confidence = true
      report.confidence_requests = report.waterfall.filter(row => row.endpoint.includes('/confidence/'))
      report.tools_request_count = report.waterfall.filter(row => /\/(?:tools|resolve)\/$/.test(row.endpoint)).length
      report.disabled_tools_passed = report.tools_request_count === 0
      if (!report.disabled_tools_passed) report.completed = false
    }
    if (natural) natural.publish(report)
    if (visualContext) await visualContext.close()
    else await context.close()
  }
  return report
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const { values } = parseArgs({ options: { 'base-url': { type: 'string' }, manifest: { type: 'string' }, output: { type: 'string' }, phase: { type: 'string' }, mode: { type: 'string', default: 'natural' }, items: { type: 'string', default: '20' }, 'visual-output': { type: 'string' }, plan: { type: 'string' }, 'start-at': { type: 'string' }, 'stop-file': { type: 'string' }, progress: { type: 'string' }, accounts: { type: 'string' } } })
  if (!values['base-url'] || !values.manifest || !values.output || !['baseline', 'repaired', 'load'].includes(values.phase)) throw new Error('Required: --base-url --manifest --output --phase baseline|repaired|load')
  const manifest = JSON.parse(readFileSync(values.manifest, 'utf8'))
  const accounts = probeAccounts(manifest)
  if (values.accounts && values.accounts.split(',').join(',') !== accounts.map(row => row.username).join(',')) throw new Error('Accounts must equal the explicit fixture browser account subset')
  const workload = values.plan ? JSON.parse(readFileSync(values.plan, 'utf8')) : null
  const plans = workload ? naturalBrowserPlans(workload, accounts) : null
  const startAt = Number(values['start-at'])
  if (!['natural', 'login-only'].includes(values.mode) || values.mode === 'login-only' && (!plans || plans.some(row => row.arrival_offset_seconds !== 0))) throw new Error('Login-only mode requires frozen zero-arrival browser plans')
  if (plans && (!Number.isFinite(startAt) || startAt <= 0 || Number(values.items) !== 20 || !manifest.browser_usernames)) throw new Error('Natural mode requires --start-at UNIX, 20 items, and explicit two-account browser fixture')
  const progressPath = values.progress || values.output.replace(/\.json$/, '') + '.progress.json'
  const live = new Map()
  const publish = report => {
    live.set(report.probe, report)
    const probes = [...live.values()].map(row => ({ probe: row.probe, username_sha256: row.username_sha256,
      started_at: row.started_at, ready_at: row.ready_at, completed_at: row.completed_at, submitted_count: row.submitted_count,
      current_item: row.current_item ?? null, failure_stage: row.failure_stage ?? null, completed: row.completed }))
    const progress = { updated_at: Date.now() / 1000, start_at: startAt, probes,
      active_count: probes.filter(row => row.ready_at !== null && row.completed_at === null && !row.failure_stage).length }
    writeFileSync(progressPath + '.tmp', JSON.stringify(progress), { mode: 0o600 })
    renameSync(progressPath + '.tmp', progressPath)
  }
  if (values['visual-output']) mkdirSync(values['visual-output'], { recursive: true })
  const browser = await chromium.launch()
  try {
    const probes = await Promise.all(accounts.map((account, index) => runAccount(browser, account, values['base-url'], index, Number(values.items), values['visual-output'], manifest.source_config,
      plans ? { plan: plans[index], scenario: workload.scenario, mode: values.mode, startAt, stopFile: values['stop-file'], publish } : null)))
    const report = { phase: values.phase, mode: values.mode, completed: probes.every((probe) => probe.completed), probes }
    if (values.mode !== 'login-only') report.architecture_sessions = buildBrowserArchitectureSessions(report, accounts)
    if (plans) { report.workload_version = 'natural-180-v1'; report.start_at = startAt }
    writeFileSync(values.output, JSON.stringify(report, null, 2), { flag: 'wx', mode: 0o600 })
    globalThis.console.log(JSON.stringify({ completed: probes.every((probe) => probe.completed), actions: probes.map((probe) => probe.actions.length), output: values.output }))
    if (probes.some((probe) => !probe.completed)) process.exitCode = 1
  } finally { await browser.close() }
}
