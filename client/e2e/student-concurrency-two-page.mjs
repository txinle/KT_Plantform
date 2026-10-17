/** Exactly two local UI journeys. This witness does not evaluate server capacity. */
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { freemem, totalmem, platform } from 'node:os'
import { spawnSync } from 'node:child_process'
import { pathToFileURL } from 'node:url'
import { parseArgs } from 'node:util'
import process from 'node:process'
import { expect } from '@playwright/test'
import { prepareLogin, releaseLogin } from './student-concurrency-witness.mjs'
import { operable, startupWitness } from './prepared-startup-witness.mjs'
import { createVisualProbeContext, captureFirstReadyLayout, captureVisualLayouts, captureBrowserViewport,
  startFixtureTask, questionNavigationSteps } from './school-navigation-probe.mjs'

const operationTimeoutMs = 60000
const submissionTimeoutMs = 300000
const domain = body => body?.data || body
const sha = bytes => createHash('sha256').update(bytes).digest('hex')
const questionPath = (account, assignment) => `/student/sessions/${account.session_id}/questions/${assignment}/`

async function resourceSample(stage, browserSession) {
  const sample = { stage, at: Date.now(), available_physical_bytes: freemem(), total_physical_bytes: totalmem(),
    node_rss_bytes: process.memoryUsage().rss, node_cpu_microseconds: process.cpuUsage(), browser_processes: [] }
  if (!browserSession) return sample
  try {
    const result = await browserSession.send('SystemInfo.getProcessInfo')
    sample.browser_processes = result.processInfo
    const ids = result.processInfo.map(row => Number(row.id)).filter(id => Number.isSafeInteger(id) && id > 0)
    if (platform() === 'win32' && ids.length) {
      const probe = spawnSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command',
        `@(Get-Process -Id @(${ids.join(',')}) -ErrorAction SilentlyContinue | Select-Object Id,WorkingSet64,CPU) | ConvertTo-Json -Compress`],
      { encoding: 'utf8', windowsHide: true, timeout: 10000 })
      if (probe.status !== 0) throw new Error('Owned browser RSS probe failed')
      const values = JSON.parse(probe.stdout.replace(/^\uFEFF/, ''))
      sample.browser_os_processes = Array.isArray(values) ? values : [values]
      sample.browser_rss_sum_bytes = sample.browser_os_processes.reduce((sum, row) => sum + row.WorkingSet64, 0)
      sample.rss_scope = 'Sum of owned Chromium process working sets; includes shared pages; phase checkpoints only.'
    }
  } catch (error) { sample.probe_error = { name: error.name, message: error.message } }
  return sample
}

export function validateTwoPageFixture(fixture, baseURL, ids) {
  const target = new globalThis.URL(baseURL)
  assert(target.protocol === 'http:' && ['127.0.0.1', 'localhost'].includes(target.hostname)
    && target.port === '18082', 'Use only the authorized local18082 tunnel')
  assert(fixture.isolated_demo === true && Array.isArray(fixture.accounts), 'Explicit isolated demo fixture required')
  const accounts = ids ? ids.map(id => fixture.accounts.find(account => account.account_id === id)) : fixture.accounts
  assert(accounts.length === 2 && accounts.every(Boolean), 'Select exactly two explicit accounts')
  for (const field of ['account_id', 'username', 'session_id']) assert(new Set(accounts.map(row => row[field])).size === 2, `Independent ${field} required`)
  for (const account of accounts) {
    assert(account.username && account.password && account.initial_state === 'prepared', 'Fresh prepared fixture accounts required')
    for (const key of ['session_id', 'courseId', 'taskId']) assert(Number.isSafeInteger(account[key]) && account[key] > 0, `Invalid ${key}`)
    assert(account.questions?.length >= 2 && new Set(account.questions.map(row => row.assignment_id)).size === account.questions.length,
      'At least two distinct assigned questions required')
    assert(account.questions.every(row => Number.isSafeInteger(row.assignment_id) && row.assignment_id > 0), 'Invalid assignment identity')
  }
  return accounts
}

export function validateTerminalSubmission(session, status, receipt, terminal, path) {
  assert(status === 202 && receipt?.submission_id && receipt.status_url, 'Durable submission acceptance missing')
  assert(terminal?.status === 'completed' && terminal.submission_id === receipt.submission_id, 'Submission is not terminal or has a different identity')
  assert(path === `/student/sessions/${session}/complete/`, 'Completion page session differs')
  return true
}

