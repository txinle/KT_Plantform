import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'
import { parseArgs } from 'node:util'
import process from 'node:process'
import { chromium, expect as baseExpect } from '@playwright/test'
import { observeBrowserProtocol } from './browser-network-witness.mjs'
import { captureBrowserStorage } from './browser-storage-witness.mjs'

export const probeExpect = baseExpect.configure({ timeout: 35000 })
const expect = probeExpect

export function navigableQuestionLink(page, assignment) {
  return page.locator(`.kt-assessment-nav [data-nav-assignment="${assignment}"][href]:not([aria-disabled="true"])`)
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
  if (!Array.isArray(manifest.usernames) || manifest.usernames.length !== 3 || new Set(manifest.usernames).size !== 3
    || manifest.usernames.some((value) => typeof value !== 'string' || !value)
    || typeof manifest.password !== 'string' || !manifest.password || !Number.isInteger(manifest.task_id)) throw new Error('Three unique fixture accounts and a task are required')
  return manifest.usernames.map((username) => ({ username, password: manifest.password, taskId: manifest.task_id, courseId: manifest.course_id }))
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
      request_id: correlationId(headers['x-request-id']), operation_id: correlationId(headers['x-kt-operation-id']), phase: currentPhase() }
    requests.set(request, row)
    report.waterfall.push(row)
  })
  page.on('requestfinished', (request) => {
    if (closed) return
    const row = requests.get(request)
    if (row) { row.end_ms = globalThis.performance.now() - origin; row.duration_ms = row.end_ms - row.start_ms }
  })
  page.on('requestfailed', (request) => { if (closed) return; const row = requests.get(request); if (row) row.failed = true })
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
    let timer
    let complete = false
    await Promise.race([Promise.allSettled([...pendingResponses]).then(() => { complete = true }),
      new Promise(resolve => { timer = globalThis.setTimeout(resolve, timeoutMs) })])
    globalThis.clearTimeout(timer)
    closed = true
    return { complete, pending_response_count: pendingResponses.size, budget_ms: timeoutMs }
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

