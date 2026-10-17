/** Two functional student journeys plus teacher completeness visuals; never a capacity measurement. */
import assert from 'node:assert/strict'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { parseArgs } from 'node:util'
import process from 'node:process'
import { expect } from '@playwright/test'
import { classifyConsoleErrors, runTwoPageWitness, validateTwoPageFixture } from './student-concurrency-two-page.mjs'
import { prepareLogin, releaseLogin } from './student-concurrency-witness.mjs'
import { captureVisualLayouts, createVisualProbeContext } from './school-navigation-probe.mjs'

const domain = value => value?.data || value

export function capacityTeacherAccount(fixture, account) {
  assert(fixture.isolated_demo === true && /^[A-Za-z0-9_-]+$/.test(fixture.run_id), 'Owned isolated fixture required')
  const tasks = fixture.tasks?.filter(row => row.course_id === account.courseId && row.task_id === account.taskId)
  assert(tasks?.length === 1 && account.password, 'One owned course/task and synthetic password required')
  const task = tasks[0]
  for (const field of ['course_id', 'task_id', 'experiment_id', 'class_id']) assert(Number.isSafeInteger(task[field]) && task[field] > 0, 'Invalid teacher scope')
  const v3 = fixture.source_ownership?.command === 'prepare_student_capacity_baseline'
  if (v3) assert(fixture.baseline_snapshot_id && fixture.source_fixture_id && fixture.teacher_account?.username
    && fixture.teacher_account?.password && Number.isSafeInteger(fixture.teacher_account?.user_id), 'Actual owned baseline teacher required')
  return { username: v3 ? fixture.teacher_account.username : `pp-${fixture.run_id}-teacher`,
    password: v3 ? fixture.teacher_account.password : account.password,
    path: `/teacher/courses/${task.course_id}/data/advanced/?experiment_id=${task.experiment_id}&class_id=${task.class_id}`,
    status_path: `/api/v1/teacher/courses/${task.course_id}/data/advanced/status/?experiment_id=${task.experiment_id}&class_id=${task.class_id}` }
}

export function validatePreparationCounts(preparation) {
  const keys = ['total_count', 'ready_count', 'latest_complete_count', 'input_incomplete_count',
    'input_unverified_count', 'stale_count', 'pending_count', 'updating_count', 'failed_count']
  for (const key of keys) assert(Number.isSafeInteger(preparation?.[key]) && preparation[key] >= 0, `Invalid completeness count: ${key}`)
  for (const key of keys.slice(1)) assert(preparation[key] <= preparation.total_count, `Completeness count exceeds total: ${key}`)
  assert(preparation.latest_complete_count <= preparation.ready_count, 'Fresh complete count exceeds readable data')
  assert(preparation.input_unverified_count <= preparation.input_incomplete_count, 'Unverified is a subset of incomplete capture')
  return Object.fromEntries(keys.map(key => [key, preparation[key]]))
}

async function teacherVisuals(fixture, account, baseURL, output) {
  const teacher = capacityTeacherAccount(fixture, account)
  const report = { scope: 'teacher_completeness_presentation', capacity_evaluated: false,
    started_at: new Date().toISOString(), status: 'running', page_errors: [], console: [], screenshots: [] }
  let visual, page
  mkdirSync(output)
  try {
    visual = await createVisualProbeContext(baseURL)
    page = visual.context.pages()[0] || await visual.context.newPage()
    page.setDefaultTimeout(60000)
    page.on('pageerror', error => report.page_errors.push({ name: error.name, message: error.message }))
    page.on('console', message => report.console.push({ type: message.type(), text: message.text(), location: message.location() }))
    await prepareLogin(page, teacher)
    report.login = await releaseLogin(page, teacher, { waitReady: () => expect(page).toHaveURL(url => url.pathname.startsWith('/teacher/'), { timeout: 60000 }) })
    await page.goto(teacher.path, { waitUntil: 'domcontentloaded' })
    const counts = page.locator('[data-testid="preparation-counts"]')
    await expect(counts).toBeVisible({ timeout: 60000 })
    const responsePromise = page.waitForResponse(response => response.request().method() === 'GET'
      && new globalThis.URL(response.url()).pathname === new globalThis.URL(teacher.status_path, baseURL).pathname, { timeout: 60000 })
    void responsePromise.catch(() => undefined)
    await page.locator('[data-testid="refresh-research-data"]').click()
    const response = await responsePromise
    assert(response.ok(), 'Teacher completeness status failed')
    const preparation = domain(await response.json()).preparation
    report.counts = validatePreparationCounts(preparation)
    report.preparation = { state: preparation.state, projection_version: preparation.projection_version,
      result_version: preparation.result_version, updated_at: preparation.updated_at }
    for (const [label, key] of [['已结束会话', 'total_count'], ['最新且材料完整', 'latest_complete_count'],
      ['材料不完整或待核验', 'input_incomplete_count'], ['完整性未核验', 'input_unverified_count'],
      ['首次整理', 'pending_count'], ['更新中', 'updating_count'], ['整理失败', 'failed_count']]) {
      await expect(counts).toContainText(`${label} ${preparation[key]}`)
    }
    await expect(counts).toContainText('以上状态可能重叠')
    await expect(counts).toContainText('评分状态另行展示')
    report.counts_text = await counts.innerText()
    report.visual = await captureVisualLayouts(page, output, factor => visual.setZoom(page, factor))
    assert(report.visual.passed, 'Teacher desktop/mobile/browser zoom layout failed')
    report.screenshots = report.visual.layouts.map(row => row.filename).filter(Boolean)
    assert.equal(report.page_errors.length, 0, 'Teacher browser page errors')
    Object.assign(report, classifyConsoleErrors(report.console, baseURL))
    assert.equal(report.unexpected_console_errors.length, 0, 'Unexpected teacher console errors')
    report.status = 'completed'
  } catch (error) {
    report.status = 'failed'
    report.error = { name: error.name, message: error.message }
    await page?.screenshot({ path: resolve(output, 'teacher-failed.png'), fullPage: true }).catch(() => undefined)
  } finally {
    await visual?.close().catch(() => undefined)
    report.finished_at = new Date().toISOString()
    writeFileSync(resolve(output, 'teacher-visual.json'), JSON.stringify(report, null, 2))
  }
  return report
}