export function classifyConsoleErrors(messages, baseURL) {
  const errors = messages.filter(row => row.type === 'error')
  const favicon = new globalThis.URL('/favicon.ico', baseURL).href
  const known = row => row.location?.url === favicon
    && row.text === 'Failed to load resource: the server responded with a status of 404 (Not Found)'
  return { strict_console_passed: errors.length === 0, known_asset_errors: errors.filter(known),
    unexpected_console_errors: errors.filter(row => !known(row)) }
}

function confirmedLeaseRecoveries(record, offline) {
  if (offline?.status !== 'completed' || !offline.server_confirmed_target_event_id
    || !Number.isSafeInteger(offline.server_confirmed_response_version)
    || !Array.isArray(offline.application_errors) || offline.application_errors.length
    || !Array.isArray(record?.page_errors) || record.page_errors.length) return []
  const requests = record.requests || []
  return requests.flatMap(renew => {
    const match = /^\/api\/v1\/assessment\/process\/attempts\/(\d+)\/writer-lease\/renew\/$/.exec(renew.path)
    const client = renew.capture_request?.client_instance_sha256
    if (!match || renew.method !== 'POST' || renew.status !== 409 || !client
      || renew.at < offline.started_at || renew.at > offline.completed_at
      || renew.error_evidence?.body?.error?.code !== 'writer_lease_conflict') return []
    const acquire = requests.find(row => row.method === 'POST' && row.at >= renew.at
      && row.path === renew.path.replace('/renew/', '/acquire/'))
    if (!acquire || ![200, 201].includes(acquire.status)
      || acquire.capture_request?.client_instance_sha256 !== client
      || acquire.lease_response?.success !== true || acquire.lease_response.client_instance_sha256 !== client) return []
    const draft = requests.find(row => row.method === 'POST' && row.at >= acquire.at
      && row.path === `/api/v1/student/attempts/${match[1]}/draft/`)
    const head = offline.server_confirmed_target_event_id
    if (!draft || draft.status !== 200 || draft.at > offline.completed_at
      || draft.capture_request?.client_instance_sha256 !== client || draft.capture_request.target_event_id !== head
      || draft.capture_response?.success !== true || draft.capture_response.active_response_event_id !== head
      || draft.capture_response.response_version !== offline.server_confirmed_response_version) return []
    const original = requests.find(row => row.at >= offline.started_at && row.at <= draft.at
      && row.capture_request?.client_instance_sha256 === client
      && row.capture_request.events?.some(event => event.event_type === 'response_transition' && event.event_id === head))
    if (!original) return []
    return [{ renew_path: renew.path, renew_at: renew.at, acquire_at: acquire.at, draft_at: draft.at,
      target_event_id: head, client_instance_sha256: client, server_confirmed_response_version: offline.server_confirmed_response_version,
      renew_to_acquire_ms: acquire.at - renew.at, renew_to_durable_ms: offline.completed_at - renew.at }]
  })
}

export function classifyCapacityConsoleErrors(messages, baseURL, offline, login, record) {
  const classified = classifyConsoleErrors(messages, baseURL)
  const origin = new globalThis.URL(baseURL).origin
  const expected = row => {
    if (!offline || row.at < offline.started_at || row.at > offline.completed_at
      || row.text !== 'Failed to load resource: net::ERR_INTERNET_DISCONNECTED') return false
    try { return new globalThis.URL(row.location?.url).origin === origin } catch { return false }
  }
  let remainingBusy = login?.login_http_status === 200
    ? (login.login_http_attempts || []).filter(row => row.status === 429 && row.code === 'LOGIN_BUSY').length : 0
  const expectedBusy = new Set(classified.unexpected_console_errors.filter(row => {
    if (!remainingBusy || row.text !== 'Failed to load resource: the server responded with a status of 429 (Too Many Requests)') return false
    try {
      const url = new globalThis.URL(row.location?.url)
      if (url.origin !== origin || url.pathname !== '/api/v1/auth/login/') return false
    } catch { return false }
    remainingBusy -= 1
    return true
  }))
  const chains = confirmedLeaseRecoveries(record, offline)
  const matchedChains = []
  const expectedLease = new Set(classified.unexpected_console_errors.filter(row => {
    if (row.text !== 'Failed to load resource: the server responded with a status of 409 (Conflict)') return false
    let url
    try { url = new globalThis.URL(row.location?.url) } catch { return false }
    const chain = chains.find(value => !matchedChains.includes(value) && url.origin === origin
      && url.pathname === value.renew_path && row.at >= value.renew_at && row.at <= value.draft_at)
    if (!chain) return false
    matchedChains.push(chain)
    return true
  }))
  return { ...classified, expected_offline_console_errors: classified.unexpected_console_errors.filter(expected),
    expected_login_busy_console_errors: [...expectedBusy], expected_lease_recovery_console: [...expectedLease],
    lease_recovery_chains: matchedChains,
    unexpected_console_errors: classified.unexpected_console_errors.filter(row => !expected(row) && !expectedBusy.has(row) && !expectedLease.has(row)) }
}

