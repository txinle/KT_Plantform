import assert from 'node:assert/strict'
import test from 'node:test'
import { existsSync } from 'node:fs'
import { expect as playwrightExpect } from '@playwright/test'

async function witness() {
  assert.ok(existsSync(new globalThis.URL('./prepared-startup-witness.mjs', import.meta.url)), 'Prepared browser witness runner is missing')
  return import('./prepared-startup-witness.mjs')
}

test('transaction admission C6 requires a verified complete twenty-worker allocation', async () => {
  const { startupExecutionPolicy } = await witness()
  const resource_budget = { verified_against_runtime: true, startup_http_workers: 6, assessment_http_workers: 4,
    startup_db_connections: 6, assessment_db_connections: 4, auth_http_workers: 4, capture_http_workers: 3,
    student_read_http_workers: 1, teacher_read_http_workers: 1, core_http_workers: 1,
    total_http_workers: 20, total_ordinary_db_connections: 23, total_reserve_db_connections: 3 }
  const settings = { C: 6, R: 64, admission_mode: 'postgres_transaction', resource_budget }
  assert.deepEqual(startupExecutionPolicy(settings), { executable: true, reason: null })
  assert.equal(startupExecutionPolicy({ ...settings, resource_budget: { ...resource_budget, total_http_workers: 22 } }).executable, false)
  assert.equal(startupExecutionPolicy({ ...settings, resource_budget: { ...resource_budget, total_ordinary_db_connections: 25 } }).executable, false)
  assert.equal(startupExecutionPolicy({ ...settings, resource_budget: { ...resource_budget, auth_http_workers: 6 } }).executable, false)
})

function delayedPage(readyAfterMs = 8000, samples = []) {
  const listeners = new Map()
  const locator = { _apiName: 'Locator', first() { return this }, toString: () => 'first answer',
    _expect: async (_expression, options) => ({ matches: options.timeout >= readyAfterMs,
      timedOut: options.timeout < readyAfterMs, received: { value: 'hidden' }, log: [] }) }
  return { locator: () => locator, on: (event, listener) => listeners.set(event, listener),
    emit: (event, value) => listeners.get(event)?.(value), waitForTimeout: async () => {},
    evaluate: async fn => fn.toString().includes('Object.freeze([])') ? undefined : { samples, resources: [] } }
}

test('startup evidence exports existing assessment stage measurements', async () => {
  const { startupWitness } = await witness()
  globalThis.performance.clearMeasures()
  globalThis.performance.measure('kt-assessment:process_runtime', { start: 0, duration: 12.5,
    detail: { operation_id: 'a'.repeat(32), stage: 'process_runtime', outcome: 'success' } })
  globalThis.performance.measure('unrelated-private-measure', { start: 0, duration: 99 })
  const page = delayedPage(0)
  page.evaluate = async fn => fn()
  const record = await startupWitness({ page, context: { close: async () => {} }, row: { task_id: 1 },
    start: async () => {} }, {}, 0)
  assert.deepEqual(record.assessment_stage_timings, [{ name: 'kt-assessment:process_runtime',
    start_ms: 0, duration_ms: 12.5, operation_id: 'a'.repeat(32), stage: 'process_runtime', outcome: 'success' }])
  globalThis.performance.clearMeasures()
})

test('preparation failures retain their actual phase and safe error class without provider text', async () => {
  const { prepareBrowser, preparationFailureEvidence } = await witness()
  const failure = Object.assign(new Error('private token and credentials'), { name: 'TimeoutError' })
  let closed = false
  const context = { newPage: async () => ({ setDefaultTimeout() {}, goto: async () => { throw failure } }),
    close: async () => { closed = true } }
  await assert.rejects(prepareBrowser({ newContext: async () => context }, 'http://127.0.0.1:18081', {}, {}, 60000), error => {
    const evidence = preparationFailureEvidence(error)
    assert.deepEqual(evidence, { error_code: 'browser_preparation_failed', preparation_stage: 'login', error_kind: 'TimeoutError' })
    assert.ok(!JSON.stringify(evidence).includes('private'))
    return true
  })
  assert.equal(closed, true)
  assert.deepEqual(preparationFailureEvidence({ name: 'unsafe-secret-class', witness_preparation_stage: 'secret' }), {
    error_code: 'browser_preparation_failed', preparation_stage: 'unknown', error_kind: 'Error' })
})

