import { createHash } from 'node:crypto'
import { existsSync, readFileSync, writeFileSync } from 'node:fs'
import { freemem, platform } from 'node:os'
import { resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { parseArgs } from 'node:util'
import process from 'node:process'
import { chromium, expect as baseExpect } from '@playwright/test'
import { startFixtureTask, waitForStudentHomeCourse } from './school-navigation-probe.mjs'
import { linuxResourceEvidence, resourceBudgetDecision } from './prepared-startup-witness.mjs'

const BUDGET = 60000
const expect = baseExpect.configure({ timeout: BUDGET })
const hash = value => createHash('sha256').update(typeof value === 'string' ? value : JSON.stringify(value)).digest('hex')
const domain = body => body?.data || body
function fail(code) { throw Object.assign(new Error(code), { code }) }
function check(condition, code) { if (!condition) fail(code) }

export function validateRecoveryInputs(fixture, baseURL, index, outputExists) {
  const url = new globalThis.URL(baseURL)
  check(url.protocol === 'http:' && ['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname)
    && url.port === '18081' && !url.username && !url.password && url.pathname === '/' && !url.search && !url.hash, 'ISOLATED_LOOPBACK_18081_REQUIRED')
  check(fixture.isolated_demo === true && /^\d+$/.test(index), 'EXPLICIT_PRIVATE_ACCOUNT_REQUIRED')
  const account = fixture.accounts?.[Number(index)]
  check(account && Number.isInteger(account.taskId) && Number.isInteger(account.courseId) && account.username && account.password, 'FIXTURE_ACCOUNT_INVALID')
  check(!outputExists, 'OUTPUT_ALREADY_EXISTS')
  return account
}

export function receiptEvidence(body) {
  const data = domain(body), runtime = data.runtime || data, receipt = runtime.entry_receipt
  check(receipt && Number.isInteger(receipt.session_id) && receipt.session_id === data.session_id
    && Number.isInteger(receipt.visit_id) && Number.isInteger(receipt.visit_index)
    && receipt.navigation_id && receipt.entry_event_id && receipt.entered_at, 'ACTUAL_ENTRY_RECEIPT_REQUIRED')
  const identity = { session_id: receipt.session_id, assignment_id: receipt.assignment_id, attempt_id: receipt.attempt_id,
    visit_id: receipt.visit_id, visit_index: receipt.visit_index, entered_at: receipt.entered_at,
    navigation_id_sha256: hash(receipt.navigation_id), entry_event_id_sha256: hash(receipt.entry_event_id) }
  return { ...identity, receipt_sha256: hash(identity) }
}

export async function loseCommittedActivationResponse(route, timeoutMs, onCommitted = async () => {}) {
  const response = await route.fetch({ timeout: timeoutMs, maxRetries: 0, maxRedirects: 0 })
  if (response.status() !== 201) { await route.fulfill({ response }); fail('ACTIVATION_NOT_COMMITTED_201') }
  const envelope = await response.json()
  const receipt = receiptEvidence(envelope)
  await onCommitted(receipt)
  await route.abort('failed')
  return { receipt, envelope, http_status: 201 }
}

export function newRecoveryCases() {
  return ['lost_activation_response_reload', 'duplicate_original_activation', 'copied_tab_writer_isolation']
    .map(case_id => ({ case_id, status: 'not_executed', reason: 'prerequisites_not_completed' }))
}

export async function runRecoveryCase(row, action, { timeoutMs = BUDGET, onTimeout = async () => {} } = {}) {
  check(Number.isInteger(timeoutMs) && timeoutMs > 0 && timeoutMs <= BUDGET, 'INVALID_ACTION_BUDGET')
  row.status = 'running'; delete row.reason
  row.started_at_utc = new Date().toISOString()
  const deadline = Date.now() + timeoutMs
  let timer
  try {
    await Promise.race([action(() => Math.max(1, deadline - Date.now())), new Promise((_, reject) => {
      timer = globalThis.setTimeout(() => reject(Object.assign(new Error('ACTION_TIMEOUT'), { code: 'ACTION_TIMEOUT' })), timeoutMs)
    })])
    row.status = 'completed'
  } catch (error) {
    row.status = 'failed'
    row.error_code = /^[A-Z_]{1,80}$/.test(error.code || '') ? error.code : error.name === 'TimeoutError' || error.matcherResult?.timeout ? 'ACTION_TIMEOUT' : 'ACTION_FAILED'
    if (row.error_code === 'ACTION_TIMEOUT') await onTimeout()
  } finally { globalThis.clearTimeout(timer); row.finished_at_utc = new Date().toISOString() }
}

export function assessCopiedTab(input) {
  return { native_web_locks: input.nativeLocks, session_storage_copied_by_window_open: input.originalTab === input.inheritedTab,
    tab_namespace_forked: input.copiedTab !== input.originalTab,
    original_client_sha256: hash(input.originalClient || ''), copied_client_sha256: hash(input.copiedClient || ''),
    client_identity_forked: Boolean(input.originalClient && input.copiedClient && input.originalClient !== input.copiedClient),
    lease_http_statuses: input.leaseStatuses, copied_draft_successes: input.copiedDraftSuccesses,
    passed: input.nativeLocks === true && input.originalTab === input.inheritedTab && input.copiedTab !== input.originalTab
      && Boolean(input.originalClient && input.copiedClient && input.originalClient !== input.copiedClient)
      && input.leaseStatuses.includes(409) && !input.leaseStatuses.some(status => status >= 200 && status < 300)
      && input.copiedDraftSuccesses === 0 }
}

async function storedIdentity(page, userId, taskId) {
  return page.evaluate(async ({ userId, taskId }) => {
    const tab = globalThis.sessionStorage.getItem('kt-assessment-tab-id')
    const credentials = globalThis.sessionStorage.getItem(`kt-assessment-admission:${taskId}`)
    const admission = credentials ? JSON.parse(credentials) : null
    const startIntent = JSON.parse(globalThis.sessionStorage.getItem(`kt-assessment-start:${taskId}`) || 'null')
    if (!(await globalThis.indexedDB.databases()).some(row => row.name === 'kt-process-events-v1')) return { tab, client: null, admission, startIntent, nativeLocks: !!globalThis.navigator.locks }
    const db = await new Promise((resolveOpen, reject) => {
      const request = globalThis.indexedDB.open('kt-process-events-v1')
      request.onupgradeneeded = () => { request.transaction.abort(); reject(new Error('unexpected_schema')) }
      request.onerror = request.onblocked = () => reject(new Error('storage_unavailable'))
      request.onsuccess = () => resolveOpen(request.result)
    })
    try {
      const row = await new Promise((resolveRead, reject) => {
        const request = db.transaction('meta', 'readonly').objectStore('meta').get(`assessment-client:${userId}:${tab}`)
        request.onsuccess = () => resolveRead(request.result)
        request.onerror = () => reject(request.error)
      })
      return { tab, client: row?.value || null, admission, startIntent, nativeLocks: !!globalThis.navigator.locks }
    } finally { db.close() }
  }, { userId, taskId })
}

function publicStorage(value) {
  return { tab_sha256: value.tab ? hash(value.tab) : null, client_sha256: value.client ? hash(value.client) : null,
    start_intent_present: !!value.startIntent, start_navigation_sha256: value.startIntent?.navigation_id ? hash(value.startIntent.navigation_id) : null,
    admission_token_present: !!value.admission?.admission_token,
    admission_token_sha256: value.admission?.admission_token ? hash(value.admission.admission_token) : null,
    admission_action: value.admission?.admission_action || null, native_web_locks: value.nativeLocks }
}

export function pendingStartMatches(storage, body) {
  return !!storage.startIntent && storage.startIntent.navigation_id === body.navigation_id
    && storage.startIntent.client_instance_id === body.client_instance_id
}

async function ready(page, remaining) {
  await expect(page.locator('[data-init-state="ready"]')).toBeVisible({ timeout: remaining() })
  await expect(page.locator('[name="answer"]').first()).toBeEnabled({ timeout: remaining() })
}

export async function answerDifferentControl(page, account) {
  const input = page.locator('[name="answer"]').first(), type = await input.getAttribute('type')
  if (type === 'checkbox') await input.setChecked(!await input.isChecked())
  else if (type === 'radio') {
    const unselected = page.locator('[name="answer"]:not(:checked)').first()
    check(await unselected.count() > 0, 'NO_DIFFERENT_RADIO_RESPONSE')
    await unselected.check()
  }
  else {
    check(account.save_value !== undefined, 'EXPLICIT_FIXTURE_SAVE_VALUE_REQUIRED')
    const value = String(account.save_value)
    if (await input.inputValue() === value) await input.fill('')
    await input.fill(value)
  }
}

async function main() {
  const { values } = parseArgs({ options: { fixture: { type: 'string' }, 'account-index': { type: 'string' },
    'base-url': { type: 'string' }, output: { type: 'string' }, 'min-free-mib': { type: 'string', default: platform() === 'linux' ? '2048' : '768' },
    'min-cgroup-free-mib': { type: 'string', default: '256' } } })
  for (const key of ['fixture', 'account-index', 'base-url', 'output']) check(values[key] !== undefined, 'EXPLICIT_CLI_INPUT_REQUIRED')
  const fixtureBytes = readFileSync(resolve(values.fixture)), fixture = JSON.parse(fixtureBytes)
  const output = resolve(values.output), account = validateRecoveryInputs(fixture, values['base-url'], values['account-index'], existsSync(output))
  const minimum = Number(values['min-free-mib']) * 1024 ** 2, minimumCgroup = Number(values['min-cgroup-free-mib']) * 1024 ** 2
  check(Number.isFinite(minimum) && minimum >= 768 * 1024 ** 2 && Number.isFinite(minimumCgroup) && minimumCgroup >= 256 * 1024 ** 2, 'RESOURCE_THRESHOLD_CANNOT_BE_LOWERED')
  const report = { version: 1, isolated_demo: true, account_index: Number(values['account-index']), task_id: account.taskId,
    fixture_sha256: hash(fixtureBytes.toString()), code_sha256: Object.fromEntries(['prepared-recovery-witness.mjs', 'prepared-startup-witness.mjs', 'school-navigation-probe.mjs']
      .map(name => [name, hash(readFileSync(new globalThis.URL(name, import.meta.url)).toString())])),
    status: 'not_executed', operation_timeout_ms: BUDGET, maximum_contexts: 1, maximum_pages: 2,
    cases: newRecoveryCases(), http: [], resources: [],
    db_audit_status: 'required', db_assertions: [
      'One session start, unchanged started_at/deadlines and canonical activation/first-entry receipt after recovery and duplicate.',
      'After lost-response recovery and duplicate: no extra first-enter initialization, QuestionVisitEvent or first_open_count.',
      'Copied-tab genuine view events may be recorded; canonical first-entry/start facts must remain unchanged.',
      'Writer owner remains original client hash; copied client has no successful response-pointer mutation.',
    ] }
  writeFileSync(output, JSON.stringify(report, null, 2), { flag: 'wx', mode: 0o600 })
  const save = () => writeFileSync(output, JSON.stringify(report, null, 2))
  let server, browser, context, page, monitor, stopped = false, captured = null, actualSessionId = null, actualUserId = null
  const reads = [], entries = [], observedRequests = new Map()
  const resource = stage => {
    const sample = { stage, sampled_at_utc: new Date().toISOString(), available_physical_bytes: freemem(), cgroup_available_bytes: null }
    if (platform() === 'linux') Object.assign(sample, linuxResourceEvidence(server?.process().pid))
    report.resources.push(sample)
    return resourceBudgetDecision(sample, minimum, minimumCgroup)
  }
  const stop = async reason => { stopped = true; report.stop_reason = reason; await context?.close().catch(() => {}) }
  const observe = (target, label) => {
    target.on('request', request => {
      const path = new globalThis.URL(request.url()).pathname
      if (!path.startsWith('/api/')) return
      let body; try { body = request.postDataJSON() } catch { /* Non-JSON has no safe request identity fields. */ }
      const client = body?.client_instance_id || body?.writer_client_instance_id || body?.capture_bundle?.client_instance_id
      const entry = { page: label, method: request.method(), path, observed_at_utc: new Date().toISOString(),
        client_sha256: client ? hash(client) : null, navigation_sha256: body?.navigation_id ? hash(body.navigation_id) : null,
        admission_token_sha256: body?.admission_token ? hash(body.admission_token) : null }
      report.http.push(entry); observedRequests.set(request, entry)
    })
    target.on('response', response => {
      const entry = observedRequests.get(response.request())
      if (!entry) return
      entry.http_status = response.status()
      if (!/\/(?:start|resume|recovery)\/$|\/sessions\/\d+\/questions\/\d+\/$|\/(?:writer-lease|lease)\/(?:acquire|renew)\/$/.test(entry.path)) return
      reads.push((async () => {
        try {
          const data = domain(await response.json()); entry.body_read = 'completed'
          if (typeof data?.can_continue === 'boolean') entry.can_continue = data.can_continue
          if (Number.isInteger(data?.session_id)) entry.session_id = data.session_id
          if (data?.runtime?.entry_receipt || data?.entry_receipt) {
            entry.receipt = receiptEvidence(data); entries.push({ page: label, receipt: entry.receipt })
          }
          const code = data?.error?.code
          if (typeof code === 'string' && /^[A-Za-z0-9_-]{1,80}$/.test(code)) entry.application_error_code = code
        } catch { entry.body_read = 'failed' }
      })())
    })
    target.on('requestfailed', request => { const entry = observedRequests.get(request); if (entry) entry.request_failed = true })
  }
  const onTimeout = () => stop('ACTION_TIMEOUT')
  try {
    const initial = resource('before_launch')
    if (!initial.allowed) { report.stop_reason = initial.reason; return }
    server = await chromium.launchServer({ headless: true })
    browser = await chromium.connect(server.wsEndpoint())
    context = await browser.newContext({ baseURL: values['base-url'], viewport: { width: 1440, height: 900 } })
    context.setDefaultTimeout(BUDGET); page = await context.newPage(); observe(page, 'primary')
    monitor = globalThis.setInterval(() => {
      if (!resource('runtime').allowed && !stopped) void stop('RESOURCE_BUDGET_ABORT')
      if (context.pages().length > 2 && !stopped) void stop('PAGE_LIMIT_EXCEEDED')
    }, 1000)
    await page.goto('/login/')
    await page.getByLabel('账号或学号').fill(account.username); await page.getByLabel('密码').fill(account.password)
    await page.getByRole('button', { name: '登录', exact: true }).click()
    await expect(page).toHaveURL(/\/student\/$/); await waitForStudentHomeCourse(page, account.courseId)
    const consent = page.getByRole('dialog', { name: '研究参与说明与知情同意' })
    if (await consent.isVisible()) { await consent.getByRole('checkbox').check(); await consent.getByRole('button', { name: '同意并继续', exact: true }).click(); await expect(consent).toHaveCount(0) }
    const auth = domain(await (await context.request.get('/api/v1/auth/bootstrap/')).json())
    actualUserId = auth.user.id; report.user_id = actualUserId
    const start = await startFixtureTask(page, account, () => ready(page, () => BUDGET))
    check(start.entryAction === 'start', 'FRESH_PRIMARY_ACTION_START_REQUIRED')
    report.initial_primary_action = 'start'
    let resolveLost, rejectLost
    const loss = new Promise((resolveLoss, rejectLoss) => { resolveLost = resolveLoss; rejectLost = rejectLoss })
    const startPath = `/api/v1/student/tasks/${account.taskId}/start/`
    await page.route(`**${startPath}`, async route => {
      const request = route.request(), body = request.postDataJSON()
      if (captured || !body?.client_instance_id || !body?.navigation_id) { await route.continue(); return }
      captured = { body, raw: request.postData(), headers: await request.allHeaders() }
      try {
        captured.result = await loseCommittedActivationResponse(route, BUDGET, async receipt => {
          actualSessionId = receipt.session_id
          report.session_id = receipt.session_id
          report.canonical_receipt = receipt
          report.cases[0].committed_http_status = 201
          captured.storageBeforeLoss = await storedIdentity(page, actualUserId, account.taskId)
        })
        actualSessionId = captured.result.receipt.session_id
        resolveLost(captured.result)
      } catch (error) { rejectLost(error) }
    })
    await runRecoveryCase(report.cases[0], async remaining => {
      const starting = start().catch(() => undefined)
      const committed = await loss
      report.session_id = committed.receipt.session_id
      report.canonical_receipt = committed.receipt
      report.cases[0].committed_http_status = committed.http_status
      report.cases[0].storage_at_server_commit = publicStorage(captured.storageBeforeLoss)
      const before = await storedIdentity(page, actualUserId, account.taskId)
      report.cases[0].before_reload_storage = publicStorage(before)
      check(pendingStartMatches(captured.storageBeforeLoss, captured.body), 'ORIGINAL_PENDING_INTENT_NOT_PERSISTED')
      await page.reload({ timeout: remaining(), waitUntil: 'domcontentloaded' })
      report.cases[0].after_reload_storage = publicStorage(await storedIdentity(page, actualUserId, account.taskId))
      if (new globalThis.URL(page.url()).pathname === `/student/courses/${account.courseId}/`) {
        const tasks = domain(await (await context.request.get(`/api/v1/student/courses/${account.courseId}/tasks/`, { timeout: remaining() })).json())
        const task = tasks.items.find(value => value.task_id === account.taskId)
        check(task && ['start', 'resume'].includes(task.primary_action), 'RECOVERY_HAS_NO_AUTHORIZED_UI_ACTION')
        report.cases[0].recovery_ui_action = task.primary_action
        await page.locator('.kt-student-task-row').filter({ has: page.getByRole('heading', { name: task.experiment_name, exact: true }) })
          .getByRole('button', { name: task.primary_action === 'resume' ? '继续作答' : '开始作业', exact: true }).click({ timeout: remaining() })
      }
      await ready(page, remaining); await starting; await Promise.all(reads)
      report.cases[0].after_recovery_storage = publicStorage(await storedIdentity(page, actualUserId, account.taskId))
      check(new RegExp(`/student/sessions/${actualSessionId}/questions/\\d+/$`).test(new globalThis.URL(page.url()).pathname), 'RECOVERY_CHANGED_SESSION')
      const current = entries.filter(entry => entry.page === 'primary').at(-1)?.receipt
      report.cases[0].last_observed_ui_receipt = current || null
      report.cases[0].original_intent_seen_on_recovery_http = report.http.filter(entry => entry.navigation_sha256 === hash(captured.body.navigation_id)).length > 1
      report.cases[0].authorized_recovery_observed = report.http.some(entry => entry.path.endsWith('/recovery/') && entry.can_continue && entry.session_id === actualSessionId)
      check(current?.receipt_sha256 === committed.receipt.receipt_sha256, 'RECOVERY_CHANGED_FIRST_ENTRY_RECEIPT')
    }, { onTimeout }); save()

    if (captured?.result && !stopped) await runRecoveryCase(report.cases[1], async remaining => {
      const headers = { ...captured.headers }
      for (const key of ['host', 'content-length', 'connection']) delete headers[key]
      const response = await context.request.post(startPath, { headers, data: captured.raw, timeout: remaining() })
      const receipt = receiptEvidence(await response.json())
      report.cases[1].http_status = response.status(); report.cases[1].receipt = receipt
      report.cases[1].same_original_request_sha256 = hash(captured.raw)
      report.cases[1].transport = 'real_authenticated_HTTP_replay_of_the_original_browser_operation'
      check(response.ok() && receipt.receipt_sha256 === captured.result.receipt.receipt_sha256, 'DUPLICATE_CHANGED_ACTIVATION_RECEIPT')
    }, { onTimeout }); save()

    if (actualSessionId && !stopped && /\/questions\/\d+\/$/.test(new globalThis.URL(page.url()).pathname)) await runRecoveryCase(report.cases[2], async remaining => {
      await ready(page, remaining)
      const saved = page.waitForResponse(response => response.request().method() === 'POST' && /\/attempts\/\d+\/draft\/$/.test(new globalThis.URL(response.url()).pathname), { timeout: remaining() })
      await answerDifferentControl(page, account)
      check((await saved).ok(), 'ORIGINAL_TAB_DRAFT_NOT_SAVED')
      const original = await storedIdentity(page, actualUserId, account.taskId)
      const popupPromise = page.waitForEvent('popup', { timeout: remaining() })
      await page.evaluate(() => globalThis.window.open('about:blank', '_blank'))
      const copied = await popupPromise; observe(copied, 'copied')
      const inheritedTab = await copied.evaluate(() => globalThis.sessionStorage.getItem('kt-assessment-tab-id'))
      await copied.goto(page.url(), { timeout: remaining() }); await ready(copied, remaining)
      const second = await storedIdentity(copied, actualUserId, account.taskId)
      const lease = copied.waitForResponse(response => /\/(?:writer-lease|lease)\/(?:acquire|renew)\/$/.test(new globalThis.URL(response.url()).pathname), { timeout: remaining() })
      await answerDifferentControl(copied, account)
      const leaseResponse = await lease; await leaseResponse.finished()
      await expect(copied.locator('[role="alert"]').filter({ hasText: '另一页面或设备正在作答' })).toBeVisible({ timeout: remaining() })
      await Promise.all(reads)
      const copiedHttp = report.http.filter(entry => entry.page === 'copied')
      const assessment = assessCopiedTab({ originalClient: original.client, copiedClient: second.client, originalTab: original.tab,
        inheritedTab, copiedTab: second.tab, nativeLocks: original.nativeLocks && second.nativeLocks,
        leaseStatuses: copiedHttp.filter(entry => /\/(?:writer-lease|lease)\/(?:acquire|renew)\/$/.test(entry.path)).map(entry => entry.http_status),
        copiedDraftSuccesses: copiedHttp.filter(entry => /\/draft\/$/.test(entry.path) && entry.http_status >= 200 && entry.http_status < 300).length })
      report.cases[2].assessment = assessment
      report.cases[2].readonly_ui = { writer_conflict_visible: true, answer_control_disabled: await copied.locator('[name="answer"]').first().isDisabled(),
        save_status: await copied.locator('[data-save-status]').innerText() }
      check(assessment.passed, 'COPIED_TAB_WRITER_ISOLATION_FAILED')
    }, { onTimeout }); save()
    report.status = report.cases.slice(0, 3).every(row => row.status === 'completed') ? 'browser_witness_completed_db_audit_required' : 'failed_or_incomplete'
  } catch (error) {
    report.setup_error_code = /^[A-Z_]{1,80}$/.test(error.code || '') ? error.code : 'BROWSER_SETUP_FAILED'
    report.status = 'failed_or_incomplete'
  } finally {
    globalThis.clearInterval(monitor)
    await context?.close().catch(() => {}); await browser?.close().catch(() => {}); await server?.close().catch(() => {})
    await Promise.allSettled(reads)
    for (const row of report.cases.slice(0, 3)) if (row.status === 'not_executed') row.reason = report.stop_reason || report.setup_error_code || 'dependent_browser_state_unavailable'
    report.browser_closed = true
    report.finished_at_utc = new Date().toISOString(); save()
    if (report.status !== 'browser_witness_completed_db_audit_required') process.exitCode = 2
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) await main()