export function captureRequestSummary(path, body) {
  if (/^\/api\/v1\/assessment\/process\/attempts\/\d+\/writer-lease\/(?:renew|acquire)\/$/.test(path)) {
    return { client_instance_sha256: body?.client_instance_id ? sha(String(body.client_instance_id)) : null }
  }
  if (!/\/(?:capture-v2\/batch|attempts\/\d+\/(?:draft|save-and-leave))\/$/.test(path)) return undefined
  const bundle = body?.capture_bundle || body || {}
  const pick = (value, keys) => Object.fromEntries(keys.filter(key => value?.[key] !== undefined).map(key => [key, value[key]]))
  return { ...pick(body, ['capture_operation_id', 'expected_response_version', 'target_event_id', 'response_version']),
    event_boundary: bundle.event_boundary,
    client_instance_sha256: bundle.client_instance_id ? sha(String(bundle.client_instance_id)) : null,
    events: (bundle.events || []).map(event => ({ ...pick(event, ['event_id', 'event_type', 'event_version', 'client_sequence_no']),
      ...pick(event.payload, ['parent_event_id', 'before_state_hash', 'after_state_hash']) })),
    checkpoints: (bundle.checkpoints || []).map(row => pick(row, ['checkpoint_id', 'highest_emitted_sequence_no',
      'highest_acked_sequence_no', 'highest_contiguous_acked_sequence_no', 'pending_event_count', 'failed_event_count',
      'missing_local_sequence_ranges', 'quality_flags'])) }
}

export function captureErrorEvidence(status, headers, body) {
  const scrub = value => {
    if (Array.isArray(value)) return value.map(scrub)
    if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value)
      .filter(([key]) => !/password|token|csrf|cookie|authorization|secret/i.test(key)).map(([key, item]) => [key, scrub(item)]))
    if (typeof value === 'string' && /password|token|csrf|cookie|authorization|secret/i.test(value)) return '[credential-related response text omitted]'
    return value
  }
  return { status, request_id: headers['x-request-id'] || null, content_type: headers['content-type'] || null, body: scrub(body) }
}

export function validateCompletionFields(fields) {
  assert.equal(Number(fields['已正式提交']), 2, 'Completion UI must show2 formally submitted answers')
  assert.equal(Number(fields['分配题目']), 20, 'Completion UI must show20 assigned questions')
  return { submitted_count: 2, assigned_count: 20, unanswered_count_displayed: false }
}

export function twoPageSummary(records) {
  assert(records.length === 2 && new Set(records.map(row => row.account_id)).size === 2, 'Retain both independent page outcomes')
  return { scope: 'two_real_page_functional_verification', capacity_evaluated: false, planned_pages: 2,
    strict_console_passed: records.every(row => row.strict_console_passed === true),
    known_asset_error_count: records.reduce((sum, row) => sum + (row.known_asset_errors?.length || 0), 0),
    unexpected_console_error_count: records.reduce((sum, row) => sum + (row.unexpected_console_errors?.length || 0), 0),
    expected_lease_recovery_console_error_count: records.reduce((sum, row) => sum + (row.expected_lease_recovery_console?.length || 0), 0),
    completed_pages: records.filter(row => row.status === 'completed').length,
    failed_pages: records.filter(row => row.status === 'failed').length,
    not_executed_pages: records.filter(row => row.status === 'not_executed').length,
    passed: records.every(row => row.status === 'completed' && !(row.unexpected_console_errors?.length)) }
}

