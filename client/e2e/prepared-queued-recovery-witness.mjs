import { createHash } from 'node:crypto'
import { existsSync, readFileSync, writeFileSync } from 'node:fs'
import { freemem, platform } from 'node:os'
import { resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { parseArgs } from 'node:util'
import process from 'node:process'
import { chromium, expect as baseExpect } from '@playwright/test'
import { receiptEvidence, runRecoveryCase, validateRecoveryInputs } from './prepared-recovery-witness.mjs'
import { linuxResourceEvidence, resourceBudgetDecision } from './prepared-startup-witness.mjs'
import { startFixtureTask, waitForStudentHomeCourse } from './school-navigation-probe.mjs'

const BUDGET = 60000, expect = baseExpect.configure({ timeout: BUDGET })
const hash = value => createHash('sha256').update(value).digest('hex')
const domain = value => value?.data || value
function check(value, code) { if (!value) throw Object.assign(new Error(code), { code }) }

export function createActivationGate({ now = () => Date.now(), setTimer = (fn, ms) => globalThis.setTimeout(fn, ms), clearTimer = id => globalThis.clearTimeout(id) } = {}) {
  const evidence = { automatic_failsafe_ms: 7000, reached: false, forwarded: false }
  let reach, resume, timer, heldAt, released = false
  const reached = new Promise(resolveReached => { reach = resolveReached })
  const release = reason => {
    if (!evidence.reached || released) return
    released = true; clearTimer(timer)
    evidence.release_reason = reason; evidence.held_ms = now() - heldAt
    evidence.released_at_utc = new Date(now()).toISOString(); resume()
  }
  return { reached, evidence, release, intercept: async route => {
    if (evidence.reached) { await route.continue(); return }
    evidence.reached = true; heldAt = now(); evidence.reached_at_utc = new Date(heldAt).toISOString()
    const pending = new Promise(resolveResume => { resume = resolveResume })
    timer = setTimer(() => release('automatic_failsafe'), 7000)
    reach(); await pending
    await route.continue(); evidence.forwarded = true; evidence.forwarded_at_utc = new Date(now()).toISOString()
  } }
}

export async function offlineReloadAndRelease(context, page, release, remaining) {
  const evidence = { offline_reload_failed: false, offline_error_code: null }
  try {
    await context.setOffline(true)
    try { await page.reload({ timeout: Math.min(2000, remaining()), waitUntil: 'domcontentloaded' }) }
    catch (error) { evidence.offline_reload_failed = true; evidence.offline_error_code = /net::[A-Z_]+/.exec(error.message)?.[0] || error.name }
  } finally {
    try { await context.setOffline(false) } finally { release() }
  }
  return evidence
}

export function assessQueuedRecovery({ originalTokenHash, restoredTokenHash, recoveryPosts, canonicalReceiptCount }) {
  const fresh = recoveryPosts.some(entry => !entry.admission_token_sha256 || entry.admission_token_sha256 !== originalTokenHash)
  return { original_token_sha256: originalTokenHash, restored_token_sha256: restoredTokenHash,
    original_token_seen_on_recovery_http: recoveryPosts.some(entry => entry.admission_token_sha256 === originalTokenHash),
    new_application_observed: fresh, canonical_receipt_count: canonicalReceiptCount,
    passed: Boolean(originalTokenHash && restoredTokenHash === originalTokenHash && recoveryPosts.length && !fresh && canonicalReceiptCount === 3) }
}

async function persistedToken(page, taskId) {
  return page.evaluate(id => {
    const value = globalThis.sessionStorage.getItem(`kt-assessment-admission:${id}`)
    return value ? JSON.parse(value).admission_token || null : null
  }, taskId)
}

async function main() {
  const { values } = parseArgs({ options: { mode: { type: 'string' }, fixture: { type: 'string' }, 'account-indices': { type: 'string' },
    'base-url': { type: 'string' }, output: { type: 'string' }, 'min-free-mib': { type: 'string', default: platform() === 'linux' ? '2048' : '768' },
    'min-cgroup-free-mib': { type: 'string', default: '256' } } })
  for (const key of ['mode', 'fixture', 'account-indices', 'base-url', 'output']) check(values[key] !== undefined, 'EXPLICIT_CLI_INPUT_REQUIRED')
  check(values.mode === 'queued-offline-recovery', 'EXPLICIT_QUEUED_RECOVERY_MODE_REQUIRED')
  const indices = values['account-indices'].split(',')
  check(indices.length === 3 && new Set(indices).size === 3, 'THREE_DISTINCT_FRESH_INDICES_REQUIRED')
  const bytes = readFileSync(resolve(values.fixture)), fixture = JSON.parse(bytes), output = resolve(values.output)
  const accounts = indices.map(index => validateRecoveryInputs(fixture, values['base-url'], index, existsSync(output)))
  check(new Set(accounts.map(account => account.username)).size === 3, 'THREE_DISTINCT_USERS_REQUIRED')
  const minHost = Number(values['min-free-mib']) * 1024 ** 2, minCgroup = Number(values['min-cgroup-free-mib']) * 1024 ** 2
  check(Number.isFinite(minHost) && minHost >= 768 * 1024 ** 2 && Number.isFinite(minCgroup) && minCgroup >= 256 * 1024 ** 2, 'RESOURCE_THRESHOLD_CANNOT_BE_LOWERED')
  const row = { case_id: 'queued_offline_recovery', status: 'not_executed', reason: 'browser_setup_not_completed' }
  const gates = [createActivationGate(), createActivationGate()]
  const report = { version: 1, mode: values.mode, status: 'not_executed', operation_timeout_ms: BUDGET,
    maximum_contexts: 3, maximum_pages: 3, fixture_sha256: hash(bytes),
    code_sha256: Object.fromEntries(['prepared-queued-recovery-witness.mjs', 'prepared-recovery-witness.mjs', 'prepared-startup-witness.mjs', 'school-navigation-probe.mjs']
      .map(name => [name, hash(readFileSync(new globalThis.URL(name, import.meta.url)))])),
    accounts: indices.map((index, position) => ({ account_index: Number(index), task_id: accounts[position].taskId,
      expected_session_id: Number.isInteger(accounts[position].session_id) ? accounts[position].session_id : null, status: 'not_executed' })),
    cases: [row], gates: gates.map(gate => gate.evidence), http: [], resources: [], canonical_receipts: [], db_audit_status: 'required' }
  writeFileSync(output, JSON.stringify(report, null, 2), { flag: 'wx', mode: 0o600 })
  const save = () => writeFileSync(output, JSON.stringify(report, null, 2))
  let browser, server, monitor, stopped = false, remaining = () => BUDGET
  const contexts = [], prepared = [], reads = [], requestRows = new Map(), originalStarts = []
  const resource = stage => {
    const sample = { stage, sampled_at_utc: new Date().toISOString(), available_physical_bytes: freemem(), cgroup_available_bytes: null }
    if (platform() === 'linux') Object.assign(sample, linuxResourceEvidence(server?.process().pid))
    report.resources.push(sample); return resourceBudgetDecision(sample, minHost, minCgroup)
  }
  const releaseBoth = reason => gates.forEach(gate => gate.release(reason))
  const stop = async reason => { stopped = true; report.stop_reason = reason; releaseBoth(reason); await Promise.allSettled(contexts.map(context => context.close())) }
  const ready = async page => {
    await expect(page.locator('[data-init-state="ready"]')).toBeVisible({ timeout: remaining() })
    await expect(page.locator('[name="answer"]').first()).toBeEnabled({ timeout: remaining() })
  }
  const observe = (page, position) => {
    page.on('request', request => {
      const path = new globalThis.URL(request.url()).pathname
      if (!path.startsWith('/api/')) return
      let body; try { body = request.postDataJSON() } catch { /* Only explicit identity fields are retained. */ }
      const entry = { account_position: position, path, method: request.method(), observed_at_utc: new Date().toISOString(),
        admission_token_sha256: body?.admission_token ? hash(body.admission_token) : null,
        client_sha256: body?.client_instance_id ? hash(body.client_instance_id) : null }
      report.http.push(entry); requestRows.set(request, entry)
    })
    page.on('response', response => {
      const entry = requestRows.get(response.request())
      if (!entry) return
      entry.http_status = response.status()
      if (!new RegExp(`/tasks/${accounts[position].taskId}/(?:start|resume)/$`).test(entry.path)) return
      reads.push((async () => {
        try {
          const data = domain(await response.json()); entry.body_read = 'completed'
          if (typeof data.status === 'string') entry.admission_status = data.status
          if (data.runtime?.entry_receipt) {
            const receipt = receiptEvidence(data)
            entry.receipt = receipt
            if (!report.canonical_receipts.some(item => item.account_position === position)) report.canonical_receipts.push({ account_position: position, ...receipt })
          }
        } catch { entry.body_read = 'failed' }
      })())
    })
    page.on('requestfailed', request => { const entry = requestRows.get(request); if (entry) entry.request_failed = true })
  }
  try {
    const initial = resource('before_launch')
    if (!initial.allowed) { row.reason = initial.reason; report.stop_reason = initial.reason; return }
    server = await chromium.launchServer({ headless: true }); browser = await chromium.connect(server.wsEndpoint())
    monitor = globalThis.setInterval(() => {
      if (!resource('runtime').allowed && !stopped) void stop('RESOURCE_BUDGET_ABORT')
      if ((contexts.length > 3 || contexts.reduce((count, context) => count + context.pages().length, 0) > 3) && !stopped) void stop('BROWSER_LIMIT_EXCEEDED')
    }, 1000)
    for (let position = 0; position < 3; position++) {
      const account = accounts[position], context = await browser.newContext({ baseURL: values['base-url'], viewport: { width: 1440, height: 900 } })
      contexts.push(context); context.setDefaultTimeout(BUDGET)
      const page = await context.newPage(); observe(page, position)
      await page.goto('/login/'); await page.getByLabel('账号或学号').fill(account.username); await page.getByLabel('密码').fill(account.password)
      await page.getByRole('button', { name: '登录', exact: true }).click(); await expect(page).toHaveURL(/\/student\/$/)
      await waitForStudentHomeCourse(page, account.courseId)
      const consent = page.getByRole('dialog', { name: '研究参与说明与知情同意' })
      if (await consent.isVisible()) { await consent.getByRole('checkbox').check(); await consent.getByRole('button', { name: '同意并继续', exact: true }).click(); await expect(consent).toHaveCount(0) }
      const start = await startFixtureTask(page, account, () => ready(page))
      check(start.entryAction === 'start', 'FRESH_PRIMARY_ACTION_START_REQUIRED')
      report.accounts[position].initial_primary_action = 'start'
      if (position < 2) await page.route(`**/api/v1/student/tasks/${account.taskId}/start/`, async route => {
        const request = route.request(), body = request.postDataJSON()
        if (stopped) { await route.abort(); return }
        if (request.method() !== 'POST' || !body?.admission_token || gates[position].evidence.reached) { await route.continue(); return }
        gates[position].evidence.admission_token_sha256 = hash(body.admission_token)
        try { await gates[position].intercept(route) } catch { gates[position].evidence.forward_error = 'BROWSER_REQUEST_ABORTED' }
      })
      prepared.push({ context, page, start })
      report.accounts[position].status = 'prepared'
    }
    await runRecoveryCase(row, async budget => {
      remaining = budget
      originalStarts.push(prepared[0].start().catch(() => undefined)); await gates[0].reached
      originalStarts.push(prepared[1].start().catch(() => undefined)); await gates[1].reached
      check(!gates.some(gate => gate.evidence.forwarded), 'ACTIVATIONS_RELEASED_BEFORE_QUEUE')
      const third = prepared[2], account = accounts[2]
      const responsePromise = third.page.waitForResponse(response => response.request().method() === 'POST'
        && new globalThis.URL(response.url()).pathname === `/api/v1/student/tasks/${account.taskId}/start/`, { timeout: remaining() })
      originalStarts.push(third.start().catch(() => undefined))
      const response = await responsePromise, queued = domain(await response.json())
      check(response.status() === 202 && queued.status === 'queued' && typeof queued.admission_token === 'string', 'REAL_QUEUED_202_NOT_OBSERVED')
      const queuedHash = hash(queued.admission_token)
      row.queued = { http_status: response.status(), status: queued.status, admission_token_sha256: queuedHash, observed_at_utc: new Date().toISOString() }
      await third.page.waitForFunction(({ taskId, token }) => {
        const value = globalThis.sessionStorage.getItem(`kt-assessment-admission:${taskId}`)
        return value && JSON.parse(value).admission_token === token
      }, { taskId: account.taskId, token: queued.admission_token }, { timeout: Math.min(2000, remaining()) })
      row.persisted_queued_token_sha256 = hash(await persistedToken(third.page, account.taskId))
      const recoveryResponse = await third.context.request.get(`/api/v1/student/tasks/${account.taskId}/recovery/`, { timeout: remaining() })
      const recovery = domain(await recoveryResponse.json())
      row.queued_authoritative_recovery = { http_status: recoveryResponse.status(), status: recovery.status,
        code: recovery.code, can_continue: recovery.can_continue, observed_at_utc: new Date().toISOString() }
      check(recoveryResponse.ok() && recovery.code === 'SESSION_NOT_STARTED' && recovery.can_continue === false, 'THIRD_STARTED_WHILE_EXPECTED_QUEUED')
      save()
      row.offline = await offlineReloadAndRelease(third.context, third.page, () => releaseBoth('third_back_online'), remaining)
      check(row.offline.offline_reload_failed && row.offline.offline_error_code === 'net::ERR_INTERNET_DISCONNECTED', 'EXPECTED_OFFLINE_RELOAD_FAILURE_NOT_OBSERVED')
      const recoveryHttpFrom = report.http.length
      const resume = await startFixtureTask(third.page, account, () => ready(third.page))
      row.recovery_ui_action = resume.entryAction
      const restored = await persistedToken(third.page, account.taskId)
      row.restored_token_sha256 = restored ? hash(restored) : null
      check(restored === queued.admission_token, 'QUEUED_TOKEN_CHANGED_AFTER_OFFLINE_RELOAD')
      await resume(); await Promise.all(originalStarts); await Promise.all(reads)
      for (let position = 0; position < 3; position++) { await ready(prepared[position].page); report.accounts[position].status = 'operable' }
      const recoveryPosts = report.http.slice(recoveryHttpFrom).filter(entry => entry.account_position === 2 && entry.method === 'POST'
        && new RegExp(`/tasks/${account.taskId}/`).test(entry.path) && !entry.path.endsWith('/resume/'))
      row.recovery_http = recoveryPosts
      row.assessment = assessQueuedRecovery({ originalTokenHash: queuedHash, restoredTokenHash: row.restored_token_sha256,
        recoveryPosts, canonicalReceiptCount: report.canonical_receipts.length })
      check(row.assessment.passed, 'THIRD_REAPPLIED_OR_RECOVERY_EVIDENCE_INCOMPLETE')
      check(new Set(report.canonical_receipts.map(receipt => receipt.session_id)).size === 3, 'THREE_DISTINCT_SESSIONS_NOT_OBSERVED')
      check(report.canonical_receipts.every(receipt => !Number.isInteger(accounts[receipt.account_position].session_id)
        || accounts[receipt.account_position].session_id === receipt.session_id), 'SESSION_CHANGED_FROM_PREALLOCATED_FIXTURE')
      check(gates.every(gate => gate.evidence.held_ms <= 8000 && gate.evidence.forwarded), 'GATE_FAILSAFE_BOUND_EXCEEDED')
      check(report.http.filter(entry => entry.account_position < 2 && entry.path.endsWith('/start/') && entry.http_status === 201).length === 2, 'FIRST_TWO_REAL_201_NOT_OBSERVED')
    }, { onTimeout: () => stop('ACTION_TIMEOUT') })
    report.status = row.status === 'completed' ? 'browser_witness_completed_db_audit_required' : 'failed_or_incomplete'
  } catch (error) {
    report.setup_error_code = /^[A-Z_]{1,80}$/.test(error.code || '') ? error.code : 'BROWSER_SETUP_FAILED'
    row.reason = report.setup_error_code; report.status = 'failed_or_incomplete'
  } finally {
    releaseBoth('final_cleanup'); globalThis.clearInterval(monitor)
    await Promise.allSettled(contexts.map(context => context.close()))
    await browser?.close().catch(() => {}); await server?.close().catch(() => {}); await Promise.allSettled(reads)
    report.accounts.forEach((account, position) => {
      if (account.status === 'operable') return
      const requested = report.http.some(entry => entry.account_position === position && entry.method === 'POST' && entry.path.endsWith('/start/'))
      account.status = requested ? 'failed' : 'not_executed'
      account.reason = report.stop_reason || row.error_code || report.setup_error_code || row.reason
    })
    report.browser_closed = true; report.finished_at_utc = new Date().toISOString(); save()
    if (row.status !== 'completed') process.exitCode = 2
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) await main()