test('startup readiness honors the frozen operation budget rather than the implicit 5 second expect default', async () => {
  const { operable, operationTimeoutMs } = await witness()
  const page = delayedPage()
  await assert.rejects(playwrightExpect(page.locator()).toBeVisible(), error => error.matcherResult.timeout === 5000)
  await operable(page)
  assert.equal(operationTimeoutMs(), 60000)
  assert.equal(operationTimeoutMs({ operation_timeout_ms: 12000 }), 12000)
  for (const value of [0, 60001, 1.5, '60000', null]) assert.throws(() => operationTimeoutMs({ operation_timeout_ms: value }))
  await operable(page, 12000)
  await assert.rejects(operable(page, 4000), error => error.matcherResult.timeout === 4000)
})

test('failed startup preserves an actual HTTP 503, partial phases and assertion timeout classification', async () => {
  const { startupWitness } = await witness()
  const samples = [{ phase: 'click', client_monotonic_ms: 10, client_time_origin: 1000 },
    { phase: 'request', client_monotonic_ms: 20, client_time_origin: 1000 }]
  const page = delayedPage(70000, samples)
  const request = { url: () => 'http://127.0.0.1/api/v1/student/tasks/1/start/', method: () => 'POST' }
  const response = { url: request.url, request: () => request, status: () => 503,
    json: async () => ({ error: { code: 'ADMISSION_UNAVAILABLE' } }) }
  const record = await startupWitness({ page, context: { close: async () => {} }, row: { task_id: 1 },
    start: async () => { page.emit('request', request); page.emit('response', response); await playwrightExpect(page.locator()).toBeVisible({ timeout: 60000 }) } }, {}, 0)
  assert.equal(record.error_code, 'browser_startup_http_5xx')
  assert.equal(record.error_kind, 'assert_timeout')
  assert.equal(record.assertion_timeout_ms, 60000)
  assert.equal(record.failure_stage, 'awaiting_admission_grant')
  assert.equal(record.timestamps_ms.admission_requested, 20)
  assert.equal(record.timestamps_ms.operable, undefined)
  assert.equal(record.activation_http_responses[0].status, 503)
  assert.equal(record.activation_http_responses[0].application_error_code, 'ADMISSION_UNAVAILABLE')
  assert.equal(record.activation_http_responses[0].body_read_state, 'completed')
})

test('a failed ready assertion drains an observed activation response before closing its context', async () => {
  const { startupWitness } = await witness()
  const page = delayedPage(70000, [{ phase: 'activation_start', client_monotonic_ms: 30, client_time_origin: 1000 }])
  const request = { url: () => 'http://127.0.0.1/api/v1/student/tasks/1/start/', method: () => 'POST' }
  let releaseBody, closed = false
  const recordPromise = startupWitness({ page, context: { close: async () => { closed = true } }, row: { task_id: 1 },
    start: async () => {
      page.emit('request', request)
      page.emit('response', { url: request.url, request: () => request, status: () => 201,
        json: () => new Promise(resolve => { releaseBody = resolve }) })
      await playwrightExpect(page.locator()).toBeVisible({ timeout: 60000 })
    } }, {}, 0)
  await new Promise(resolve => globalThis.setImmediate(resolve))
  assert.equal(closed, false)
  releaseBody({ data: { session_id: 7 } })
  const record = await recordPromise
  assert.equal(closed, true)
  assert.deepEqual(record.activation_receipt_ids, ['7'])
  assert.equal(record.activation_http_responses[0].body_read_state, 'completed')
  assert.equal(record.error_code, 'browser_startup_assert_timeout')
  assert.equal(record.failure_stage, 'awaiting_activation_response')
  assert.equal(record.timestamps_ms.activation_completed, undefined)
})