function installEvidence(page, record) {
  const pending = new Set()
  const requests = new Map()
  const projection = (dto, path) => {
    if (!dto?.assignment || !dto.process || !dto.actions) return
    record.runtime_projections.push({ path, assignment_id: dto.assignment.assignment_id,
      session_id: dto.session_id, research_tools_url: dto.actions.research_tools_url || null })
  }
  page.on('console', message => record.console.push({ type: message.type(), text: message.text(), location: message.location(), at: Date.now() }))
  page.on('pageerror', error => record.page_errors.push({ name: error.name, message: error.message, stack: error.stack, at: Date.now() }))
  page.on('request', request => {
    const path = new globalThis.URL(request.url()).pathname
    const row = { source: 'page', path, method: request.method(), resource_type: request.resourceType(), at: Date.now(), state: 'pending' }
    requests.set(request, row)
    record.requests.push(row)
    if (request.method() === 'POST') {
      try { row.capture_request = captureRequestSummary(path, request.postDataJSON()) }
      catch { row.capture_request = { parse_failed: true } }
    }
    if (request.method() === 'POST' && /\/(draft|save-and-leave)\/$/.test(path)) {
      const body = request.postDataJSON()
      record.answer_mutations.push({ path, answer: body?.answer, inline_confidence: body?.inline_confidence ?? null })
    }
  })
  page.on('requestfinished', request => { const row = requests.get(request); if (row) row.state = 'finished' })
  page.on('requestfailed', request => { const row = requests.get(request); if (row) { row.state = 'failed'; row.failure = request.failure()?.errorText } })
  page.on('response', response => {
    const row = requests.get(response.request())
    if (row) row.status = response.status()
    if (row?.capture_request && !response.ok()) {
      const headers = response.headers()
      const read = (headers['content-type']?.includes('application/json') ? response.json() : response.text())
        .then(body => { row.error_evidence = captureErrorEvidence(response.status(), headers, body) })
        .catch(() => { row.error_evidence = { status: response.status(), request_id: headers['x-request-id'] || null, body_read_failed: true } })
        .finally(() => pending.delete(read))
      pending.add(read)
      return
    }
    if (!response.ok() || !response.headers()['content-type']?.includes('application/json')) return
    const read = response.json().then(body => {
      const dto = domain(body)
      if (row?.capture_request && /\/writer-lease\/(?:acquire|renew)\/$/.test(row.path)) {
        row.lease_response = { success: dto?.success === true,
          client_instance_sha256: dto?.client_instance_id ? sha(String(dto.client_instance_id)) : null }
      } else if (row?.capture_request && /\/draft\/$/.test(row.path)) {
        row.capture_response = { success: dto?.success === true, active_response_event_id: dto?.active_response_event_id,
          response_version: dto?.response_version }
      }
      projection(dto?.runtime || dto, new globalThis.URL(response.url()).pathname)
    })
      .catch(() => undefined).finally(() => pending.delete(read))
    pending.add(read)
  })
  return { projection, settle: () => Promise.all([...pending]) }
}

async function readServer(prepared, path) {
  const began = Date.now()
  const response = await prepared.context.request.get(path, { timeout: operationTimeoutMs })
  prepared.record.requests.push({ source: 'verification_read', path: new globalThis.URL(path, prepared.page.url()).pathname,
    method: 'GET', status: response.status(), at: began, state: 'finished' })
  assert(response.ok(), `Server verification failed: ${response.status()}`)
  const dto = domain(await response.json())
  prepared.evidence.projection(dto, path)
  return dto
}

async function blankConfidence(prepared, assignment) {
  const input = prepared.page.locator('[data-inline-confidence]')
  if (!await input.count()) return false
  await expect(input).toHaveValue('')
  await expect(prepared.page.getByText('作答信心（可选）', { exact: true })).toBeVisible()
  prepared.record.blank_confidence_observations.push({ assignment_id: assignment, value: await input.inputValue(), disabled: await input.isDisabled(), at: Date.now() })
  return true
}

async function setAnswerInput(page, value) {
  const input = page.locator('[name="answer"]').first()
  const type = await input.getAttribute('type')
  if (type === 'radio') {
    if (value === null) await page.locator('[data-clear-response]').click()
    else await page.locator(`[name="answer"][value=${JSON.stringify(value)}]`).check()
  } else if (type === 'checkbox') for (const option of await page.locator('[name="answer"]').all()) {
    await option.setChecked(value.includes(await option.getAttribute('value')))
  }
  else { await input.fill(value ?? ''); await input.blur() }
}

async function verifyInput(page, value) {
  const input = page.locator('[name="answer"]').first()
  const type = await input.getAttribute('type')
  if (Array.isArray(value)) assert.deepEqual(await page.locator('[name="answer"]:checked').evaluateAll(nodes => nodes.map(node => node.value)), value)
  else if (type === 'radio') {
    if (value === null) await expect(page.locator('[name="answer"]:checked')).toHaveCount(0)
    else await expect(page.locator(`[name="answer"][value=${JSON.stringify(value)}]`)).toBeChecked()
  } else await expect(input).toHaveValue(value ?? '')
}

