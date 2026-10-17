import { createHash, randomUUID } from 'node:crypto'
import { existsSync, linkSync, readFileSync, unlinkSync, writeFileSync } from 'node:fs'
import { freemem, platform } from 'node:os'
import { resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { parseArgs } from 'node:util'
import process from 'node:process'
import { chromium, expect as baseExpect } from '@playwright/test'
import { answerDifferentControl, runRecoveryCase, validateRecoveryInputs } from './prepared-recovery-witness.mjs'
import { linuxResourceEvidence, resourceBudgetDecision } from './prepared-startup-witness.mjs'
import { startFixtureTask, waitForStudentHomeCourse } from './school-navigation-probe.mjs'

const BUDGET = 60000, expect = baseExpect.configure({ timeout: BUDGET })
const hash = value => createHash('sha256').update(value).digest('hex')
const domain = value => value?.data || value
function check(value, code) { if (!value) throw Object.assign(new Error(code), { code }) }

export function acceptedMarker(httpStatus, data, observedAt = new Date().toISOString()) {
  check(httpStatus === 202 && data.success === true && typeof data.submission_id === 'string'
    && ['queued', 'running'].includes(data.status) && typeof data.accepted_at === 'string', 'ACTUAL_PENDING_202_REQUIRED')
  return { submission_id: data.submission_id, status: data.status,
    accepted_at_sha256: hash(data.accepted_at), observed_at_sha256: hash(observedAt) }
}

export function writeAcceptedMarker(path, marker) {
  const temporary = `${path}.${randomUUID()}.pending`
  writeFileSync(temporary, JSON.stringify(marker, null, 2), { flag: 'wx', mode: 0o600 })
  try { linkSync(temporary, path) } finally { unlinkSync(temporary) }
}

export function sameSubmissionEvidence(expected, httpStatus, data) {
  check(httpStatus === 200 && data.success === true && data.submission_id === expected, 'SUBMISSION_RECEIPT_CHANGED')
  check(['queued', 'running', 'completed'].includes(data.status), 'SUBMISSION_JOB_FAILED_OR_INVALID')
  return { submission_id: data.submission_id, status: data.status, http_status: httpStatus,
    accepted_at_sha256: typeof data.accepted_at === 'string' ? hash(data.accepted_at) : null }
}

export function validateSubmissionMode(mode, output, marker, exists = existsSync) {
  check(mode === 'submission-worker-restart', 'EXPLICIT_SUBMISSION_RESTART_MODE_REQUIRED')
  check(resolve(output) !== resolve(marker) && !exists(output) && !exists(marker), 'NEW_SEPARATE_OUTPUT_AND_MARKER_REQUIRED')
}

async function main() {
  const { values } = parseArgs({ options: { mode: { type: 'string' }, fixture: { type: 'string' }, 'account-index': { type: 'string' },
    'base-url': { type: 'string' }, output: { type: 'string' }, 'accepted-marker': { type: 'string' },
    'min-free-mib': { type: 'string', default: platform() === 'linux' ? '2048' : '768' }, 'min-cgroup-free-mib': { type: 'string', default: '256' } } })
  for (const key of ['mode', 'fixture', 'account-index', 'base-url', 'output', 'accepted-marker']) check(values[key] !== undefined, 'EXPLICIT_CLI_INPUT_REQUIRED')
  const output = resolve(values.output), markerPath = resolve(values['accepted-marker'])
  validateSubmissionMode(values.mode, output, markerPath)
  const bytes = readFileSync(resolve(values.fixture)), fixture = JSON.parse(bytes)
  const account = validateRecoveryInputs(fixture, values['base-url'], values['account-index'], existsSync(output))
  const minHost = Number(values['min-free-mib']) * 1024 ** 2, minCgroup = Number(values['min-cgroup-free-mib']) * 1024 ** 2
  check(Number.isFinite(minHost) && minHost >= 768 * 1024 ** 2 && Number.isFinite(minCgroup) && minCgroup >= 256 * 1024 ** 2, 'RESOURCE_THRESHOLD_CANNOT_BE_LOWERED')
  const row = { case_id: 'submission_worker_restart', status: 'not_executed', reason: 'browser_setup_not_completed' }
  const report = { version: 1, mode: values.mode, account_index: Number(values['account-index']), task_id: account.taskId,
    fixture_sha256: hash(bytes), code_sha256: Object.fromEntries(['prepared-submission-recovery-witness.mjs', 'prepared-recovery-witness.mjs', 'prepared-startup-witness.mjs', 'school-navigation-probe.mjs']
      .map(name => [name, hash(readFileSync(new globalThis.URL(name, import.meta.url)))])),
    operation_timeout_ms: BUDGET, maximum_contexts: 1, maximum_pages: 1, status: 'not_executed', cases: [row],
    status_receipts: [], http: [], resources: [], db_audit_status: 'required',
    worker_restart_evidence: 'External root-owned worker stop/start logs are required; this runner never controls Docker or claims exactly-once.' }
  writeFileSync(output, JSON.stringify(report, null, 2), { flag: 'wx', mode: 0o600 })
  const save = () => writeFileSync(output, JSON.stringify(report, null, 2))
  let server, browser, context, page, monitor, stopped = false, accepted = null, navigation = 0, reloadNavigation = null
  let resolveReloadStatus, rejectReloadStatus
  const reloadedStatus = new Promise((resolveStatus, rejectStatus) => { resolveReloadStatus = resolveStatus; rejectReloadStatus = rejectStatus })
  void reloadedStatus.catch(() => {})
  const requests = new Map()
  const sample = stage => {
    const data = { stage, sampled_at_utc: new Date().toISOString(), available_physical_bytes: freemem(), cgroup_available_bytes: null }
    if (platform() === 'linux') Object.assign(data, linuxResourceEvidence(server?.process().pid))
    report.resources.push(data)
    return resourceBudgetDecision(data, minHost, minCgroup)
  }
  const stop = async reason => { stopped = true; report.stop_reason = reason; await context?.close().catch(() => {}) }
  try {
    const initial = sample('before_launch')
    if (!initial.allowed) { row.reason = initial.reason; report.stop_reason = initial.reason; return }
    server = await chromium.launchServer({ headless: true }); browser = await chromium.connect(server.wsEndpoint())
    context = await browser.newContext({ baseURL: values['base-url'], viewport: { width: 1440, height: 900 } })
    context.setDefaultTimeout(BUDGET); page = await context.newPage()
    page.on('framenavigated', frame => { if (frame === page.mainFrame()) navigation++ })
    page.on('request', request => {
      const path = new globalThis.URL(request.url()).pathname
      if (!path.startsWith('/api/')) return
      const entry = { method: request.method(), path, navigation, observed_at_utc: new Date().toISOString() }
      requests.set(request, entry); report.http.push(entry)
    })
    page.on('response', response => { const entry = requests.get(response.request()); if (entry) entry.http_status = response.status() })
    monitor = globalThis.setInterval(() => {
      if (!sample('runtime').allowed && !stopped) void stop('RESOURCE_BUDGET_ABORT')
      if (context.pages().length > 1 && !stopped) void stop('PAGE_LIMIT_EXCEEDED')
    }, 1000)
    await page.goto('/login/'); await page.getByLabel('账号或学号').fill(account.username); await page.getByLabel('密码').fill(account.password)
    await page.getByRole('button', { name: '登录', exact: true }).click(); await expect(page).toHaveURL(/\/student\/$/)
    await waitForStudentHomeCourse(page, account.courseId)
    const consent = page.getByRole('dialog', { name: '研究参与说明与知情同意' })
    if (await consent.isVisible()) { await consent.getByRole('checkbox').check(); await consent.getByRole('button', { name: '同意并继续', exact: true }).click(); await expect(consent).toHaveCount(0) }
    let remaining = () => BUDGET
    const start = await startFixtureTask(page, account, async () => {
      await expect(page.locator('[data-init-state="ready"]')).toBeVisible({ timeout: remaining() })
      await expect(page.locator('[name="answer"]').first()).toBeEnabled({ timeout: remaining() })
    })
    check(start.entryAction === 'start', 'FRESH_PRIMARY_ACTION_START_REQUIRED')
    report.initial_primary_action = 'start'
    await page.route('**/api/v1/student/sessions/*/submission/', async route => {
      const initiatedNavigation = requests.get(route.request())?.navigation ?? navigation
      try {
        const response = await route.fetch({ timeout: remaining(), maxRetries: 0 })
        const data = domain(await response.json())
        const observed = { submission_id: data.submission_id, status: data.status, http_status: response.status(), navigation: initiatedNavigation }
        report.status_receipts.push(observed)
        if (accepted) {
          try {
            sameSubmissionEvidence(accepted.submission_id, response.status(), data)
            if (reloadNavigation !== null && initiatedNavigation >= reloadNavigation) resolveReloadStatus(observed)
          } catch (error) { rejectReloadStatus(error) }
        }
        await route.fulfill({ response })
      } catch { if (!stopped) rejectReloadStatus(Object.assign(new Error('STATUS_REQUEST_FAILED'), { code: 'STATUS_REQUEST_FAILED' })); await route.abort().catch(() => {}) }
    })
    await runRecoveryCase(row, async budget => {
      remaining = budget
      await start()
      const match = /\/student\/sessions\/(\d+)\/questions\//.exec(new globalThis.URL(page.url()).pathname)
      check(match, 'START_DID_NOT_OPEN_AUTHORIZED_QUESTION'); report.session_id = Number(match[1])
      const draft = page.waitForResponse(response => response.request().method() === 'POST' && /\/attempts\/\d+\/draft\/$/.test(new globalThis.URL(response.url()).pathname), { timeout: remaining() })
      await answerDifferentControl(page, account)
      const draftResponse = await draft, draftBody = domain(await draftResponse.json())
      check(draftResponse.ok() && draftBody.success === true, 'REAL_DRAFT_NOT_SAVED')
      row.draft_http_status = draftResponse.status()
      const finish = page.waitForResponse(response => response.request().method() === 'POST' && /\/sessions\/\d+\/finish\/$/.test(new globalThis.URL(response.url()).pathname), { timeout: remaining() })
      await page.locator('[data-finish-request]').click({ timeout: remaining() })
      await expect(page.locator('[data-paper-missing-confirm]')).toBeVisible({ timeout: remaining() })
      await page.locator('[data-paper-submit-confirm]').click({ timeout: remaining() })
      const response = await finish
      accepted = domain(await response.json())
      const marker = acceptedMarker(response.status(), accepted)
      check(new globalThis.URL(accepted.status_url, values['base-url']).pathname === `/api/v1/student/sessions/${report.session_id}/submission/`, 'UNEXPECTED_STATUS_JOB_URL')
      await expect(page.locator('[name="answer"]').first()).toBeDisabled({ timeout: remaining() })
      row.accepted = marker; row.accepted_http_status = response.status(); row.frozen_before_marker = true
      reloadNavigation = navigation + 1
      writeAcceptedMarker(markerPath, marker); row.accepted_marker_written = true; save()
      await page.reload({ timeout: remaining(), waitUntil: 'domcontentloaded' })
      row.reload_status_receipt = await reloadedStatus
      check(row.reload_status_receipt.submission_id === marker.submission_id, 'RELOAD_CREATED_DIFFERENT_JOB')
      await expect(page).toHaveURL(new globalThis.URL(`/student/sessions/${report.session_id}/complete/`, values['base-url']).href, { timeout: remaining() })
      await expect(page.locator('#session-summary')).toContainText('已完成', { timeout: remaining() })
      const completed = report.status_receipts.find(entry => entry.submission_id === marker.submission_id && entry.status === 'completed' && entry.navigation >= reloadNavigation)
      check(completed, 'NO_COMPLETED_RECEIPT_AFTER_RELOAD')
      row.completed = completed; row.completion_page_rendered = true
      row.after_reload_finish_post_count = report.http.filter(entry => entry.navigation >= reloadNavigation && entry.method === 'POST' && /\/finish\/$/.test(entry.path)).length
      row.after_reload_enter_post_count = report.http.filter(entry => entry.navigation >= reloadNavigation && entry.method === 'POST' && /\/sessions\/\d+\/questions\/\d+\/$/.test(entry.path)).length
    }, { onTimeout: () => stop('ACTION_TIMEOUT') })
    report.status = row.status === 'completed' ? 'browser_witness_completed_worker_and_db_audit_required' : 'failed_or_incomplete'
  } catch (error) {
    report.setup_error_code = /^[A-Z_]{1,80}$/.test(error.code || '') ? error.code : 'BROWSER_SETUP_FAILED'
    row.reason = report.setup_error_code; report.status = 'failed_or_incomplete'
  } finally {
    globalThis.clearInterval(monitor)
    await context?.close().catch(() => {}); await browser?.close().catch(() => {}); await server?.close().catch(() => {})
    report.browser_closed = true; report.finished_at_utc = new Date().toISOString(); save()
    if (row.status !== 'completed') process.exitCode = 2
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) await main()