test('an activation still awaiting headers at the assertion boundary stays unobserved', async () => {
  const { startupWitness } = await witness()
  const page = delayedPage(70000, [{ phase: 'activation_start', client_monotonic_ms: 30, client_time_origin: 1000 }])
  const record = await startupWitness({ page, context: { close: async () => {} }, row: { task_id: 1 },
    start: async () => {
      page.emit('request', { url: () => 'http://127.0.0.1/api/v1/student/tasks/1/start/', method: () => 'POST' })
      await playwrightExpect(page.locator()).toBeVisible({ timeout: 60000 })
    } }, {}, 0)
  assert.deepEqual(record.activation_receipt_ids, [])
  assert.deepEqual(record.activation_http_responses, [])
  assert.equal(record.inflight_api_request_count_at_witness_end, 1)
  assert.equal(record.api_requests[0].transport_state, 'pending_response')
  assert.equal(record.timestamps_ms.activation_completed, undefined)
})

test('an observed response with an unfinished body cannot extend the frozen operation budget', async () => {
  const { startupWitness } = await witness()
  const page = delayedPage(70000, [{ phase: 'activation_start', client_monotonic_ms: 30, client_time_origin: 1000 }])
  const request = { url: () => 'http://127.0.0.1/api/v1/student/tasks/1/start/', method: () => 'POST' }
  const record = await startupWitness({ page, context: { close: async () => {} }, row: { task_id: 1 }, operationTimeoutMs: 1,
    start: async () => {
      page.emit('request', request)
      page.emit('response', { url: request.url, request: () => request, status: () => 201, json: () => new Promise(() => {}) })
      throw new Error('actual failure')
    } }, {}, 0)
  assert.equal(record.response_reads_settled, false)
  assert.equal(record.activation_http_responses[0].body_read_state, 'pending')
  assert.deepEqual(record.activation_receipt_ids, [])
})

test('browser normalization preserves all real phases on one monotonic origin', async () => {
  const { normalizeStartupPhases } = await witness()
  const names = ['click', 'request', 'grant', 'activation_start', 'activation_response',
    'first_content_start', 'first_content_end', 'runtime_init_end', 'operable']
  const result = normalizeStartupPhases(names.map((phase, index) => ({ phase, client_monotonic_ms: index * 10, client_time_origin: 1000 })))
  assert.equal(result.phase_origin_id, '1000')
  assert.equal(result.timestamps_ms.admission_granted, 20)
  assert.equal(result.timestamps_ms.activation_started, 30)
  assert.equal(result.timestamps_ms.operable, 80)
})

test('missing phases and mixed browser time origins are never fabricated', async () => {
  const { normalizeStartupPhases } = await witness()
  const result = normalizeStartupPhases([{ phase: 'click', client_monotonic_ms: 10, client_time_origin: 1000 },
    { phase: 'operable', client_monotonic_ms: 20, client_time_origin: 2000 }])
  assert.equal(result.phase_origin_id, null)
  assert.deepEqual(result.timestamps_ms, {})
  const missing = normalizeStartupPhases([{ phase: 'click', client_monotonic_ms: 10, client_time_origin: 1000 }])
  assert.equal(missing.timestamps_ms.click, 10)
  assert.equal(missing.timestamps_ms.activation_started, undefined)
})

test('witness target must be explicit isolated local fixture and every browser account bound', async () => {
  const { validateWitnessInputs } = await witness()
  const plan = { run_ids: ['r01'], scenarios: [{ scenario_id: 's1', planned_startups: [
    { attempt_id: 'a1', transport: 'browser', account_id: 'student1', task_id: 1, cache_state: 'cold' }], planned_saves: [] }] }
  const accounts = { isolated_demo: true, accounts: [{ account_id: 'student1', username: 'u1', password: 'unused', courseId: 2, taskId: 1 }] }
  assert.doesNotThrow(() => validateWitnessInputs(plan, accounts, 'http://127.0.0.1:18080', 'r01'))
  assert.throws(() => validateWitnessInputs(plan, accounts, 'https://production.example.com', 'r01'))
  assert.throws(() => validateWitnessInputs(plan, { ...accounts, isolated_demo: false }, 'http://127.0.0.1:18080', 'r01'))
  assert.throws(() => validateWitnessInputs({ ...plan, settings: { harness_source_sha256: {
    'client/e2e/prepared-startup-witness.mjs': '0'.repeat(64),
  } } }, accounts, 'http://127.0.0.1:18080', 'r01'), /runner source/)
})

