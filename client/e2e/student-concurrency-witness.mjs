/** Frozen real-browser login, mixed saving, full-flow and concentrated submission.
 * --plan FILE --plan-sha256 SHA --fixture FILE --attestation FILE
 * --dut-resources FILE --base-url URL --records FILE --resources FILE
 * Optional: --release-at-ms EPOCH --armed-file FILE --release-file FILE.
 * No preauthentication occurs for the measured login cohort. Output never says pass.
 */
import { createHash } from 'node:crypto'
import { appendFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { freemem, hostname, loadavg, platform, totalmem } from 'node:os'
import { dirname, resolve } from 'node:path'
import { spawnSync } from 'node:child_process'
import { performance } from 'node:perf_hooks'
import process from 'node:process'
import { clearInterval, clearTimeout, setInterval, setTimeout } from 'node:timers'
import { fileURLToPath, pathToFileURL, URL } from 'node:url'
import { isDeepStrictEqual, parseArgs } from 'node:util'
import { chromium, expect } from '@playwright/test'
import { linuxResourceEvidence, operable, prepareBrowser, startupExecutionPolicy, startupWitness, switchQuestionWitness } from './prepared-startup-witness.mjs'
import { startFixtureTask } from './school-navigation-probe.mjs'

const hash = bytes => createHash('sha256').update(bytes).digest('hex')
const pause = ms => new Promise(done => setTimeout(done, Math.max(0, ms)))
const now = () => performance.now()
const data = value => value?.data || value
const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..')

export function selectRoster(accounts, mode, count) {
  if (![3, 20, 50, 180].includes(count) || accounts.length !== 180
    || new Set(accounts.map(row => row.account_id)).size !== 180) throw new Error('Explicit 180-person fixture and diagnostic denominator required')
  if (!['login', 'login-mixed', 'startup-mixed', 'full-flow', 'submission'].includes(mode)) throw new Error('Unknown mode')
  if (mode === 'login-mixed' || mode === 'startup-mixed') {
    const active = accounts.filter(row => row.initial_state === 'active')
    const fresh = accounts.filter(row => row.initial_state === 'prepared')
    if (active.length !== 60 || fresh.length !== 120) throw new Error('Mixed fixture must contain 60 active and 120 prepared')
    const saving = Math.floor(count / 3)
    return [...active.slice(0, saving).map(row => ({ ...row, role: 'save' })),
      ...fresh.slice(0, count - saving).map(row => ({ ...row, role: mode === 'startup-mixed' ? 'startup' : 'login' }))]
  }
  if (mode !== 'submission' && accounts.some(row => row.initial_state !== 'prepared')) throw new Error('Fresh login cohort required')
  return accounts.slice(0, count).map(row => ({ ...row, role: mode === 'submission' ? 'submission' : 'login' }))
}

export function validateAttestation(attestation, machineId) {
  if (!machineId || attestation.load_machine_id !== machineId || !attestation.dut_machine_id
    || attestation.dut_machine_id === machineId || !attestation.dut_identity_command || !attestation.load_identity_command
    || !Number.isFinite(Date.parse(attestation.collected_at)) || !/^[a-f0-9]{64}$/.test(attestation.resource_evidence_sha256 || '')) {
    throw new Error('Independent host attestation differs from actual local identity or lacks DUT collection evidence')
  }
  return true
}

function localMachineId() {
  if (platform() === 'linux') return readFileSync('/etc/machine-id', 'utf8').trim()
  if (platform() === 'win32') {
    const result = spawnSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command',
      '(Get-CimInstance Win32_ComputerSystemProduct).UUID'], { encoding: 'utf8', windowsHide: true, timeout: 10000 })
    if (result.status === 0 && result.stdout.trim()) return result.stdout.trim()
  }
  throw new Error('A collected local machine identity is required')
}

export async function prepareLogin(page, account) {
  await page.goto('/login/', { waitUntil: 'domcontentloaded' })
  await page.getByLabel('账号或学号').fill(account.username)
  await page.getByLabel('密码').fill(account.password)
}