async function inputAndSave(prepared, question, revision = false, options = {}) {
  const { page, record } = prepared
  await operable(page)
  const input = page.locator('[name="answer"]').first()
  const type = await input.getAttribute('type')
  let value
  if (type === 'radio' || type === 'checkbox') {
    const options = await page.locator('[name="answer"]').evaluateAll(nodes => nodes.map(node => node.value))
    assert(options.length >= 2, 'Choice revision needs two actual options')
    value = options[revision ? 1 : 0]
    if (type === 'checkbox') value = [value]
  } else value = question.question_type === 'numeric' ? (revision ? '8' : '7') : (revision ? 'local UI revised answer' : 'local UI answer')
  if (Object.hasOwn(options, 'value')) value = options.value
  const saved = page.waitForResponse(response => {
    const request = response.request()
    return request.method() === 'POST' && /\/student\/attempts\/\d+\/draft\/$/.test(new globalThis.URL(response.url()).pathname)
      && JSON.stringify(request.postDataJSON()?.answer) === JSON.stringify(value)
  }, { timeout: operationTimeoutMs })
  void saved.catch(() => undefined)
  const began = globalThis.performance.now()
  await setAnswerInput(page, value)
  const response = await saved
  assert(response.ok() && !await response.finished(), 'Draft transport failed')
  const receipt = domain(await response.json())
  assert(receipt.success === true && Number.isSafeInteger(receipt.response_version)
    && (receipt.active_response_event_id || receipt.target_event_id), 'Draft receipt lacks saved version/head')
  const sample = { assignment_id: question.assignment_id, value, revision, response_version: receipt.response_version,
    target_event_id: receipt.active_response_event_id || receipt.target_event_id, http_status: response.status(),
    edit_to_saved_ms: globalThis.performance.now() - began }
  const dto = await readServer(prepared, `/api/v1/student/sessions/${prepared.account.session_id}/questions/${question.assignment_id}/`)
  assert.deepEqual(dto.draft_answer, value, 'Saved answer differs from actual UI input')
  assert.equal(dto.process.active_response_event_id, sample.target_event_id)
  assert.equal(dto.process.response_version, sample.response_version)
  assert(dto.inline_confidence?.probability == null, 'Unfilled confidence must remain null')
  record.save_samples.push(sample)
  await blankConfidence(prepared, question.assignment_id)
  return sample
}

async function capacityAnswerChecks(prepared, question, offlineValue, output) {
  const { page, context, record, account } = prepared
  const type = await page.locator('[name="answer"]').first().getAttribute('type')
  const cleared = await inputAndSave(prepared, question, false, { value: type === 'checkbox' ? [] : type === 'radio' ? null : '' })
  await verifyInput(page, cleared.value)
  record.cleared_answer_verified = true
  await inputAndSave(prepared, question, true)
  const offline = record.offline_recovery = { started_at: Date.now(), status: 'running' }
  try {
    await context.setOffline(true)
    await setAnswerInput(page, offlineValue)
    await expect(page.locator('[data-save-status]')).toContainText('已保存到本机', { timeout: operationTimeoutMs })
    await screenshot(prepared, output, 'offline-local')
    await page.reload({ waitUntil: 'domcontentloaded', timeout: operationTimeoutMs })
    await operable(page)
    await verifyInput(page, offlineValue)
    offline.offline_reload_restored_answer = true
    await screenshot(prepared, output, 'offline-reloaded')
    const saved = page.waitForResponse(response => response.request().method() === 'POST'
      && /\/student\/attempts\/\d+\/draft\/$/.test(new globalThis.URL(response.url()).pathname)
      && JSON.stringify(response.request().postDataJSON()?.answer) === JSON.stringify(offlineValue), { timeout: operationTimeoutMs })
    void saved.catch(() => undefined)
    await context.setOffline(false)
    const response = await saved
    assert(response.ok() && !await response.finished(), 'Recovered draft did not reach server')
    const receipt = domain(await response.json())
    assert(receipt.success && Number.isSafeInteger(receipt.response_version), 'Recovered draft lacks durable version')
    const current = await readServer(prepared, `/api/v1/student/sessions/${account.session_id}/questions/${question.assignment_id}/`)
    assert.deepEqual(current.draft_answer, offlineValue)
    assert.equal(current.process.response_version, receipt.response_version)
    assert.equal(current.process.active_response_event_id, receipt.active_response_event_id || receipt.target_event_id)
    offline.server_confirmed_response_version = receipt.response_version
    offline.server_confirmed_target_event_id = current.process.active_response_event_id
    await expect(page.locator('[data-save-status]')).toContainText('草稿已保存', { timeout: operationTimeoutMs })
    offline.application_errors = await page.locator('.kt-assessment-focus [role="alert"]:visible').allTextContents()
    offline.status = 'completed'
  } finally {
    await context.setOffline(false)
    offline.completed_at = Date.now()
  }
}

async function navigate(prepared, assignment) {
  const path = questionPath(prepared.account, assignment)
  const steps = questionNavigationSteps(prepared.page, assignment, path, () => operable(prepared.page), { mobile: prepared.mobile })
  await steps.enabled(operationTimeoutMs)
  await steps.click(operationTimeoutMs)
  await steps.target(operationTimeoutMs)
  await steps.ready()
  prepared.record.navigation.push({ assignment_id: assignment, path, at: Date.now() })
}