export async function captureVisualLayouts(page, outputDirectory, setZoom) {
  const rows = []
  let zoom
  const layoutMetrics = () => page.evaluate(() => {
    globalThis.scrollTo(0, 0)
    const documentWidth = Math.max(globalThis.document.documentElement.scrollWidth, globalThis.document.body.scrollWidth)
    return { viewport_width: globalThis.innerWidth, document_width: documentWidth, overflow: documentWidth > globalThis.innerWidth + 1 }
  })
  try {
    for (const viewport of [
      { label: 'desktop-1440', width: 1440, height: 900 },
      { label: 'mobile-390', width: 390, height: 844 },
    ]) {
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

export async function waitForSessionCompletion(page, taskId, waitForSummary = async (locator) => { await expect(locator).toBeVisible() }) {
  await waitForSummary(page.locator('.kt-session-summary'))
  const recovered = await page.request.get(`/api/v1/student/tasks/${taskId}/recovery/`)
  if (!recovered.ok()) throw new Error('Fixture completion check failed')
  if ((await recovered.json()).data.status !== 'completed') throw new Error('Fixture session is not completed')
  return true
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

async function runAccount(browser, account, baseURL, index, itemLimit, visualOutput, candidate) {
  const visualContext = visualOutput && index === 0 ? await createVisualProbeContext(baseURL) : null
  const context = visualContext?.context || await browser.newContext({ baseURL, viewport: { width: 1440, height: 900 } })
  const page = await context.newPage()
  page.setDefaultTimeout(35000)
  const origin = globalThis.performance.now()
  const report = { probe: index + 1, completed: false, submitted_count: 0, session_completed: false, question_types: [], actions: [], waterfall: [], longtasks: [], runtime_timings: [], page_error_count: 0,
    input_latency_boundary: 'Answer input event dispatch through the next animation-frame callback; physical screen paint is not directly observed.' }
  let stage = 'login'
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
  const protocol = observeBrowserProtocol(page, { candidate, taskId: account.taskId, probe: index + 1 })
  report.protocol_witness = protocol.evidence
  async function ready(timeoutMs = 35000) {
    const started = globalThis.performance.now()
    await expect(page.locator('[data-init-state="ready"]')).toBeVisible({ timeout: timeoutMs })
    await expect(page.locator('[name=answer]').first()).toBeEnabled({ timeout: Math.max(1, timeoutMs - (globalThis.performance.now() - started)) })
  }
  async function timed(action, sequence, operation) {
    stage = action
    return timedProbeAction(report, origin, action, sequence, operation)
  }
  async function navigation(action, sequence, assignment, targetPath) {
    stage = action
    const link = navigableQuestionLink(page, assignment)
    await navigationProbeAction(report, origin, action, sequence, {
      enabled: timeout => expect(link).toBeEnabled({ timeout }),
      click: timeout => link.click({ timeout }),
      target: timeout => expect(page).toHaveURL(url => url.pathname === targetPath, { timeout }),
      ready,
    })
  }
  try {
    await page.goto('/login/')
    await page.getByLabel('账号或学号').fill(account.username)
    await page.getByLabel('密码').fill(account.password)
    await page.getByRole('button', { name: '登录', exact: true }).click()
    await expect(page).toHaveURL(/\/student\/$/)
    await page.waitForLoadState('domcontentloaded')
    await expect(page.locator('[data-vue-shell]')).toBeVisible()
    stage = 'research_consent'
    const consent = page.getByRole('dialog', { name: '研究参与说明与知情同意' })
    if (await consent.isVisible()) {
      await consent.getByRole('checkbox').check()
      await consent.getByRole('button', { name: '同意并继续', exact: true }).click()
      await expect(consent).toHaveCount(0)
    }
    stage = 'task_list'
    const startTask = await startFixtureTask(page, account, ready)
    await timed(startTask.entryAction === 'resume' ? 'resume_first_ready' : 'start_first_ready', 0, startTask)
    if (visualOutput && index === 0) {
      stage = 'visual_layout_capture'
      const started = globalThis.performance.now()
      report.visual = await captureVisualLayouts(page, visualOutput, factor => visualContext.setZoom(page, factor))
      report.visual.start_ms = started - origin
      report.visual.duration_ms = globalThis.performance.now() - started
      report.visual.setup_overlaps_other_probes = true
      await ready()
    }
    const links = await page.locator('.kt-assessment-nav [data-nav-assignment]').evaluateAll((nodes) => nodes.map((node) => ({ id: node.getAttribute('data-nav-assignment'), href: node.getAttribute('href') })))
    if (links.length < itemLimit) throw new Error('Fixture has insufficient questions')
    const sessionPath = new globalThis.URL(page.url()).pathname.replace(/questions\/\d+\/$/, '')
    for (let item = 0; item < itemLimit; item++) {
      stage = 'answer_input'
      const input = page.locator('[name=answer]').first()
      const type = await input.getAttribute('type')
      if (['radio', 'checkbox'].includes(type)) await input.check()
      else await input.fill(type === 'number' ? '4' : '浏览器探针作答')
      if (item + 1 < itemLimit) {
        await navigation('navigation_next_ready', item + 1, links[item + 1].id, `${sessionPath}questions/${links[item + 1].id}/`)
        await navigation('navigation_return_ready', item + 1, links[item].id, `${sessionPath}questions/${links[item].id}/`)
      }
      stage = 'research_tools'
      const questionType = await page.locator('.kt-assessment-context').innerText()
      for (const [label, kind] of [['单选题', 'single_choice'], ['多选题', 'multiple_choice'], ['填空题', 'fill_blank'], ['简答题', 'short_answer'], ['判断题', 'true_false']]) {
        if (questionType.includes(label) && !report.question_types.includes(kind)) report.question_types.push(kind)
      }
      const tools = page.getByRole('region', { name: '题目工具', exact: true })
      if (questionType.includes('单选题')) {
        const required = page.locator('[aria-label="题目工具"] .kt-alert').filter({ hasText: '本题要求完成' })
        await expect(required).toBeVisible()
        await required.getByRole('button', { name: '开始检查', exact: true }).click()
        await required.getByRole('button', { name: '完成检查', exact: true }).click()
        await expect(required).toHaveCount(0)
      }
      if (questionType.includes('填空题')) {
        await tools.getByRole('button', { name: '查看第 1 级提示', exact: true }).click()
        await tools.getByRole('button', { name: '关闭提示', exact: true }).click()
      }
      await timed('submit_confirmation_ready', item + 1, async () => {
        await page.locator('[data-submit-request]').click()
        if (await page.locator('[data-confidence-panel]').isVisible()) await page.getByRole('button', { name: '跳过评价', exact: true }).click()
        await expect(page.locator('[data-submit-confirm]')).toBeVisible()
      })
      await timed(item + 1 < itemLimit ? 'submit_next_ready' : 'finish_ready', item + 1, async () => {
        const submitted = page.waitForResponse((response) => response.url().endsWith('/submit/') && response.request().method() === 'POST')
        await page.locator('[data-submit-confirm]').click()
        const response = await submitted
        if (!response.ok()) throw new Error('Fixture submission failed')
        const result = (await response.json()).data
        if (result.success !== true || result.status !== 'submitted') throw new Error('Fixture submission was not confirmed')
        report.submitted_count++
        await expect(page).toHaveURL((url) => url.pathname === new globalThis.URL(result.next_url, page.url()).pathname)
        if (item + 1 < itemLimit) await ready()
        else report.session_completed = await waitForSessionCompletion(page, account.taskId)
      })
    }
    report.completed = report.submitted_count === itemLimit && report.session_completed
  } catch (error) {
    report.failure_stage = stage
    report.error_class = error instanceof Error ? error.name : 'UnknownError'
    report.last_failed_http_status = report.waterfall.findLast((row) => row.status >= 400)?.status || null
  } finally {
    await settleProbeEvidence(page, report, protocol, waterfall)
    if (visualContext) await visualContext.close()
    else await context.close()
  }
  return report
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const { values } = parseArgs({ options: { 'base-url': { type: 'string' }, manifest: { type: 'string' }, output: { type: 'string' }, phase: { type: 'string' }, items: { type: 'string', default: '20' }, 'visual-output': { type: 'string' } } })
  if (!values['base-url'] || !values.manifest || !values.output || !['baseline', 'repaired', 'load'].includes(values.phase)) throw new Error('Required: --base-url --manifest --output --phase baseline|repaired|load')
  const manifest = JSON.parse(readFileSync(values.manifest, 'utf8'))
  const accounts = probeAccounts(manifest)
  if (values['visual-output']) mkdirSync(values['visual-output'], { recursive: true })
  const browser = await chromium.launch()
  try {
    const probes = await Promise.all(accounts.map((account, index) => runAccount(browser, account, values['base-url'], index, Number(values.items), values['visual-output'], manifest.source_config)))
    writeFileSync(values.output, JSON.stringify({ phase: values.phase, completed: probes.every((probe) => probe.completed), probes }, null, 2), { flag: 'wx' })
    globalThis.console.log(JSON.stringify({ completed: probes.every((probe) => probe.completed), actions: probes.map((probe) => probe.actions.length), output: values.output }))
    if (probes.some((probe) => !probe.completed)) process.exitCode = 1
  } finally { await browser.close() }
}