test('runner validates the selected fresh repeat roster instead of reusing first-run accounts', async () => {
  const { validateWitnessInputs } = await witness()
  const plan = { run_ids: ['r01', 'r02'], scenarios: [{ scenario_id: 's1', planned_startups: [
    { attempt_id: 'a1', transport: 'browser', account_id: 'old', task_id: 1, cache_state: 'cold' }], planned_saves: [],
    run_bindings: { r01: { planned_startups: [], planned_saves: [] }, r02: {
      planned_startups: [{ attempt_id: 'a2', transport: 'browser', account_id: 'fresh', task_id: 2, cache_state: 'cold' }], planned_saves: [] } } }] }
  const accounts = { isolated_demo: true, accounts: [{ account_id: 'fresh', username: 'u2', password: 'unused', courseId: 2, taskId: 2 }] }
  assert.doesNotThrow(() => validateWitnessInputs(plan, accounts, 'http://127.0.0.1:18080', 'r02'))
})

test('a bounded browser pilot preserves the full unexecuted roster', async () => {
  const { partitionBrowserRoster } = await witness()
  assert.equal(typeof partitionBrowserRoster, 'function', 'Bounded browser roster support is missing')
  const rows = Array.from({ length: 180 }, (_, index) => ({ attempt_id: `a${index}`, transport: 'browser' }))
  const selected = partitionBrowserRoster(rows, 5)
  assert.equal(selected.executed.length, 5)
  assert.equal(selected.notExecuted.length, 175)
  assert.equal(selected.notExecuted[0].attempt_id, 'a5')
})

test('C4 is recorded as over the startup HTTP2 budget and cannot launch load', async () => {
  const { startupExecutionPolicy } = await witness()
  assert.equal(typeof startupExecutionPolicy, 'function', 'HTTP budget guard is missing')
  assert.deepEqual(startupExecutionPolicy({ C: 4, R: 2 }), { executable: false, reason: 'skipped_over_http_budget' })
  assert.deepEqual(startupExecutionPolicy({ C: 2, R: 64 }), { executable: true, reason: null })
  assert.throws(() => startupExecutionPolicy({ C: 2, R: 3 }))
})

test('C4 requires a frozen verified 4/4 allocation within the original eight worker and DB budgets', async () => {
  const { startupExecutionPolicy } = await witness()
  const resource_budget = { verified_against_runtime: true, startup_http_workers: 4, assessment_http_workers: 4,
    startup_db_connections: 4, assessment_db_connections: 4 }
  assert.deepEqual(startupExecutionPolicy({ C: 4, R: 64, resource_budget }), { executable: true, reason: null })
  for (const bad of [{ ...resource_budget, verified_against_runtime: false }, { ...resource_budget, assessment_http_workers: 6 },
    { ...resource_budget, startup_db_connections: 2 }, { ...resource_budget, startup_http_workers: 8 }]) {
    assert.equal(startupExecutionPolicy({ C: 4, R: 64, resource_budget: bad }).executable, false)
  }
})

test('warm transfer selects the actual post-content-start fetch and preserves cache byte counts', async () => {
  const { selectFirstContentTiming } = await witness()
  assert.equal(typeof selectFirstContentTiming, 'function', 'Current content fetch selection is missing')
  const url = 'http://127.0.0.1/assessment-content/abc.json'
  const resources = [{ name: url, startTime: 20, responseEnd: 40, transferSize: 1000, encodedBodySize: 700 },
    { name: url, startTime: 210, responseEnd: 220, transferSize: 0, encodedBodySize: 700, decodedBodySize: 700, deliveryType: 'cache' }]
  const selected = selectFirstContentTiming(resources, url, [{ phase: 'first_content_start', client_monotonic_ms: 200 }])
  assert.equal(selected.startTime, 210)
  assert.equal(selected.transferSize, 0)
  assert.equal(selected.encodedBodySize, 700)
  assert.equal(selectFirstContentTiming(resources.slice(0, 1), url, [{ phase: 'first_content_start', client_monotonic_ms: 200 }]), null)
})