async function screenshot(prepared, output, name) {
  const filename = `${prepared.mobile ? 'mobile' : 'desktop'}-${name}.png`
  await prepared.page.screenshot({ path: resolve(output, filename), fullPage: true, animations: 'disabled' })
  const geometry = await prepared.page.evaluate(() => ({ inner_width: globalThis.innerWidth,
    scroll_width: Math.max(globalThis.document.documentElement.scrollWidth, globalThis.document.body.scrollWidth) }))
  assert(geometry.scroll_width <= geometry.inner_width + 1, `${filename} has horizontal overflow`)
  prepared.record.screenshots.push({ filename, ...geometry })
}

async function submit(prepared) {
  const { page, record, account } = prepared
  const responsePromise = page.waitForResponse(response => response.request().method() === 'POST'
    && new globalThis.URL(response.url()).pathname.endsWith(`/sessions/${account.session_id}/finish/`), { timeout: operationTimeoutMs })
  void responsePromise.catch(() => undefined)
  const button = page.locator('[data-finish-request]')
  if (await button.isVisible()) await button.click()
  else {
    await page.locator('[data-mobile-navigation-trigger]').click()
    await page.locator('#assessment-mobile-navigation').getByRole('button', { name: '提交试卷', exact: true }).click()
  }
  const confirmation = page.locator('[data-paper-submit-confirm]')
  const first = await Promise.race([responsePromise.then(() => 'receipt'),
    confirmation.waitFor({ state: 'visible', timeout: operationTimeoutMs }).then(() => 'confirmation')])
  if (first === 'confirmation') await confirmation.click()
  const response = await responsePromise
  const receipt = domain(await response.json())
  assert.equal(response.status(), 202, 'Whole-paper submission must be durably accepted')
  const statusURL = new globalThis.URL(receipt.status_url, page.url())
  assert(statusURL.origin === new globalThis.URL(page.url()).origin
    && statusURL.pathname === `/api/v1/student/sessions/${account.session_id}/submission/`, 'Submission status scope differs')
  record.submission = { accepted_http_status: response.status(), submission_id: receipt.submission_id, accepted_at: receipt.accepted_at }
  await page.waitForURL(url => url.pathname === `/student/sessions/${account.session_id}/complete/`, { timeout: submissionTimeoutMs })
  const terminal = await readServer(prepared, statusURL.href)
  validateTerminalSubmission(account.session_id, response.status(), receipt, terminal, new globalThis.URL(page.url()).pathname)
  Object.assign(record.submission, { final_status: terminal.status, completion_path: new globalThis.URL(page.url()).pathname })
  const summary = page.locator('.kt-session-summary')
  await expect(summary).toBeVisible({ timeout: operationTimeoutMs })
  await expect(summary.locator('p strong')).toHaveText('已完成', { timeout: operationTimeoutMs })
  const fields = Object.fromEntries(await summary.locator('dl > div').evaluateAll(nodes => nodes.map(node =>
    [node.querySelector('dt')?.textContent?.trim(), node.querySelector('dd')?.textContent?.trim()])))
  record.submission.rendered_summary = { ...validateCompletionFields(fields), fields, text: await summary.innerText() }
}

