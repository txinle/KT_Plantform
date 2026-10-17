import { createHash } from 'node:crypto'
import { appendFileSync, existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs'
import { spawnSync } from 'node:child_process'
import { freemem, totalmem, platform } from 'node:os'
import { dirname, resolve } from 'node:path'
import process from 'node:process'
import { pathToFileURL } from 'node:url'
import { parseArgs } from 'node:util'
import { chromium, expect } from '@playwright/test'
import { startFixtureTask, waitForStudentHomeCourse } from './school-navigation-probe.mjs'

const phaseNames = { click: 'click', request: 'admission_requested', grant: 'admission_granted',
  activation_start: 'activation_started', activation_response: 'activation_completed',
  runtime_init_end: 'runtime_initialized', operable: 'operable' }

export function partitionBrowserRoster(rows, maximum) {
  if (!Number.isSafeInteger(maximum) || maximum < 0) throw new Error('Browser context budget must be a nonnegative integer')
  return { executed: rows.slice(0, maximum), notExecuted: rows.slice(maximum) }
}

export function startupExecutionPolicy(settings) {
  if (![1, 2, 4, 6].includes(settings.C) || ![2, 4, 8, 16, 32, 64].includes(settings.R)) {
    throw new Error('Frozen settings require C1/2/4/6 and R2/4/8/16/32/64')
  }
  if (settings.C <= 2 && !settings.resource_budget) return { executable: true, reason: null }
  const budget = settings.resource_budget
  if (!budget) return { executable: false, reason: 'skipped_over_http_budget' }
  const fields = ['startup_http_workers', 'assessment_http_workers', 'startup_db_connections', 'assessment_db_connections']
  if (settings.admission_mode === 'postgres_transaction') {
    const workerFields = ['startup_http_workers', 'assessment_http_workers', 'auth_http_workers',
      'capture_http_workers', 'student_read_http_workers', 'teacher_read_http_workers', 'core_http_workers']
    if (budget.verified_against_runtime !== true || workerFields.some(name => !Number.isSafeInteger(budget[name]) || budget[name] < 1)
      || workerFields.reduce((sum, name) => sum + budget[name], 0) !== 20
      || budget.total_http_workers !== 20 || budget.total_ordinary_db_connections !== 23 || budget.total_reserve_db_connections !== 3
      || budget.startup_db_connections !== budget.startup_http_workers || budget.assessment_db_connections !== budget.assessment_http_workers) {
      return { executable: false, reason: 'skipped_unverified_startup_allocation' }
    }
    return settings.C === budget.startup_http_workers
      ? { executable: true, reason: null } : { executable: false, reason: 'skipped_over_http_budget' }
  }
  if (budget.verified_against_runtime !== true || fields.some(name => !Number.isSafeInteger(budget[name]) || budget[name] < 1)
    || budget.startup_http_workers + budget.assessment_http_workers !== 8
    || budget.startup_db_connections + budget.assessment_db_connections !== 8) {
    return { executable: false, reason: 'skipped_unverified_startup_allocation' }
  }
  if (settings.C > budget.startup_http_workers) return { executable: false, reason: 'skipped_over_http_budget' }
  if (settings.C > budget.startup_db_connections) return { executable: false, reason: 'skipped_over_db_budget' }
  return { executable: true, reason: null }
}

export function operationTimeoutMs(settings = {}) {
  const timeout = Object.hasOwn(settings, 'operation_timeout_ms') ? settings.operation_timeout_ms : 60000
  if (!Number.isSafeInteger(timeout) || timeout < 1 || timeout > 60000) throw new Error('Operation timeout must be an integer from 1 to 60000 ms')
  return timeout
}

export function linuxResourceEvidence(browserPid, { readText = path => readFileSync(path, 'utf8'),
  processIds = () => readdirSync('/proc') } = {}) {
  const result = { cgroup_available_bytes: null, chrome_process_tree: null }
  const optionalText = path => { try { return readText(path) } catch { return '' } }
  const meminfo = optionalText('/proc/meminfo')
  const available = /^MemAvailable:\s+(\d+)\s+kB/m.exec(meminfo)
  result.available_physical_bytes = available ? Number(available[1]) * 1024 : null
  result.host_memory_probe = available ? '/proc/meminfo:MemAvailable' : 'unavailable'
  let maximum = optionalText('/sys/fs/cgroup/memory.max').trim()
  let current = optionalText('/sys/fs/cgroup/memory.current').trim()
  result.cgroup_version = maximum ? 2 : null
  if (!maximum) {
    maximum = optionalText('/sys/fs/cgroup/memory/memory.limit_in_bytes').trim()
    current = optionalText('/sys/fs/cgroup/memory/memory.usage_in_bytes').trim()
    if (maximum) result.cgroup_version = 1
  }
  if (/^\d+$/.test(maximum) && /^\d+$/.test(current)) {
    result.cgroup_limit_bytes = Number(maximum)
    result.cgroup_current_bytes = Number(current)
    result.cgroup_available_bytes = Math.max(0, Number(maximum) - Number(current))
  }
  if (Number.isSafeInteger(browserPid) && browserPid > 0) {
    const rows = []
    for (const pid of processIds().filter(value => /^\d+$/.test(value))) {
      const stat = optionalText(`/proc/${pid}/stat`)
      const parent = stat.slice(stat.lastIndexOf(')') + 1).trim().split(/\s+/)[1]
      const rss = /^VmRSS:\s+(\d+)\s+kB/m.exec(optionalText(`/proc/${pid}/status`))
      if (parent && rss) rows.push({ pid: Number(pid), parent: Number(parent), rss: Number(rss[1]) * 1024 })
    }
    const owned = new Set([browserPid])
    let changed = true
    while (changed) {
      changed = false
      for (const row of rows) if (owned.has(row.parent) && !owned.has(row.pid)) { owned.add(row.pid); changed = true }
    }
    const children = rows.filter(row => owned.has(row.pid))
    result.chrome_process_tree = { root_pid: browserPid, process_count: children.length,
      rss_sum_bytes: children.reduce((sum, row) => sum + row.rss, 0), probe: '/proc/stat+status' }
  }
  return result
}

export function resourceBudgetDecision(sample, minimumHost, minimumCgroup) {
  if (!Number.isFinite(sample.available_physical_bytes)) return { allowed: false, reason: 'host_memory_probe_unavailable' }
  if (sample.available_physical_bytes < minimumHost) return { allowed: false, reason: 'host_memory_budget' }
  if (sample.cgroup_available_bytes !== null && sample.cgroup_available_bytes < minimumCgroup) return { allowed: false, reason: 'browser_cgroup_memory_budget' }
  return { allowed: true, reason: null }
}

function resourceSample(stage, browserPid = null) {
  const sample = { stage, sampled_at: new Date().toISOString(), total_physical_bytes: totalmem(),
    available_physical_bytes: freemem(), node_rss_bytes: process.memoryUsage().rss,
    cgroup_available_bytes: null, chrome_process_tree: null }
  if (platform() === 'linux') Object.assign(sample, linuxResourceEvidence(browserPid))
  if (Number.isSafeInteger(browserPid) && browserPid > 0 && platform() === 'win32') {
    const script = `$taskBrowserPid=${browserPid}; $taskRows=@(Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,WorkingSetSize); $taskKnown=[System.Collections.Generic.HashSet[int]]::new(); [void]$taskKnown.Add($taskBrowserPid); do { $taskAdded=$false; foreach($taskRow in $taskRows) { if($taskKnown.Contains([int]$taskRow.ParentProcessId) -and $taskKnown.Add([int]$taskRow.ProcessId)) { $taskAdded=$true } } } while($taskAdded); $taskOwned=@($taskRows | Where-Object { $taskKnown.Contains([int]$_.ProcessId) }); @{root_pid=$taskBrowserPid; process_count=$taskOwned.Count; rss_sum_bytes=[long](($taskOwned | Measure-Object WorkingSetSize -Sum).Sum)} | ConvertTo-Json -Compress`
    const probe = spawnSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', script], { encoding: 'utf8', windowsHide: true, timeout: 10000 })
    try { sample.chrome_process_tree = JSON.parse(probe.stdout) } catch { sample.chrome_rss_probe_error = 'process_tree_probe_unavailable' }
  }
  return sample
}

export async function runWitnessActions(actions, handlers) {
  const results = []
  let failed = false
  for (const action of actions) {
    const record = { operation_id: action.operation_id, kind: action.kind, status: 'not_executed' }
    if (failed) record.error_code = 'prior_action_failed'
    else {
      try {
        const handler = handlers[action.kind]
        if (!handler) throw new Error('Unsupported frozen action')
        Object.assign(record, await handler(action))
        if (record.status !== 'completed') failed = true
      } catch (error) {
        record.status = 'failed'
        record.error_code = `${action.kind}_${error.name === 'TimeoutError' ? 'timeout' : 'failed'}`
        failed = true
      }
    }
    results.push(record)
  }
  return results
}

export function normalizeStartupPhases(samples, firstContentTiming = null) {
  const origins = new Set(samples.map(row => row.client_time_origin))
  if (origins.size !== 1 || !Number.isFinite([...origins][0])) return { phase_origin_id: null, timestamps_ms: {} }
  const timestamps = {}
  for (const sample of samples) {
    const key = phaseNames[sample.phase]
    if (key && Number.isFinite(sample.client_monotonic_ms) && timestamps[key] === undefined) timestamps[key] = sample.client_monotonic_ms
  }
  // ResourceTiming measures transfer; content phase markers also include decryption.
  if (firstContentTiming && Number.isFinite(firstContentTiming.startTime) && Number.isFinite(firstContentTiming.responseEnd)) {
    timestamps.first_question_requested = firstContentTiming.startTime
    timestamps.first_question_received = firstContentTiming.responseEnd
  }
  return { phase_origin_id: String([...origins][0]), timestamps_ms: timestamps }
}

export function selectFirstContentTiming(resources, url, samples) {
  const start = samples.find(sample => sample.phase === 'first_content_start')?.client_monotonic_ms
  if (!Number.isFinite(start)) return null
  return resources.filter(entry => entry.name === url && entry.startTime >= start)
    .sort((left, right) => left.startTime - right.startTime)[0] || null
}

function runBinding(scenario, runId) {
  if (scenario.run_bindings && !scenario.run_bindings[runId]) throw new Error('Selected repeat roster binding is missing')
  return scenario.run_bindings ? scenario.run_bindings[runId] : scenario
}

export function validateWitnessInputs(plan, fixture, baseURL, runId, scenarioId = null) {
  operationTimeoutMs(plan.settings)
  const frozenSource = plan.settings?.harness_source_sha256?.['client/e2e/prepared-startup-witness.mjs']
  if (frozenSource && frozenSource !== createHash('sha256').update(readFileSync(new globalThis.URL(import.meta.url))).digest('hex')) {
    throw new Error('Frozen browser runner source differs')
  }
  if (fixture.input_settings_hash && fixture.input_settings_hash !== plan.input_settings_hash) throw new Error('Merged fixture plan binding differs')
  const origin = new globalThis.URL(baseURL)
  if (!['127.0.0.1', 'localhost', '[::1]'].includes(origin.hostname) || fixture.isolated_demo !== true) {
    throw new Error('Browser witnesses require an explicitly isolated local demo fixture')
  }
  if (!plan.run_ids.includes(runId)) throw new Error('Run ID is absent from frozen plan')
  if (scenarioId && !plan.scenarios.some(row => row.scenario_id === scenarioId)) throw new Error('Selected scenario is absent from frozen plan')
  const accounts = new Map(fixture.accounts.map(account => [account.account_id, account]))
  for (const scenario of plan.scenarios.filter(row => !scenarioId || row.scenario_id === scenarioId)) {
    const binding = runBinding(scenario, runId)
    for (const row of [...binding.planned_startups.filter(item => item.transport === 'browser'), ...binding.planned_saves]) {
      const account = accounts.get(row.account_id)
      if (!account || account.taskId !== row.task_id || !Number.isInteger(account.courseId)) throw new Error('Planned browser account/task binding differs')
      if (row.cache_state === 'warm' && !Array.isArray(row.warm_urls)) throw new Error('Warm witness requires explicit encrypted/static resource URLs')
      const actions = row.actions || row.post_start_actions || []
      if (new Set(actions.map(action => action.operation_id)).size !== actions.length) throw new Error('Duplicate frozen action ID')
      for (const action of actions) {
        if (!action.operation_id || !['save', 'switch', 'submit', 'reload'].includes(action.kind)) throw new Error('Unsupported frozen browser action')
        if (['save', 'switch', 'reload'].includes(action.kind) && (!Number.isInteger(action.assignment_id)
          || !account.questions?.some(question => question.assignment_id === action.assignment_id))) throw new Error('Action question differs from fixture')
        if (['save', 'reload'].includes(action.kind) && action.value === undefined) throw new Error('Action requires an explicit frozen value')
      }
    }
  }
}

async function login(page, account) {
  await page.goto('/login/')
  await page.getByLabel('账号或学号').fill(account.username)
  await page.getByLabel('密码').fill(account.password)
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL(/\/student\/$/)
  await waitForStudentHomeCourse(page, account.courseId)
  const consent = page.getByRole('dialog', { name: '研究参与说明与知情同意' })
  if (await consent.isVisible()) {
    await consent.getByRole('checkbox').check()
    await consent.getByRole('button', { name: '同意并继续', exact: true }).click()
    await expect(consent).toHaveCount(0)
  }
}

export async function operable(page, timeoutMs = 60000) {
  const began = Date.now()
  await expect(page.locator('[data-init-state="ready"]')).toBeVisible({ timeout: timeoutMs })
  await expect(page.locator('[name="answer"]').first()).toBeEnabled({ timeout: Math.max(1, timeoutMs - (Date.now() - began)) })
}

export async function switchQuestionWitness(prepared, assignmentId) {
  const { page, account } = prepared
  const began = Date.now()
  const remaining = () => Math.max(1, (prepared.operationTimeoutMs ?? 60000) - (Date.now() - began))
  const path = `/student/sessions/${account.session_id}/questions/${assignmentId}/`
  await page.locator(`.kt-assessment-nav [data-nav-assignment="${assignmentId}"][href]:not([aria-disabled="true"])`).click({ timeout: remaining() })
  await page.waitForURL(url => url.pathname === path, { timeout: remaining() })
  await operable(page, remaining())
  return { assignment_id: assignmentId, target_path: new globalThis.URL(page.url()).pathname }
}

export function preparationFailureEvidence(error) {
  return { error_code: 'browser_preparation_failed',
    preparation_stage: ['login', 'task_entry', 'active_question', 'warm_content'].includes(error?.witness_preparation_stage)
      ? error.witness_preparation_stage : 'unknown',
    error_kind: ['Error', 'TimeoutError', 'TypeError', 'AssertionError'].includes(error?.name) ? error.name : 'Error' }
}

export async function prepareBrowser(browser, baseURL, account, row, timeoutMs) {
  const context = await browser.newContext({ baseURL, viewport: row.viewport || { width: 1440, height: 900 } })
  const page = await context.newPage()
  page.setDefaultTimeout(timeoutMs)
  let stage = 'login'
  try {
  await login(page, account)
  const prepared = { context, page, row, account, operationTimeoutMs: timeoutMs }
  if (row.operation_id) {
    stage = 'active_question'
    if (!row.question_url || row.save_value === undefined) throw new Error('Mixed saving requires explicit existing question URL and frozen answer input')
    await page.goto(row.question_url)
    await operable(page, timeoutMs)
  } else {
    stage = 'task_entry'
    prepared.start = await startFixtureTask(page, account, () => operable(page, timeoutMs))
    if (prepared.start.entryAction !== 'start') throw new Error('Fresh startup cohort already has an active session')
    if (row.cache_state === 'warm') {
      stage = 'warm_content'
      prepared.warmResources = await page.evaluate(async urls => {
        const statuses = []
        for (const url of urls) {
          const parsed = new globalThis.URL(url, globalThis.location.href)
          if (parsed.origin !== globalThis.location.origin) throw new Error('Warm resource origin differs')
          const response = await globalThis.fetch(parsed.href, { credentials: 'same-origin', cache: 'force-cache' })
          await response.arrayBuffer()
          statuses.push({ path: parsed.pathname, status: response.status })
          if (!response.ok) throw new Error('Warm resource preload failed')
        }
        return statuses
      }, row.warm_urls)
    }
  }
  return prepared
  } catch (error) { await context.close(); throw Object.assign(error, { witness_preparation_stage: stage }) }
}

async function setAnswer(page, value) {
  const input = page.locator('[name="answer"]').first()
  const type = await input.getAttribute('type')
  if (type === 'radio' || type === 'checkbox') {
    const choices = Array.isArray(value) ? value.map(String) : [String(value)]
    if (type === 'checkbox') for (const option of await page.locator('[name="answer"]').all()) {
      await option.setChecked(choices.includes(await option.getAttribute('value')))
    }
    else await page.locator(`[name="answer"][value=${JSON.stringify(choices[0])}]`).check()
  } else { await input.fill(String(value)); await input.blur() }
}

async function assertAnswer(page, value) {
  const input = page.locator('[name="answer"]').first()
  const type = await input.getAttribute('type')
  if (type === 'radio' || type === 'checkbox') {
    const checked = await page.locator('[name="answer"]:checked').evaluateAll(nodes => nodes.map(node => node.value).sort())
    expect(checked).toEqual((Array.isArray(value) ? value.map(String) : [String(value)]).sort())
  } else await expect(input).toHaveValue(String(value))
}

async function browserActions(prepared, actions) {
  const { page, context, account } = prepared
  let lastSave = null
  const ready = () => operable(page, prepared.operationTimeoutMs)
  const questionPath = id => `/student/sessions/${account.session_id}/questions/${id}/`
  const timed = async operation => {
    const started = globalThis.performance.now()
    const result = await operation()
    return { status: 'completed', started_at_ms: started, completed_at_ms: globalThis.performance.now(),
      measurement_origin: `driver-monotonic:${globalThis.performance.timeOrigin}`, ...result }
  }
  return runWitnessActions(actions, {
    save: action => timed(async () => {
      await expect(page).toHaveURL(url => url.pathname === questionPath(action.assignment_id))
      const started = globalThis.performance.now()
      const draftResponse = page.waitForResponse(response => response.request().method() === 'POST'
        && /\/student\/attempts\/\d+\/draft\/$/.test(new globalThis.URL(response.url()).pathname)).catch(() => null)
      await setAnswer(page, action.value)
      const response = await draftResponse
      if (!response) throw Object.assign(new Error('Draft receipt timed out'), { name: 'TimeoutError' })
      await response.finished()
      const data = domainData(await response.json())
      if (!response.ok() || data.success !== true) throw new Error('Draft was rejected')
      const timing = response.request().timing()
      lastSave = { assignment_id: action.assignment_id, response_version: data.response_version,
        target_event_id: data.active_response_event_id || data.target_event_id }
      if (!lastSave.target_event_id) {
        // Draft receipt uses active_response_event_id in current API; retain a
        // missing head as failed evidence instead of inventing a binding.
        throw new Error('Draft receipt lacks saved response head')
      }
      return { editing_experience_ms: globalThis.performance.now() - started,
        draft_http_ms: timing.requestStart >= 0 && timing.responseEnd >= timing.requestStart ? timing.responseEnd - timing.requestStart : null,
        http_status: response.status(), response_version: lastSave.response_version,
        target_event_id: lastSave.target_event_id }
    }),
    switch: action => timed(() => switchQuestionWitness(prepared, action.assignment_id)),
    reload: action => timed(async () => {
      if (!lastSave || lastSave.assignment_id !== action.assignment_id) throw new Error('Recovery requires the preceding real saved head')
      await page.reload({ waitUntil: 'domcontentloaded' })
      await ready()
      await assertAnswer(page, action.value)
      const response = await context.request.get(`/api/v1/student/sessions/${account.session_id}/questions/${action.assignment_id}/`)
      const data = domainData(await response.json())
      if (!response.ok() || data.process?.active_response_event_id !== lastSave.target_event_id
        || data.process?.response_version !== lastSave.response_version || JSON.stringify(data.draft_answer) !== JSON.stringify(action.value)) throw new Error('Reload/server saved response differs')
      return { recovery_type: 'reload_after_saved_draft', ui_value_verified: true, server_pointer_verified: true,
        target_event_id: lastSave.target_event_id, response_version: lastSave.response_version }
    }),
    submit: () => timed(async () => {
      const finish = page.waitForResponse(response => response.request().method() === 'POST'
        && /\/sessions\/\d+\/finish\/$/.test(new globalThis.URL(response.url()).pathname)).catch(() => null)
      await page.locator('[data-finish-request]').first().click()
      const confirmation = page.locator('[data-paper-submit-confirm]')
      const first = await Promise.race([confirmation.waitFor({ state: 'visible' }).then(() => 'confirm'), finish.then(() => 'receipt')])
      if (first === 'confirm') await confirmation.click()
      const response = await finish
      if (!response) throw Object.assign(new Error('Whole submission receipt timed out'), { name: 'TimeoutError' })
      const receipt = domainData(await response.json())
      if (response.status() !== 202 || !receipt.submission_id || !receipt.status_url) throw new Error('Whole submission was not accepted')
      await page.waitForURL(url => url.pathname === `/student/sessions/${account.session_id}/complete/`, { timeout: 180000 })
      const statusResponse = await context.request.get(receipt.status_url)
      const committed = domainData(await statusResponse.json())
      if (!statusResponse.ok() || committed.status !== 'completed' || committed.submission_id !== receipt.submission_id) throw new Error('Whole submission completion was not verified')
      return { submission_id: receipt.submission_id, accepted_at: receipt.accepted_at,
        accepted_http_status: response.status(), final_status: committed.status,
        completed_page_path: new globalThis.URL(page.url()).pathname }
    }),
  })
}

function domainData(body) { return body?.data || body }

export async function startupWitness(prepared, binding, releaseAtMs, closeContext = true) {
  const { page, row, context } = prepared
  const receipts = new Set(), responseReads = [], requests = [], httpResponses = [], requestRecords = new Map()
  const timeoutMs = prepared.operationTimeoutMs ?? 60000
  let began = null, failure = null
  let firstContentURL = null
  let firstContentLength = null
  const record = { ...binding, attempt_id: row.attempt_id, task_id: row.task_id, transport: 'browser',
    cache_state: row.cache_state, status: 'failed', fresh_context: true,
    warm_resource_evidence: prepared.warmResources || [], activation_call_count: 0, activation_receipt_ids: [], operation_timeout_ms: timeoutMs }
  page.on('request', request => {
    const path = new globalThis.URL(request.url()).pathname
    if (request.method() === 'POST' && new RegExp(`/tasks/${row.task_id}/(?:start|resume)/$`).test(path)) record.activation_call_count++
    if (path.startsWith('/api/v1/')) {
      const entry = { path, method: request.method(), transport_state: 'pending_response', observed_at_epoch_ms: Date.now() }
      requests.push(entry)
      requestRecords.set(request, entry)
    }
  })
  page.on('requestfinished', request => { const entry = requestRecords.get(request); if (entry) entry.transport_state = 'finished' })
  page.on('requestfailed', request => { const entry = requestRecords.get(request); if (entry) entry.transport_state = 'failed' })
  page.on('response', response => {
    const path = new globalThis.URL(response.url()).pathname
    const entry = requestRecords.get(response.request())
    if (entry) { entry.transport_state = 'response_headers_received'; entry.http_status = response.status() }
    if (!new RegExp(`/tasks/${row.task_id}/(?:start|resume)/$`).test(path)) return
    const receiptRead = { path, status: response.status(), body_read_state: 'pending', headers_received_at_epoch_ms: Date.now() }
    httpResponses.push(receiptRead)
    responseReads.push((async () => {
      let body
      try { body = await response.json(); receiptRead.body_read_state = 'completed'; receiptRead.body_read_at_epoch_ms = Date.now() }
      catch (error) { receiptRead.body_read_state = 'failed'; receiptRead.body_read_error_name = error.name; return }
      const code = body?.error?.code
      if (typeof code === 'string' && /^[A-Za-z0-9_-]{1,80}$/.test(code)) receiptRead.application_error_code = code
      const data = body?.data || body
      if (data?.session_id) receipts.add(String(data.session_id))
      if (data?.runtime?.prepared_paper?.question?.url) {
        firstContentURL = new globalThis.URL(data.runtime.prepared_paper.question.url, response.url()).href
        firstContentLength = data.runtime.prepared_paper.question.byte_length
      }
    })())
  })
  const captureBrowserEvidence = async () => {
    const browserEvidence = await page.evaluate(() => {
      const samples = globalThis.__ktPreparedStartupWitness || []
      const resources = globalThis.performance.getEntriesByType('resource').map(entry => ({ name: entry.name,
        startTime: entry.startTime, responseEnd: entry.responseEnd, transferSize: entry.transferSize,
        encodedBodySize: entry.encodedBodySize, decodedBodySize: entry.decodedBodySize,
        deliveryType: typeof entry.deliveryType === 'string' ? entry.deliveryType : null }))
      const measures = globalThis.performance.getEntriesByType('measure')
        .filter(entry => entry.name.startsWith('kt-assessment:'))
        .map(entry => ({ name: entry.name, start_ms: entry.startTime, duration_ms: entry.duration,
          operation_id: entry.detail?.operation_id ?? null, stage: entry.detail?.stage ?? null,
          outcome: entry.detail?.outcome ?? null }))
      return { samples, resources, measures }
    })
    const firstContentTiming = selectFirstContentTiming(browserEvidence.resources, firstContentURL, browserEvidence.samples)
    record.raw_phase_samples = browserEvidence.samples
    record.assessment_stage_timings = browserEvidence.measures || []
    record.first_content_resource_timing = firstContentTiming
    record.first_content_bytes = firstContentTiming ? {
      transfer_size: firstContentTiming.transferSize, encoded_body_size: firstContentTiming.encodedBodySize,
      decoded_body_size: firstContentTiming.decodedBodySize, delivery_type: firstContentTiming.deliveryType,
      observed_zero_transfer: firstContentTiming.transferSize === 0, declared_descriptor_length: firstContentLength,
    } : { resource_timing_missing: true, declared_descriptor_length: firstContentLength }
    Object.assign(record, normalizeStartupPhases(browserEvidence.samples, firstContentTiming))
  }
  try {
    await page.waitForTimeout(Math.max(0, releaseAtMs + (row.arrival_offset_seconds || 0) * 1000 - Date.now()))
    await page.evaluate(() => { globalThis.__ktPreparedStartupWitness = Object.freeze([]) })
    began = Date.now()
    record.observer_start_epoch_ms = began
    await prepared.start()
    await Promise.all(responseReads)
    await captureBrowserEvidence()
    record.status = 'completed'
    record.physical_boundary = 'Visible ready state and enabled answer control; operable phase recorded by browser runtime.'
    if (row.post_start_actions?.length) record.action_results = await browserActions(prepared, row.post_start_actions)
  } catch (error) {
    failure = error
    record.observer_failure_epoch_ms = Date.now()
  } finally {
    let timer
    try {
      record.response_reads_settled = await Promise.race([Promise.all(responseReads).then(() => true),
        new Promise(resolveWait => { timer = globalThis.setTimeout(() => resolveWait(false), Math.max(0, timeoutMs - (Date.now() - (began ?? Date.now())))) })])
    } finally { globalThis.clearTimeout(timer) }
    if (failure) {
      try { await captureBrowserEvidence() } catch (error) { record.browser_evidence_error_name = error.name }
      const assertionTimeout = Number.isFinite(failure.matcherResult?.timeout)
      record.error_kind = assertionTimeout ? 'assert_timeout' : failure.name === 'TimeoutError' ? 'action_timeout' : 'error'
      record.error_name = failure.name
      if (assertionTimeout) record.assertion_timeout_ms = failure.matcherResult.timeout
      record.error_code = httpResponses.some(response => response.status >= 500) ? 'browser_startup_http_5xx'
        : assertionTimeout ? 'browser_startup_assert_timeout' : failure.name === 'TimeoutError' ? 'browser_startup_timeout' : 'browser_startup_failed'
      const lastPhase = record.raw_phase_samples?.at(-1)?.phase
      record.last_observed_phase = lastPhase || null
      record.failure_stage = ({ click: 'awaiting_admission_request', request: 'awaiting_admission_grant', grant: 'awaiting_activation_start',
        activation_start: 'awaiting_activation_response', activation_response: 'awaiting_first_content', first_content_start: 'awaiting_first_content_end',
        first_content_end: 'awaiting_runtime_init', runtime_init_start: 'awaiting_runtime_init_end', runtime_init_end: 'awaiting_operable' })[lastPhase] || 'before_click_or_after_operable'
    }
    record.activation_http_responses = httpResponses
    record.inflight_api_request_count_at_witness_end = requests.filter(request => !['finished', 'failed'].includes(request.transport_state)).length
    record.activation_receipt_ids = [...receipts]
    record.api_requests = requests
    record.observer_end_epoch_ms = Date.now()
    record.observer_clock = 'node_epoch_ms; separate from browser monotonic phase clock'
    if (closeContext) await context.close()
  }
  return record
}

async function savingWitness(prepared, binding, releaseAtMs, closeContext = true) {
  const { page, row, context } = prepared
  const record = { ...binding, operation_id: row.operation_id, task_id: row.task_id, status: 'failed' }
  const observedHttp = []
  const observeResponse = response => {
    const path = new globalThis.URL(response.url()).pathname
    if (response.request().method() === 'POST' && path.startsWith('/api/v1/student/')) {
      observedHttp.push({ path, http_status: response.status(), observed_epoch_ms: Date.now() })
    }
  }
  page.on('response', observeResponse)
  try {
    await page.waitForTimeout(Math.max(0, releaseAtMs + (row.arrival_offset_seconds || 0) * 1000 - Date.now()))
    if (row.actions?.length) {
      record.action_results = await browserActions(prepared, row.actions)
      record.status = record.action_results.every(action => action.status === 'completed') ? 'completed' : 'failed'
      record.started_at_ms = record.action_results[0]?.started_at_ms
      record.completed_at_ms = record.action_results.at(-1)?.completed_at_ms
      if (record.status !== 'completed') record.error_code = 'mixed_action_failed'
      return record
    }
    record.started_at_ms = await page.evaluate(() => globalThis.performance.now())
    const receiptPromise = page.waitForResponse(response => response.request().method() === 'POST'
      && /\/student\/attempts\/\d+\/draft\/$/.test(new globalThis.URL(response.url()).pathname)).catch(() => null)
    const input = page.locator('[name="answer"]').first()
    const type = await input.getAttribute('type')
    if (type === 'radio' || type === 'checkbox') await page.locator(`[name="answer"][value=${JSON.stringify(String(row.save_value))}]`).check()
    else { await input.fill(String(row.save_value)); await input.blur() }
    const response = await receiptPromise
    if (!response) throw Object.assign(new Error('Mixed save receipt timed out'), { name: 'TimeoutError' })
    record.http_status = response.status()
    const payload = await response.json()
    const data = payload.data || payload
    if (!response.ok() || data.success !== true) throw new Error('Mixed save did not commit')
    record.completed_at_ms = await page.evaluate(() => globalThis.performance.now())
    record.status = 'completed'
    record.response_version = data.response_version
  } catch (error) {
    record.error_code = error.name === 'TimeoutError' ? 'mixed_save_timeout' : 'mixed_save_failed'
  } finally {
    page.off('response', observeResponse)
    record.action_http_responses = observedHttp
    if (closeContext) await context.close()
  }
  return record
}

async function main() {
  const { values } = parseArgs({ options: { plan: { type: 'string' }, accounts: { type: 'string' },
    'base-url': { type: 'string' }, 'run-id': { type: 'string' }, scenario: { type: 'string' },
    'plan-sha256': { type: 'string' }, 'release-at-ms': { type: 'string' },
    'release-file': { type: 'string' }, 'max-browser-contexts': { type: 'string' },
    'min-free-mib': { type: 'string', default: platform() === 'linux' ? '2048' : '768' },
    'min-cgroup-free-mib': { type: 'string', default: '256' },
    'resource-report': { type: 'string' }, 'armed-file': { type: 'string' }, startups: { type: 'string' }, saves: { type: 'string' } } })
  for (const name of ['plan', 'accounts', 'base-url', 'run-id', 'plan-sha256', 'startups', 'saves', 'max-browser-contexts', 'resource-report']) {
    if (!values[name]) throw new Error(`Missing --${name}`)
  }
  const bytes = readFileSync(resolve(values.plan))
  if (createHash('sha256').update(bytes).digest('hex') !== values['plan-sha256']) throw new Error('Frozen plan file hash differs')
  const plan = JSON.parse(bytes), fixture = JSON.parse(readFileSync(resolve(values.accounts), 'utf8'))
  validateWitnessInputs(plan, fixture, values['base-url'], values['run-id'], values.scenario)
  const accounts = new Map(fixture.accounts.map(account => [account.account_id, account]))
  const policy = startupExecutionPolicy(plan.settings)
  const maximum = Number(values['max-browser-contexts']), minimumFree = Number(values['min-free-mib']) * 1024 * 1024
  const minimumCgroupFree = Number(values['min-cgroup-free-mib']) * 1024 * 1024
  if (!Number.isSafeInteger(maximum) || maximum < 1 || !Number.isFinite(minimumFree) || minimumFree < 0
    || !Number.isFinite(minimumCgroupFree) || minimumCgroupFree < 0) throw new Error('Invalid browser resource budget')
  const resources = { browser_context_limit: maximum, minimum_free_physical_bytes: minimumFree,
    minimum_free_cgroup_bytes: minimumCgroupFree,
    runtime: { node: process.version, platform: platform(), chromium: null }, resource_sample_interval_ms: 1000,
    startup_policy: { C: plan.settings.C, R: plan.settings.R, ...policy }, samples: [resourceSample('before_launch')],
    resource_scope: 'OS available physical RAM, Node RSS, and sum of owned Chromium process RSS; RSS includes shared pages.' }
  for (const path of [values.startups, values.saves]) { mkdirSync(dirname(resolve(path)), { recursive: true }); appendFileSync(path, '') }
  mkdirSync(dirname(resolve(values['resource-report'])), { recursive: true })
  let server = null, browser = null
  const initialBudget = resourceBudgetDecision(resources.samples[0], minimumFree, minimumCgroupFree)
  if (!initialBudget.allowed) resources.launch_error_code = initialBudget.reason
  if (policy.executable && initialBudget.allowed) {
    try {
      server = await chromium.launchServer({ headless: true })
      browser = await chromium.connect(server.wsEndpoint())
      resources.runtime.chromium = browser.version()
    } catch { resources.launch_error_code = 'browser_launch_failed' }
  }
  const browserPid = server?.process().pid || null
  try {
    for (const scenario of plan.scenarios.filter(row => !values.scenario || row.scenario_id === values.scenario)) {
      const binding = { run_id: values['run-id'], scenario_id: scenario.scenario_id, input_settings_hash: plan.input_settings_hash }
      const roster = runBinding(scenario, values['run-id'])
      const allRows = [...roster.planned_startups.filter(item => item.transport === 'browser'), ...roster.planned_saves]
      const selected = partitionBrowserRoster(allRows, browser?.isConnected() ? maximum : 0)
      const emitUnexecuted = row => appendFileSync(row.operation_id ? values.saves : values.startups, JSON.stringify({ ...binding,
        ...(row.operation_id ? { operation_id: row.operation_id } : { attempt_id: row.attempt_id, transport: 'browser', cache_state: row.cache_state }),
        task_id: row.task_id, status: 'not_executed', error_code: policy.reason || resources.launch_error_code || 'local_browser_resource_budget' }) + '\n')
      for (const row of selected.notExecuted) emitUnexecuted(row)
      const prepared = []
      for (const row of selected.executed) {
        const sample = resourceSample('before_context_preparation', browserPid)
        resources.samples.push(sample)
        const budget = resourceBudgetDecision(sample, minimumFree, minimumCgroupFree)
        if (!budget.allowed) { resources.launch_error_code = budget.reason; emitUnexecuted(row); continue }
        try { prepared.push(await prepareBrowser(browser, values['base-url'], accounts.get(row.account_id), row, operationTimeoutMs(plan.settings))) }
        catch (error) { appendFileSync(row.operation_id ? values.saves : values.startups, JSON.stringify({ ...binding,
          ...(row.operation_id ? { operation_id: row.operation_id } : { attempt_id: row.attempt_id, transport: 'browser' }),
          task_id: row.task_id, status: 'failed', ...preparationFailureEvidence(error) }) + '\n') }
      }
      resources.samples.push(resourceSample('all_contexts_prepared', browserPid))
      if (values['armed-file']) writeFileSync(values['armed-file'], JSON.stringify({ ...binding, prepared_count: prepared.length }))
      let releaseAt = values['release-at-ms'] ? Number(values['release-at-ms']) : Date.now() + 1000
      if (values['release-file']) {
        const expires = Date.now() + 180000
        while (!existsSync(values['release-file'])) {
          if (Date.now() >= expires) throw new Error('Shared browser release timed out')
          await new Promise(resolveWait => globalThis.setTimeout(resolveWait, 100))
        }
        const release = JSON.parse(readFileSync(values['release-file'], 'utf8'))
        if (release.input_settings_hash !== plan.input_settings_hash) throw new Error('Shared release plan binding differs')
        releaseAt = release.release_at_ms
      }
      if (!Number.isFinite(releaseAt)) throw new Error('Invalid shared release timestamp')
      let resourceAborted = false
      const monitor = globalThis.setInterval(() => {
        const sample = resourceSample('runtime_resource_check', browserPid)
        resources.samples.push(sample)
        if (!resourceBudgetDecision(sample, minimumFree, minimumCgroupFree).allowed && !resourceAborted) {
          resourceAborted = true
          resources.launch_error_code = 'browser_runtime_resource_abort'
          resources.samples.push({ ...sample, stage: 'runtime_resource_abort' })
          void browser?.close()
        }
      }, 1000)
      await Promise.all(prepared.map(async account => {
        const saving = Boolean(account.row.operation_id)
        const record = await (saving ? savingWitness : startupWitness)(account, binding, releaseAt, false)
        if (resourceAborted && record.status === 'failed') record.error_code = 'browser_runtime_resource_abort'
        appendFileSync(saving ? values.saves : values.startups, JSON.stringify(record) + '\n')
      }))
      globalThis.clearInterval(monitor)
      resources.samples.push(resourceSample('all_startups_settled_before_close', browserPid))
      for (const account of prepared) await account.context.close().catch(() => {})
    }
  } finally {
    await browser?.close().catch(() => {})
    await server?.close().catch(() => {})
    resources.samples.push(resourceSample('after_browser_close'))
    writeFileSync(values['resource-report'], JSON.stringify(resources, null, 2))
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  await main()
}