test('Linux resource evidence counts only owned Chromium descendants and respects cgroup RAM', async () => {
  const { linuxResourceEvidence, resourceBudgetDecision } = await witness()
  assert.equal(typeof linuxResourceEvidence, 'function')
  const files = { '/proc/meminfo': 'MemTotal: 16000000 kB\nMemAvailable: 3000000 kB\n',
    '/sys/fs/cgroup/memory.max': '6442450944', '/sys/fs/cgroup/memory.current': '6000000000',
    '/proc/10/stat': '10 (chrome with spaces) S 1 0', '/proc/10/status': 'VmRSS: 100 kB\n',
    '/proc/11/stat': '11 (chrome) S 10 0', '/proc/11/status': 'VmRSS: 200 kB\n',
    '/proc/12/stat': '12 (other browser) S 1 0', '/proc/12/status': 'VmRSS: 900 kB\n' }
  const sample = linuxResourceEvidence(10, { readText: path => {
    if (!(path in files)) throw new Error('absent')
    return files[path]
  }, processIds: () => ['10', '11', '12'] })
  assert.equal(sample.available_physical_bytes, 3000000 * 1024)
  assert.equal(sample.cgroup_available_bytes, 442450944)
  assert.equal(sample.chrome_process_tree.process_count, 2)
  assert.equal(sample.chrome_process_tree.rss_sum_bytes, 300 * 1024)
  assert.equal(resourceBudgetDecision(sample, 2 * 1024 ** 3, 256 * 1024 ** 2).allowed, true)
  assert.equal(resourceBudgetDecision({ ...sample, cgroup_available_bytes: 200 * 1024 ** 2 }, 2 * 1024 ** 3, 256 * 1024 ** 2).reason, 'browser_cgroup_memory_budget')
  assert.equal(resourceBudgetDecision({ ...sample, available_physical_bytes: 1024 ** 3 }, 2 * 1024 ** 3, 256 * 1024 ** 2).reason, 'host_memory_budget')
})

test('action execution preserves failed and unexecuted frozen operation IDs', async () => {
  const { runWitnessActions } = await witness()
  assert.equal(typeof runWitnessActions, 'function')
  const actions = [{ operation_id: 'save1', kind: 'save' }, { operation_id: 'switch1', kind: 'switch' },
    { operation_id: 'save2', kind: 'save' }, { operation_id: 'submit1', kind: 'submit' }]
  const result = await runWitnessActions(actions, {
    save: async () => ({ status: 'completed', editing_experience_ms: 900, draft_http_ms: 80 }),
    switch: async () => { throw new Error('actual navigation failed') },
    submit: async () => assert.fail('Cannot submit after the navigation failed'),
  })
  assert.deepEqual(result.map(row => row.operation_id), actions.map(row => row.operation_id))
  assert.deepEqual(result.map(row => row.status), ['completed', 'failed', 'not_executed', 'not_executed'])
  assert.equal(result[0].draft_http_ms, 80)
  assert.equal(result[0].editing_experience_ms, 900)
})

test('question switching honors the frozen action budget for click, route and ready state', async () => {
  const { switchQuestionWitness } = await witness()
  assert.equal(typeof switchQuestionWitness, 'function')
  const page = delayedPage(8000)
  const baseLocator = page.locator
  const budgets = []
  page.locator = () => ({ ...baseLocator(), click: async options => {
    budgets.push(options.timeout)
    if (options.timeout < 8000) throw Object.assign(new Error('Delayed click'), { name: 'TimeoutError' })
  } })
  page.waitForURL = async (predicate, options) => {
    budgets.push(options.timeout)
    assert.ok(predicate(new globalThis.URL('http://127.0.0.1/student/sessions/1/questions/2/')))
    if (options.timeout < 8000) throw Object.assign(new Error('Delayed route'), { name: 'TimeoutError' })
  }
  page.url = () => 'http://127.0.0.1/student/sessions/1/questions/2/'
  const prepared = { page, account: { session_id: 1 }, operationTimeoutMs: 60000 }
  assert.equal((await switchQuestionWitness(prepared, 2)).assignment_id, 2)
  assert.ok(budgets.every(value => value > 5000 && value <= 60000))
  await assert.rejects(switchQuestionWitness({ ...prepared, operationTimeoutMs: 4000 }, 2), error => error.name === 'TimeoutError')
})