async function runAnswerFlow(prepared, output, { capacityVisual = false } = {}) {
  const { record, account, page } = prepared
  const [first, second] = account.questions
  const initial = await inputAndSave(prepared, first)
  let amended = await inputAndSave(prepared, first, true)
  if (capacityVisual) {
    await capacityAnswerChecks(prepared, first, initial.value, output)
    amended = await inputAndSave(prepared, first, true)
  }
  await navigate(prepared, second.assignment_id)
  await inputAndSave(prepared, second)
  if (!record.blank_confidence_observations.length) for (const question of account.questions.slice(2)) {
    await navigate(prepared, question.assignment_id)
    if (await blankConfidence(prepared, question.assignment_id)) break
  }
  assert(record.blank_confidence_observations.length > 0, 'No actual optional confidence control was observed')
  await navigate(prepared, first.assignment_id)
  const current = await readServer(prepared, `/api/v1/student/sessions/${account.session_id}/questions/${first.assignment_id}/`)
  assert.deepEqual(current.draft_answer, amended.value)
  await verifyInput(page, amended.value)
  record.returned_answer_verified = true
  await page.waitForTimeout(11000)
  await screenshot(prepared, output, 'returned-answer')
  await submit(prepared)
  if (capacityVisual) {
    await page.reload({ waitUntil: 'domcontentloaded', timeout: operationTimeoutMs })
    await expect(page.locator('.kt-session-summary p strong')).toHaveText('已完成', { timeout: operationTimeoutMs })
    record.submission.completed_reload_verified = true
  }
  await screenshot(prepared, output, 'completed')
  await prepared.evidence.settle()
  const tools = record.requests.filter(row => /\/capture-v2\/attempts\/\d+\/tools\/$/.test(row.path))
  record.no_tools = { request_count: tools.length, projected_endpoints: record.runtime_projections.filter(row => row.research_tools_url).length }
  assert(record.runtime_projections.length > 0 && !record.no_tools.request_count && !record.no_tools.projected_endpoints, 'No-tools fixture exposed or requested a tools endpoint')
  assert(record.answer_mutations.every(row => row.inline_confidence?.probability == null), 'A confidence value was sent despite leaving inputs blank')
  assert.equal(record.page_errors.length, 0, 'Browser page errors were observed')
  Object.assign(record, capacityVisual ? classifyCapacityConsoleErrors(record.console, page.url(), record.offline_recovery, record.login, record)
    : classifyConsoleErrors(record.console, page.url()))
  assert.equal(record.unexpected_console_errors.length, 0, 'Unexpected browser console errors were observed')
  record.status = 'completed'
}