export async function captureRetestVisuals(values) {
  const read = path => JSON.parse(readFileSync(resolve(path), 'utf8').replace(/^\uFEFF/, ''))
  const fixture = read(values.fixture), manifest = read(values['run-manifest']), plan = read(values.plan)
  const { validateRetestInputs } = await import('./student-capacity-retest-browser.mjs')
  const selected = validateRetestInputs(manifest, fixture, plan, values['base-url'], values['account-ids']?.split(','))
  const output = resolve(values.output)
  const records = readFileSync(resolve(output, 'browser-records.jsonl'), 'utf8').trim().split('\n').map(line => JSON.parse(line))
  assert(records.length === 2 && records.every(row => row.plan_id === plan.plan_id && ['completed','failed'].includes(row.status)),
    'Pressure outcomes must be settled before post-pressure visuals')
  // The original two contexts collect student visuals after the pressure barrier.
  // Reopening an assessment here would create process evidence outside its witness.
  const students = records.map(record => ({ account_id: record.account_id,
    status: record.visual_phase === 'after_pressure' && record.visual?.passed ? 'completed' : 'failed', visual: record.visual }))
  const teacher = await teacherVisuals(fixture, selected[0].account, values['base-url'], resolve(output, 'post-pressure-teacher'))
  const report = { run_id: plan.run_id, plan_id: plan.plan_id, scope: 'post_pressure_students_and_actual_teacher',
    students, teacher, passed: students.every(row => row.status === 'completed') && teacher.status === 'completed' }
  writeFileSync(resolve(output, 'capacity-visual.json'), JSON.stringify(report, null, 2), { mode: 0o600 })
  if (!report.passed) process.exitCode = 1
  return report
}

export async function runCapacityVisual(values) {
  if (values['run-manifest']) {
    if (values['visuals-only']) return captureRetestVisuals(values)
    return runTwoPageWitness(values)
  }
  assert(values.fixture && values.output && values['account-ids'], '--fixture, --output and two explicit --account-ids required')
  const fixture = JSON.parse(readFileSync(resolve(values.fixture), 'utf8').replace(/^\uFEFF/, ''))
  const accounts = validateTwoPageFixture(fixture, values['base-url'], values['account-ids'].split(','))
  capacityTeacherAccount(fixture, accounts[0])
  const students = await runTwoPageWitness(values, { capacityVisual: true })
  const teacher = await teacherVisuals(fixture, accounts[0], values['base-url'], resolve(values.output, 'teacher'))
  const result = { scope: 'two_students_and_teacher_functional_visual_witness', capacity_evaluated: false,
    run_id: fixture.run_id, fixture_sha256: students.fixture_sha256,
    student_results: 'two-page-results.json', teacher_results: 'teacher/teacher-visual.json',
    started_at: students.started_at, finished_at: new Date().toISOString(),
    students: students.summary, teacher_completed: teacher.status === 'completed',
    passed: students.summary.passed && teacher.status === 'completed' }
  writeFileSync(resolve(values.output, 'capacity-visual.json'), JSON.stringify(result, null, 2))
  globalThis.console.log(JSON.stringify({ artifact: resolve(values.output, 'capacity-visual.json'), ...result }, null, 2))
  if (!result.passed) process.exitCode = 1
  return result
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const { values } = parseArgs({ options: { fixture: { type: 'string' }, output: { type: 'string' },
    'base-url': { type: 'string', default: 'http://127.0.0.1:18082' }, 'account-ids': { type: 'string' },
    'run-manifest': { type: 'string' }, plan: { type: 'string' }, 'ready-file': { type: 'string' },
    'release-file': { type: 'string' }, 'pressure-finished-file': { type: 'string' }, 'visuals-only': { type: 'boolean', default: false }, 'teacher-only': { type: 'boolean', default: false } } })
  await runCapacityVisual(values)
}