export async function releaseLogin(page, account, { waitReady = async () => {
  await expect(page).toHaveURL(/\/student\/$/)
  const course = page.locator(`a[href="/student/courses/${account.courseId}/"]`).first()
  await expect(course).toBeVisible()
  await expect(course).toBeEnabled()
} } = {}) {
  const loginHttpAttempts = []
  const responsePromise = page.waitForResponse(async response => {
    if (response.request().method() !== 'POST' || new URL(response.url()).pathname !== '/api/v1/auth/login/') return false
    const attempt = { status: response.status(), at: Date.now() }
    loginHttpAttempts.push(attempt)
    if (response.status() !== 429) return true
    const body = await response.json().catch(() => null)
    attempt.code = data(body)?.error?.code || null
    return attempt.code !== 'LOGIN_BUSY'
  }).catch(() => null)
  const clicked = now()
  await page.getByRole('button', { name: '登录', exact: true }).click()
  const response = await responsePromise
  if (!response || !response.ok() || await response.finished()) throw new Error('UI login did not authenticate')
  await waitReady()
  const ready = now()
  const timing = response.request().timing()
  return { ui_login: true, login_ms: ready - clicked,
    login_http_ms: timing.requestStart >= 0 && timing.responseEnd >= timing.requestStart ? timing.responseEnd - timing.requestStart : null,
    login_http_status: response.status(), login_http_attempts: loginHttpAttempts,
    login_clicked_driver_ms: clicked, course_operable_driver_ms: ready }
}

async function saveAnswer(prepared, answer, value = answer.value) {
  const { page } = prepared
  const expectedPath = `/student/sessions/${prepared.account.session_id}/questions/${answer.assignment_id}/`
  await expect(page).toHaveURL(url => url.pathname === expectedPath)
  const responsePromise = page.waitForResponse(response => response.request().method() === 'POST'
    && /\/student\/attempts\/\d+\/draft\/$/.test(new URL(response.url()).pathname)).catch(() => null)
  const began = now()
  const input = page.locator('[name="answer"]').first()
  const type = await input.getAttribute('type')
  if (type === 'radio') await page.locator(`[name="answer"][value=${JSON.stringify(String(value))}]`).check()
  else if (type === 'checkbox') {
    const values = Array.isArray(value) ? value.map(String) : [String(value)]
    for (const option of await page.locator('[name="answer"]').all()) await option.setChecked(values.includes(await option.getAttribute('value')))
  } else { await input.fill(String(value)); await input.blur() }
  const response = await responsePromise
  if (!response || !response.ok() || await response.finished()) throw new Error('Draft receipt failed')
  const receipt = data(await response.json())
  if (receipt.success !== true || receipt.response_version == null || !(receipt.active_response_event_id || receipt.target_event_id)) throw new Error('Draft lacks authoritative saved head')
  const timing = response.request().timing()
  return { assignment_id: answer.assignment_id, value, response_version: receipt.response_version,
    target_event_id: receipt.active_response_event_id || receipt.target_event_id,
    http_ms: timing.requestStart >= 0 && timing.responseEnd >= timing.requestStart ? timing.responseEnd - timing.requestStart : null,
    editing_ms: now() - began, http_status: response.status() }
}

async function verifyAnswers(prepared, samples) {
  for (const sample of samples) {
    const response = await prepared.context.request.get(`/api/v1/student/sessions/${prepared.account.session_id}/questions/${sample.assignment_id}/`)
    const question = data(await response.json())
    if (!response.ok() || JSON.stringify(question.draft_answer) !== JSON.stringify(sample.value)
      || question.process?.active_response_event_id !== sample.target_event_id
      || question.process?.response_version !== sample.response_version) throw new Error('Saved answer/server pointer differs')
  }
  return samples.map(sample => sample.assignment_id)
}

async function prepareAnswers(prepared, record) {
  const start = await startFixtureTask(prepared.page, prepared.account, () => operable(prepared.page))
  const began = now()
  await start()
  record.startup_ms = now() - began
  const [first, second] = prepared.row.answers
  const one = await saveAnswer(prepared, first)
  await switchQuestionWitness(prepared, second.assignment_id)
  const two = await saveAnswer(prepared, second)
  await switchQuestionWitness(prepared, first.assignment_id)
  record.save_samples = [one, two]
  record.verified_assignment_ids = await verifyAnswers(prepared, record.save_samples)
  record.answers_verified = record.verified_assignment_ids.length
}