export async function runTwoPageWitness(values, { capacityVisual = false, runRetest = null } = {}) {
  if (values['run-manifest']) {
    const run = runRetest || (await import('./student-capacity-retest-browser.mjs')).runRetestBrowser
    return run(values)
  }
  assert(values.fixture && values.output, '--fixture and --output are required')
  const bytes = readFileSync(resolve(values.fixture))
  const fixture = JSON.parse(bytes.toString('utf8').replace(/^\uFEFF/, ''))
  const accounts = validateTwoPageFixture(fixture, values['base-url'], values['account-ids']?.split(','))
  const output = resolve(values.output)
  mkdirSync(dirname(output), { recursive: true })
  mkdirSync(output)
  const records = accounts.map((account, index) => ({ account_id: account.account_id, session_id: account.session_id,
    viewport: index ? { width: 390, height: 844 } : { width: 1440, height: 900 }, status: 'not_executed',
    console: [], page_errors: [], requests: [], runtime_projections: [], answer_mutations: [], save_samples: [],
    blank_confidence_observations: [], navigation: [], screenshots: [] }))
  const report = { run_id: fixture.run_id, fixture_sha256: sha(bytes), target_origin: new globalThis.URL(values['base-url']).origin,
    capacity_visual_mode: capacityVisual,
    started_at: new Date().toISOString(), node: process.version, reused_witnesses: ['student-concurrency-witness.mjs',
      'prepared-startup-witness.mjs', 'school-navigation-probe.mjs'], records, resources: [await resourceSample('before_launch')] }
  const save = () => writeFileSync(resolve(output, 'two-page-results.json'), JSON.stringify({ ...report, summary: twoPageSummary(records) }, null, 2))
  let visual, mobileContext, browserSession
  const prepared = []
  const fail = async (current, error) => {
    current.record.status = 'failed'
    current.record.error = { name: error.name, message: error.message, stack: error.stack }
    await current.page.screenshot({ path: resolve(output, `page-${current.index + 1}-failed.png`), fullPage: true }).catch(() => undefined)
    save()
  }
  try {
    visual = await createVisualProbeContext(values['base-url'])
    report.chromium = visual.context.browser().version()
    browserSession = await visual.context.browser().newBrowserCDPSession()
    mobileContext = await visual.context.browser().newContext({ baseURL: values['base-url'], viewport: { width: 390, height: 844 } })
    for (let index = 0; index < 2; index++) {
      const context = index ? mobileContext : visual.context
      const page = index ? await context.newPage() : context.pages()[0] || await context.newPage()
      await page.setViewportSize(records[index].viewport)
      page.setDefaultTimeout(operationTimeoutMs)
      const current = { context, page, account: accounts[index], record: records[index], mobile: index === 1, index,
        row: { task_id: accounts[index].taskId, attempt_id: `${fixture.run_id}:two-page:${accounts[index].account_id}`, cache_state: 'cold' },
        operationTimeoutMs }
      current.evidence = installEvidence(page, current.record)
      prepared.push(current)
    }
    assert(prepared[0].context !== prepared[1].context && prepared[0].page !== prepared[1].page)
    assert.equal(visual.context.browser().contexts().length, 2, 'Exactly two browser contexts required')
    assert.equal(prepared.reduce((sum, current) => sum + current.context.pages().length, 0), 2, 'Exactly two pages required')
    report.resources.push(await resourceSample('two_blank_pages', browserSession))
    save()
    for (const current of prepared) {
      try {
        current.record.status = 'running'
        await prepareLogin(current.page, current.account)
        current.record.login = await releaseLogin(current.page, current.account)
        const consent = current.page.getByRole('dialog', { name: '研究参与说明与知情同意' })
        if (await consent.isVisible()) {
          await consent.getByRole('checkbox').check()
          await consent.getByRole('button', { name: '同意并继续', exact: true }).click()
          await expect(consent).toHaveCount(0)
        }
        current.start = await startFixtureTask(current.page, current.account, () => operable(current.page))
        assert.equal(current.start.entryAction, 'start', 'A fresh UI account must start through the task list')
        current.record.startup = await startupWitness(current, { run_id: fixture.run_id, scenario_id: 'two-page-ui', input_settings_hash: sha(bytes) }, Date.now(), false)
        assert.equal(current.record.startup.status, 'completed', 'First question did not become operable')
        await expect(current.page).toHaveURL(url => url.pathname === questionPath(current.account, current.account.questions[0].assignment_id))
        const layout = await captureFirstReadyLayout(current.page, output, current.index, current.record.viewport)
        current.record.screenshots.push(layout)
        assert(layout.passed, 'Initial viewport has overflow or unexpected dimensions')
      } catch (error) { await fail(current, error) }
      save()
    }
    report.resources.push(await resourceSample('two_started_pages', browserSession))
    if (prepared[0].record.status === 'running') {
      try {
        const layouts = await captureVisualLayouts(prepared[0].page, output, factor => visual.setZoom(prepared[0].page, factor), { onlyZoom: true })
        prepared[0].record.browser_zoom_200 = layouts
        assert(layouts.passed, 'Actual browser200% zoom or its reset was not verified')
        await visual.setZoom(prepared[0].page, 2)
        try {
          await expect.poll(() => prepared[0].page.evaluate(() => globalThis.devicePixelRatio)).toBe(2)
          const filename = 'probe-1-active-zoom200-viewport.png'
          await captureBrowserViewport(prepared[0].page, resolve(output, filename))
          const geometry = await prepared[0].page.evaluate(() => ({ inner_width: globalThis.innerWidth,
            inner_height: globalThis.innerHeight, device_pixel_ratio: globalThis.devicePixelRatio }))
          layouts.viewport_screenshot = { filename, ...geometry, full_page: false }
        } finally {
          await visual.setZoom(prepared[0].page, 1)
          await expect.poll(() => prepared[0].page.evaluate(() => globalThis.devicePixelRatio)).toBe(1)
        }
      } catch (error) { await fail(prepared[0], error) }
    }
    for (const current of prepared.filter(row => row.record.status === 'running')) {
      try { await runAnswerFlow(current, output, { capacityVisual }) } catch (error) { await fail(current, error) }
      finally {
        await current.evidence.settle()
        report.resources.push(await resourceSample(`page_${current.index + 1}_flow_end`, browserSession))
        save()
      }
    }
  } catch (error) {
    for (const record of records) if (record.status !== 'completed' && record.status !== 'failed') {
      record.status = 'failed'; record.error = { name: error.name, message: error.message, stack: error.stack }
    }
  } finally {
    report.finished_at = new Date().toISOString()
    for (const record of records) {
      Object.assign(record, capacityVisual ? classifyCapacityConsoleErrors(record.console, values['base-url'], record.offline_recovery, record.login, record)
        : classifyConsoleErrors(record.console, values['base-url']))
      record.request_counts = Object.fromEntries([...new Set(record.requests.map(row => `${row.source} ${row.method} ${row.path}`))]
        .map(key => [key, record.requests.filter(row => `${row.source} ${row.method} ${row.path}` === key).length]))
    }
    save()
    await mobileContext?.close().catch(() => undefined)
    await visual?.close().catch(() => undefined)
  }
  globalThis.console.log(JSON.stringify({ artifact: resolve(output, 'two-page-results.json'), ...twoPageSummary(records) }, null, 2))
  if (!twoPageSummary(records).passed) process.exitCode = 1
  return { ...report, summary: twoPageSummary(records) }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const { values } = parseArgs({ options: { fixture: { type: 'string' }, output: { type: 'string' },
    'base-url': { type: 'string', default: 'http://127.0.0.1:18082' }, 'account-ids': { type: 'string' },
    'run-manifest': { type: 'string' }, plan: { type: 'string' }, 'ready-file': { type: 'string' },
    'release-file': { type: 'string' }, 'pressure-finished-file': { type: 'string' } } })
  await runTwoPageWitness(values)
}