async function submit(prepared, record, deadline) {
  const responsePromise = prepared.page.waitForResponse(response => response.request().method() === 'POST'
    && /\/sessions\/\d+\/finish\/$/.test(new URL(response.url()).pathname)).catch(() => null)
  const began = now()
  await prepared.page.locator('[data-finish-request]').first().click()
  const confirm = prepared.page.locator('[data-paper-submit-confirm]')
  const first = await Promise.race([confirm.waitFor({ state: 'visible' }).then(() => 'confirm').catch(() => 'absent'), responsePromise.then(() => 'receipt')])
  if (first === 'confirm') await confirm.click()
  const response = await responsePromise
  if (!response || response.status() !== 202 || await response.finished()) throw new Error('Submission was not durably accepted')
  const receipt = data(await response.json())
  if (!receipt.submission_id || !receipt.status_url) throw new Error('Submission receipt incomplete')
  record.accepted_ms = now() - began
  const timing = response.request().timing()
  record.accepted_http_ms = timing.requestStart >= 0 && timing.responseEnd >= timing.requestStart ? timing.responseEnd - timing.requestStart : null
  record.submission_id = receipt.submission_id
  const statusURL = new URL(receipt.status_url, prepared.page.url())
  if (statusURL.origin !== new URL(prepared.page.url()).origin
    || statusURL.pathname !== `/api/v1/student/sessions/${prepared.account.session_id}/submission/`) throw new Error('Submission status binding differs')
  // The application owns polling. Wait for its terminal navigation without a second poller.
  await prepared.page.waitForURL(url => url.pathname === `/student/sessions/${prepared.account.session_id}/complete/`,
    { timeout: Math.max(1, deadline - now()) })
  const final = await prepared.context.request.get(statusURL.href)
  const committed = data(await final.json())
  if (!final.ok() || committed.status !== 'completed' || committed.submission_id !== receipt.submission_id) throw new Error('Submission not terminal')
  record.final_status = committed.status
  record.terminal_ms = now() - began
}

export async function runReleased(prepared, plan, releaseAt, { runStartup = startupWitness } = {}) {
  const record = { account_id: prepared.account.account_id, plan_id: plan.plan_id, run_id: plan.run_id,
    fresh_context: true, status: 'failed', role: prepared.row.role, save_samples: [], ...prepared.preparation }
  await pause(releaseAt - Date.now())
  record.release_skew_ms = Math.max(0, Date.now() - releaseAt)
  record.started_at_epoch_ms = Date.now()
  const began = now()
  const budget = plan.mode === 'startup-mixed' ? plan.operation_timeout_ms : plan.completion_timeout_ms
  const deadline = began + budget
  const timer = setTimeout(() => { record.total_timeout = true; void prepared.context.close() }, budget)
  try {
    if (prepared.row.role === 'login') Object.assign(record, await releaseLogin(prepared.page, prepared.account))
    if (prepared.row.role === 'startup') {
      const raw = await runStartup(prepared, { run_id: plan.run_id, scenario_id: 'startup-mixed',
        input_settings_hash: plan.plan_id }, releaseAt, false)
      record.startup_witness = raw
      if (Number.isFinite(raw.observer_start_epoch_ms)) record.release_skew_ms = Math.max(record.release_skew_ms, raw.observer_start_epoch_ms - releaseAt)
      const click = raw.timestamps_ms?.click, ready = raw.timestamps_ms?.operable
      if (raw.status !== 'completed' || !raw.phase_origin_id || !Number.isFinite(click) || !Number.isFinite(ready)
        || click < 0 || ready < click || ready - click > plan.operation_timeout_ms) throw new Error('Startup click/operable evidence incomplete')
      record.startup_ms = ready - click
    }
    if (prepared.row.role === 'save') {
      const answer = prepared.row.answers[0]
      for (let round = 0; round < plan.save_rounds; round++) {
        await pause(Math.max(0, began + round * plan.save_interval_ms - now()))
        const value = round % 2 === 0 ? 'B' : 'A'
        record.save_samples.push(await saveAnswer(prepared, answer, value))
      }
      if (plan.mode === 'startup-mixed') await pause(Math.max(0, began + plan.save_rounds * plan.save_interval_ms - now()))
      await verifyAnswers(prepared, record.save_samples.slice(-1))
    } else if (plan.mode === 'full-flow') {
      await prepareAnswers(prepared, record)
      await submit(prepared, record, deadline)
    } else if (plan.mode === 'submission') await submit(prepared, record, deadline)
    record.flow_ms = now() - began
    record.status = 'completed'
  } catch (error) {
    record.error_code = record.total_timeout || error.name === 'TimeoutError' ? 'operation_timeout' : 'browser_action_failed'
    record.error_name = error.name
  } finally {
    clearTimeout(timer)
    record.ended_at_epoch_ms = Date.now()
  }
  return record
}

function sample(browserPid, stage) {
  return { stage, sampled_at: new Date().toISOString(), host: hostname(), total_memory_bytes: totalmem(),
    available_physical_bytes: freemem(), node_rss_bytes: process.memoryUsage().rss, load_average: loadavg(),
    ...(platform() === 'linux' ? linuxResourceEvidence(browserPid) : {}) }
}

export function validateInputs(plan, fixture, attestation, resources, baseURL, machineId) {
  validateAttestation(attestation, machineId)
  if (fixture.isolated_demo !== true || attestation.target_origin !== new URL(baseURL).origin
    || plan.run_id !== fixture.run_id || plan.fixture_spec_sha256 !== fixture.plan_sha
    || plan.count !== plan.roster.length || !['login', 'login-mixed', 'startup-mixed', 'full-flow', 'submission'].includes(plan.mode)) throw new Error('Frozen isolated target binding differs')
  if (Math.abs(Date.now() - Date.parse(attestation.collected_at)) > 86400000) throw new Error('Host attestation is older than 24 hours')
  const selected = selectRoster(fixture.accounts, plan.mode, plan.count)
  if (new Set(fixture.accounts.map(a => a.username)).size !== 180) throw new Error('Duplicate username')
  for (let index = 0; index < selected.length; index++) {
    const row = plan.roster[index], account = selected[index]
    if (row.account_id !== account.account_id || row.role !== account.role || row.task_id !== account.taskId
      || row.session_id !== account.session_id || row.course_id !== account.courseId || !account.password
      || row.answers.length !== 2 || row.answers.some(answer => !account.questions.some(q => q.assignment_id === answer.assignment_id))) throw new Error('Frozen account/action binding differs')
    if (row.role === 'startup' && (row.attempt_id !== `${plan.run_id}:startup-mixed:${account.account_id}`
      || row.cache_state !== 'cold' || row.transport !== 'browser' || row.arrival_offset_seconds !== 0)) throw new Error('Frozen startup clock/cache binding differs')
  }
  if (!resources.settings.resource_budget || resources.settings.admission_mode !== 'postgres_transaction'
    || !startupExecutionPolicy(resources.settings).executable) throw new Error('DUT worker/database budgets are not verified')
  if (!isDeepStrictEqual(plan.settings, resources.settings)) throw new Error('Frozen DUT settings differ')
  for (const name of ['client/e2e/student-concurrency-witness.mjs', 'server/scripts/load/student_concurrency.py',
    'client/e2e/prepared-startup-witness.mjs', 'client/e2e/school-navigation-probe.mjs']) {
    if (!/^[a-f0-9]{64}$/.test(plan.source_sha256?.[name] || '')) throw new Error('Missing frozen harness source hash')
  }
  for (const [name, sha] of Object.entries(plan.source_sha256)) {
    if (!name.startsWith('client/e2e/') && !name.startsWith('server/scripts/load/')) throw new Error('Unexpected harness source path')
    if (hash(readFileSync(resolve(root, name))) !== sha) throw new Error('Frozen harness source differs')
  }
  return selected
}

async function main() {
  const options = Object.fromEntries(['plan', 'plan-sha256', 'fixture', 'attestation', 'dut-resources', 'base-url', 'records', 'resources',
    'release-at-ms', 'armed-file', 'release-file', 'min-free-mib'].map(name => [name, { type: 'string' }]))
  const { values } = parseArgs({ options })
  for (const name of ['plan', 'plan-sha256', 'fixture', 'attestation', 'dut-resources', 'base-url', 'records', 'resources']) if (!values[name]) throw new Error(`Missing --${name}`)
  const read = path => JSON.parse(readFileSync(path, 'utf8').replace(/^\uFEFF/, ''))
  const plan = read(values.plan), fixture = read(values.fixture), attestation = read(values.attestation), dut = read(values['dut-resources'])
  if (hash(readFileSync(values.plan)) !== values['plan-sha256'] || hash(readFileSync(values.fixture)) !== plan.fixture_sha256
    || hash(readFileSync(values.attestation)) !== plan.attestation_sha256 || hash(readFileSync(values['dut-resources'])) !== plan.dut_resources_sha256
    || attestation.resource_evidence_sha256 !== plan.dut_resources_sha256) throw new Error('Frozen input file hash differs')
  const machineId = localMachineId()
  const selected = validateInputs(plan, fixture, attestation, dut, values['base-url'], machineId)
  const minimum = Number(values['min-free-mib'] || '1024') * 1024 * 1024
  if (!Number.isFinite(minimum) || minimum < 256 * 1024 * 1024) throw new Error('Require at least 256 MiB free memory floor')
  for (const path of [values.records, values.resources]) { mkdirSync(dirname(resolve(path)), { recursive: true }); writeFileSync(path, '', { flag: 'wx', mode: 0o600 }) }
  const report = { plan_id: plan.plan_id, run_id: plan.run_id, count: plan.count, mode: plan.mode, load_machine_id: machineId,
    dut_machine_id: attestation.dut_machine_id, attestation_sha256: plan.attestation_sha256, dut_resources_sha256: plan.dut_resources_sha256,
    source_sha256: plan.source_sha256, sample_interval_ms: 2000, minimum_free_bytes: minimum, samples: [], aborted: false,
    node_version: process.version, capacity_verdict: null }
  let server, browser, monitor
  const prepared = [], emitted = new Set()
  const emit = record => { emitted.add(record.account_id); appendFileSync(values.records, JSON.stringify(record) + '\n') }
  const checkResources = stage => {
    const current = sample(server?.process().pid || null, stage)
    report.samples.push(current)
    const healthy = current.available_physical_bytes >= minimum
      && (current.cgroup_available_bytes == null || current.cgroup_available_bytes >= 256 * 1024 * 1024)
    if (!healthy) { report.aborted = true; void browser?.close() }
    return healthy
  }
  try {
    if (!checkResources('before_launch')) throw new Error('Load host memory budget exhausted')
    server = await chromium.launchServer({ headless: true })
    browser = await chromium.connect(server.wsEndpoint())
    report.chromium_version = browser.version()
    monitor = setInterval(() => checkResources('runtime'), 2000)
    for (let index = 0; index < selected.length; index++) {
      const account = selected[index], row = plan.roster[index]
      let context
      try {
        if (!checkResources('before_context')) throw new Error('Load host memory budget exhausted')
        if (row.role === 'startup') {
          prepared.push(await prepareBrowser(browser, values['base-url'], account, row, plan.operation_timeout_ms))
          continue
        }
        context = await browser.newContext({ baseURL: values['base-url'], viewport: { width: 1440, height: 900 } })
        const page = await context.newPage()
        page.setDefaultTimeout(plan.operation_timeout_ms)
        const current = { page, context, account, row, operationTimeoutMs: plan.operation_timeout_ms, preparation: {} }
        await prepareLogin(page, account)
        if (row.role !== 'login') {
          await releaseLogin(page, account)
          if (row.role === 'save') { await page.goto(account.question_url); await operable(page) }
          else await prepareAnswers(current, current.preparation)
        }
        prepared.push(current)
      } catch (error) {
        await context?.close().catch(() => {})
        emit({ account_id: account.account_id, run_id: plan.run_id, plan_id: plan.plan_id,
          status: report.aborted ? 'not_executed' : 'failed', error_code: report.aborted ? 'load_resource_abort' : 'preparation_failed', error_name: error.name })
      }
    }
    if (values['armed-file']) writeFileSync(values['armed-file'], JSON.stringify({ plan_id: plan.plan_id, prepared_count: prepared.length,
      expected_count: plan.count, load_machine_id: machineId }), { flag: 'wx', mode: 0o600 })
    let releaseAt = Number(values['release-at-ms'] || Date.now() + 2000)
    if (values['release-file']) {
      const deadline = Date.now() + 180000
      while (!existsSync(values['release-file'])) { if (Date.now() >= deadline) throw new Error('Release timed out'); await pause(100) }
      const release = read(values['release-file'])
      if (release.plan_id !== plan.plan_id) throw new Error('Release plan differs')
      releaseAt = release.release_at_ms
    }
    if (!Number.isFinite(releaseAt) || releaseAt < Date.now()) throw new Error('Shared release must be in the future')
    report.release_at_ms = releaseAt
    await Promise.all(prepared.map(async current => emit(await runReleased(current, plan, releaseAt))))
  } catch (error) { report.runner_error_name = error.name; report.aborted = true }
  finally {
    clearInterval(monitor)
    for (const current of prepared) await current.context.close().catch(() => {})
    await browser?.close().catch(() => {})
    await server?.close().catch(() => {})
    for (const account of selected) if (!emitted.has(account.account_id)) emit({ account_id: account.account_id,
      plan_id: plan.plan_id, run_id: plan.run_id, status: 'not_executed', error_code: 'runner_aborted' })
    report.samples.push(sample(null, 'after_close'))
    writeFileSync(values.resources, JSON.stringify(report, null, 2))
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) await main()
