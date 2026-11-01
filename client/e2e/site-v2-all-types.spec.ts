import { spawnSync } from 'node:child_process'
import { randomUUID } from 'node:crypto'
import { writeFileSync } from 'node:fs'
import { expect, test, type APIResponse, type Locator, type Page, type TestInfo } from '@playwright/test'

import { requireIsolatedPostgres } from '../scripts/e2e-database.mjs'
import { requireServerPython, serverDir } from '../scripts/e2e-runtime.mjs'
import { openLoginPage } from './login-navigation'
import { acceptResearchConsentIfRequired } from './research-consent'
import { submitWholePaper } from './whole-paper'

interface Credentials { username: string; password: string }
interface Item { assignment_id: number; question_id: number; question_type: string; educational_type: string; answer: string | string[]; confidence: number }
interface Unit { key: string; kind: string; title: string; items: Item[] }
interface Fixture { teacher: Credentials; student: Credentials; course_id: number; task_id: number; experiment_id: number; units: Unit[] }
interface Runtime {
  session_id: number; assignment: { assignment_id: number }; draft_answer: unknown
  presentation_units: unknown[]; presentation_progress: { total: number }
  question: Record<string, unknown>
  question_group?: { kind: string; members: Array<Record<string, unknown>> }
  group_responses?: Record<string, unknown>
  group_member_state?: Record<string, { inline_confidence?: { probability: number | null } }>
  inline_confidence?: { probability: number | null }
}
interface Summary {
  status: string; presentation_question_count: number; attempt_count: number; submission_count: number
  result: { feedback_phase: string; items?: Array<Record<string, unknown>>; groups?: unknown[];
    score_progress?: { graded_count: number; pending_count: number; item_count: number } }
}
interface Audit {
  session_id: number; submissions: number; group_events: number
  attempts: Array<{ assignment_id: number; answer: unknown; status: string; confidence: number | null;
    confidence_status: string; confidence_answer_event: string | null; final_response_event: string | null;
    event_attempt_id: number | null; attempt_id: number; submission_answer: unknown; final_snapshot_answer: unknown }>
}

const baseURL = process.env.KT_E2E_BASE_URL || 'http://127.0.0.1:8000'
test.use({ actionTimeout: 20_000 })

function django(args: string[], extraEnvironment: Record<string, string> = {}) {
  const result = spawnSync(requireServerPython(), ['manage.py', ...args], {
    cwd: serverDir, env: { ...process.env, ...requireIsolatedPostgres(process.env), ...extraEnvironment, PYTHONUTF8: '1' },
    encoding: 'utf8', timeout: 90_000,
  })
  if (result.error) throw result.error
  expect(result.status, `${args[0]}\n${result.stdout}\n${result.stderr}`).toBe(0)
  return result.stdout.trim().split('\n').at(-1)!
}

async function data<T>(response: APIResponse): Promise<T> {
  expect(response.status(), await response.text()).toBe(200)
  return (await response.json()).data as T
}

async function login(page: Page, credentials: Credentials, role: 'teacher' | 'student') {
  await openLoginPage(page, baseURL)
  await page.getByLabel('账号或学号').fill(credentials.username)
  await page.getByLabel('密码').fill(credentials.password)
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL(new RegExp(`/${role}/`))
  if (role === 'student') await acceptResearchConsentIfRequired(page)
}

async function ready(page: Page) {
  await expect(page.locator('.kt-assessment-focus')).toHaveAttribute('data-init-state', 'ready', { timeout: 30_000 })
}

async function captureViews(page: Page, info: TestInfo, prefix: string) {
  for (const [label, width, height] of [['desktop', 1440, 900], ['mobile', 390, 844], ['zoom-equivalent', 720, 450]] as const) {
    await page.setViewportSize({ width, height })
    await page.evaluate(() => scrollTo(0, 0))
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width + 1)
    await page.screenshot({ path: info.outputPath(`${prefix}-${label}.png`), fullPage: true })
  }
  await page.setViewportSize({ width: 1440, height: 900 })
}

async function runtime(page: Page) {
  return data<Runtime>(await page.request.get(`/api/v1${new URL(page.url()).pathname}`))
}

function expectHiddenAnswers(value: Record<string, unknown>) {
  for (const field of ['correct_answer', 'accepted_answers', 'is_correct', 'score', 'explanation']) expect(value).not.toHaveProperty(field)
}

async function expectSaved(page: Page, unit: Unit) {
  await expect.poll(async () => {
    const state = await runtime(page)
    return unit.items.map(item => state.question_group ? state.group_responses?.[String(item.assignment_id)] : state.draft_answer)
  }, { timeout: 30_000, intervals: [200, 500, 1000] }).toEqual(unit.items.map(item => item.answer))
  await expect(page.locator('[data-save-status]')).toContainText(/已保存|已恢复草稿/)
}

function groupInput(page: Page, unit: Unit, item: Item): Locator {
  const group = page.locator(`.kt-question-group--${unit.kind}`)
  return unit.kind === 'reading'
    ? group.locator(`[data-group-question="${item.assignment_id}"] input[value="${item.answer}"]`)
    : group.locator(`[data-group-item="${item.assignment_id}"]`)
}

async function writeGroupItem(page: Page, unit: Unit, item: Item) {
  const input = groupInput(page, unit, item)
  if (unit.kind === 'reading') await input.check()
  else if (unit.kind === 'words') await input.selectOption(String(item.answer))
  else await input.fill(String(item.answer))
}

async function answerUnit(page: Page, unit: Unit) {
  const state = await runtime(page)
  expectHiddenAnswers(state.question)
  for (const member of state.question_group?.members || []) expectHiddenAnswers(member)
  if (state.question_group) {
    expect(state.question_group.kind).toBe(unit.kind)
    // Deliberately do not wait for autosave between members: queued activation must preserve each input.
    for (const item of unit.items) await writeGroupItem(page, unit, item)
    await expectSaved(page, unit)
    const first = unit.items[0]!
    await page.locator(`[data-clear-group-item="${first.assignment_id}"]`).click()
    await expect.poll(async () => (await runtime(page)).group_responses?.[String(first.assignment_id)],
      { timeout: 30_000 }).toBe(unit.kind === 'reading' ? null : '')
    await page.reload()
    await ready(page)
    if (unit.kind === 'reading') await expect(groupInput(page, unit, first)).not.toBeChecked()
    else await expect(groupInput(page, unit, first)).toHaveValue('')
    for (const item of unit.items.slice(1)) {
      if (unit.kind === 'reading') await expect(groupInput(page, unit, item)).toBeChecked()
      else await expect(groupInput(page, unit, item)).toHaveValue(String(item.answer))
    }
    await writeGroupItem(page, unit, first)
  } else {
    const item = unit.items[0]!
    const article = page.locator('.kt-assessment-article')
    if (item.question_type === 'multiple_choice') {
      for (const key of item.answer as string[]) await article.locator(`input[value="${key}"]`).check()
    } else if (['single_choice', 'true_false'].includes(item.question_type)) {
      await article.locator(`input[value="${item.answer}"]`).check()
    } else {
      await article.locator('[name="answer"]').fill(String(item.answer))
      await article.locator('[name="answer"]').blur()
    }
    if (unit.kind === 'essay') {
      await expect(article.locator('textarea')).toHaveAttribute('rows', '12')
      await expect(page.locator('[data-word-count]')).toContainText(String(String(item.answer).split(/\s+/).length))
    }
    if (unit.kind === 'translation') await expect(article.getByText('你的译文', { exact: true })).toBeVisible()
  }
  await expectSaved(page, unit)
  for (const item of unit.items) {
    const confidence = page.locator(state.question_group ? `[data-group-confidence="${item.assignment_id}"]` : '[data-inline-confidence]')
    await expect(confidence).toBeEnabled()
    await confidence.fill(String(item.confidence))
    await confidence.blur()
    await expect.poll(async () => {
      const saved = await runtime(page)
      return saved.question_group ? saved.group_member_state?.[String(item.assignment_id)]?.inline_confidence?.probability : saved.inline_confidence?.probability
    }, { timeout: 30_000 }).toBe(item.confidence)
  }
}

function auditSession(sessionId: number): Audit {
  return JSON.parse(django(['shell', '-c', [
    'import json',
    'from apps.assessment.models import AssessmentEvent, Attempt, SubmissionEvent',
    `attempts = list(Attempt.objects.filter(session_id=${sessionId}).select_related('confidence_assignment', 'final_response_event', 'final_response_snapshot').order_by('assignment__assigned_sequence_index'))`,
    `submissions = {row.attempt_id: row for row in SubmissionEvent.objects.filter(attempt__session_id=${sessionId})}`,
    'rows = []',
    'for attempt in attempts:',
    ' confidence = attempt.confidence_assignment',
    " rows.append({'assignment_id': attempt.assignment_id, 'attempt_id': attempt.pk, 'answer': attempt.final_answer, 'status': attempt.status, 'confidence': confidence.probability, 'confidence_status': confidence.response_status, 'confidence_answer_event': str(confidence.draft_answer_event_id) if confidence.draft_answer_event_id else None, 'final_response_event': str(attempt.final_response_event_id) if attempt.final_response_event_id else None, 'event_attempt_id': attempt.final_response_event.attempt_id if attempt.final_response_event else None, 'submission_answer': submissions[attempt.pk].final_answer, 'final_snapshot_answer': attempt.final_response_snapshot.state_json.get('value') if attempt.final_response_snapshot else None})",
    `print(json.dumps({'session_id': ${sessionId}, 'submissions': len(submissions), 'group_events': AssessmentEvent.objects.filter(session_id=${sessionId}, event_type__in=['group_presented', 'group_left']).count(), 'attempts': rows}, ensure_ascii=False))`,
  ].join('\n')])) as Audit
}

test('Site v2 answers all eleven types as twelve whole questions and preserves twenty-two independent responses', async ({ browser, page }, info) => {
  test.setTimeout(600_000)
  const fixture = JSON.parse(django(['seed_e2e_site_v2_all_types'])) as Fixture
  expect(fixture.units).toHaveLength(12)
  expect(new Set(fixture.units.map(unit => unit.kind)).size).toBe(11)
  expect(fixture.units.flatMap(unit => unit.items)).toHaveLength(22)
  await login(page, fixture.student, 'student')
  await page.goto(`/student/courses/${fixture.course_id}/`)
  const task = page.locator('.kt-student-task-row').filter({ has: page.getByRole('heading', { name: 'Site v2 all question types', exact: true }) })
  await task.locator('[data-task-action="start"]').click()
  await expect(page).toHaveURL(/\/student\/sessions\/\d+\/questions\/\d+\/$/)
  await ready(page)
  const initial = await runtime(page)
  expect(initial.presentation_units).toHaveLength(12)
  expect(initial.presentation_progress.total).toBe(12)
  await expect(page.locator('[data-whole-question-tab]')).toHaveCount(12)
  const summaryURL = `/api/v1/student/sessions/${initial.session_id}/summary/`
  const before = await data<Summary>(await page.request.get(summaryURL))
  for (const field of ['items', 'groups', 'score', 'score_progress', 'correctness', 'comparison']) expect(before.result).not.toHaveProperty(field)
  for (const [index, unit] of fixture.units.entries()) {
    if (index) {
      await page.locator('[data-whole-question-tab]').nth(index).click()
      await expect.poll(async () => (await runtime(page)).assignment.assignment_id).toBe(unit.items[0]!.assignment_id)
    }
    await answerUnit(page, unit)
    await page.evaluate(() => scrollTo(0, 0))
    await page.screenshot({ path: info.outputPath(`student-${String(index + 1).padStart(2, '0')}-${unit.kind}.png`), fullPage: true })
  }
  // A final reload must preserve each member's answer and its distinct confidence value.
  await page.reload()
  await ready(page)
  for (const [index, unit] of fixture.units.entries()) {
    await page.locator('[data-whole-question-tab]').nth(index).click()
    await expect.poll(async () => (await runtime(page)).assignment.assignment_id).toBe(unit.items[0]!.assignment_id)
    await expectSaved(page, unit)
    for (const item of unit.items) await expect(page.locator(unit.items.length > 1
      ? `[data-group-confidence="${item.assignment_id}"]` : '[data-inline-confidence]')).toHaveValue(String(item.confidence))
  }
  await submitWholePaper(page)
  const completed = await data<Summary>(await page.request.get(summaryURL))
  expect(completed).toMatchObject({ status: 'completed', presentation_question_count: 12, attempt_count: 22, submission_count: 22 })
  expect(completed.result.items).toHaveLength(22)
  expect(completed.result.groups).toHaveLength(3)
  expect(completed.result.score_progress).toMatchObject({ item_count: 22, graded_count: 18, pending_count: 4 })
  expect(completed.result.items!.every(item => 'submitted_answer' in item)).toBe(true)
  await expect(page.locator('[name="answer"]:enabled')).toHaveCount(0)
  await expect(page.getByRole('heading', { name: '逐项作答与反馈', exact: true })).toBeVisible()
  await page.screenshot({ path: info.outputPath('student-all-types-results.png'), fullPage: true })
  const audit = auditSession(initial.session_id)
  expect(audit).toMatchObject({ session_id: initial.session_id, submissions: 22, group_events: 0 })
  expect(audit.attempts).toHaveLength(22)
  expect(new Set(audit.attempts.map(row => row.attempt_id)).size).toBe(22)
  for (const item of fixture.units.flatMap(unit => unit.items)) {
    const actual = audit.attempts.find(row => row.assignment_id === item.assignment_id)!
    expect(actual).toMatchObject({ answer: item.answer, submission_answer: item.answer, final_snapshot_answer: item.answer,
      status: 'submitted', confidence: item.confidence, confidence_status: 'committed' })
    expect(actual.final_response_event).toBeTruthy()
    expect(actual.confidence_answer_event).toBe(actual.final_response_event)
    expect(actual.event_attempt_id).toBe(actual.attempt_id)
  }
  const teacherContext = await browser.newContext({ baseURL })
  try {
    const teacher = await teacherContext.newPage()
    await login(teacher, fixture.teacher, 'teacher')
    expect((await teacher.request.get(summaryURL)).status()).toBe(403)
    await teacher.goto(`/teacher/courses/${fixture.course_id}/experiments/${fixture.experiment_id}/class-tasks/${fixture.task_id}/grading/`)
    const questions = teacher.locator('.kt-grading-question-list button')
    await expect(questions).toHaveCount(4)
    await expect(teacher.locator('.kt-grading-summary > div').filter({ hasText: '人工批改题目' }).locator('strong')).toHaveText('4')
    const comments = Array.from({ length: 4 }, (_, index) => `人工批改原文 ${index + 1}：表达完整，已核对。`)
    for (let index = 0; index < 4; index++) {
      await questions.nth(index).click()
      await teacher.getByLabel('批改备注（可选）').fill(comments[index]!)
      if (!index) {
        await captureViews(teacher, info, 'all-types-grading')
      }
      const graded = teacher.waitForResponse(response => /\/grading\/submissions\/\d+\/$/.test(new URL(response.url()).pathname) && response.request().method() === 'POST')
      await teacher.getByRole('button', { name: '判为正确', exact: true }).click()
      expect((await graded).status()).toBe(201)
      await expect(teacher.getByText('判定已保存。', { exact: true })).toBeVisible()
    }
    await questions.first().click()
    await teacher.getByRole('button', { name: '已批改', exact: true }).click()
    comments[0] = '复核后的有效原文：保留原始标点。'
    await teacher.getByLabel('修改原因').fill(comments[0])
    const revised = teacher.waitForResponse(response => /\/grading\/submissions\/\d+\/$/.test(new URL(response.url()).pathname) && response.request().method() === 'POST')
    await teacher.getByRole('button', { name: '判为错误', exact: true }).click()
    expect((await revised).status()).toBe(201)
    await expect(teacher.getByText(`当前备注：${comments[0]}`, { exact: true })).toBeVisible()
    expect((await data<Summary>(await page.request.get(summaryURL))).result.score_progress).toMatchObject({ graded_count: 22, pending_count: 0 })

    await teacher.goto(`/teacher/courses/${fixture.course_id}/experiments/${fixture.experiment_id}/`)
    await expect(teacher.getByRole('heading', { name: '实验内容', exact: true })).toBeVisible()
    await captureViews(teacher, info, 'all-types-overview')
    await teacher.getByRole('tab', { name: '班级与排期', exact: true }).click()
    await expect(teacher.locator('#experiment-panel-tasks tbody > tr')).toHaveCount(1)
    await captureViews(teacher, info, 'all-types-tasks')
    await teacher.getByRole('tab', { name: '学生作答', exact: true }).click()
    await expect(teacher.locator(`[data-collection-count="${fixture.task_id}:completed"]`)).toHaveText('1')
    await teacher.locator(`[data-collection-count="${fixture.task_id}:completed"]`).click()
    await expect(teacher.locator('#collection-filter-status')).toHaveValue('completed')
    await expect(teacher.locator('[data-collection-participants]')).toContainText('SITE-V2-TYPES-001')
    await captureViews(teacher, info, 'all-types-collection')
    await teacher.goto(`/teacher/courses/${fixture.course_id}/experiments/`)
    await expect(teacher.getByRole('region', { name: '实验列表', exact: true }).getByRole('link', { name: 'Site v2 all question types', exact: true })).toBeVisible()
    await captureViews(teacher, info, 'all-types-experiments')

    const workers = { ADVANCED_DATA_MATERIALIZED_ENABLED: '1', ADVANCED_DATA_MATERIALIZED_COURSE_IDS: String(fixture.course_id) }
    django(['reconcile_research_materializations', '--limit', '1'], workers)
    const catalog = `/api/v1/teacher/courses/${fixture.course_id}/data/exports/?scope_version=2&experiment_id=${fixture.experiment_id}&class_mode=all`
    await expect.poll(async () => {
      const result = await data<{ preparation: { pending_count: number } }>(await teacher.request.get(catalog))
      if (result.preparation.pending_count) django(['run_research_materializations', '--once'], workers)
      return result.preparation.pending_count
    }, { timeout: 90_000 }).toBe(0)
    const { csrf_token } = await data<{ csrf_token: string }>(await teacher.request.get('/api/v1/auth/csrf/'))
    const created = await teacher.request.post(`/api/v1/teacher/courses/${fixture.course_id}/data/export-jobs/`, {
      data: { package: 'experiment', confirm_identified: true, scope: { scope_version: 2, experiment_id: fixture.experiment_id, class_mode: 'all' } },
      headers: { 'X-CSRFToken': csrf_token, 'Idempotency-Key': randomUUID() },
    })
    expect(created.status(), await created.text()).toBe(201)
    let job = (await created.json()).data as { job_id: string; status: string; download_url: string | null }
    await expect.poll(async () => {
      if (job.status === 'queued') django(['run_export_jobs', '--once'], workers)
      job = await data<typeof job>(await teacher.request.get(`/api/v1/teacher/courses/${fixture.course_id}/data/export-jobs/${job.job_id}/`))
      expect(job.status).not.toBe('failed')
      return job.status
    }, { timeout: 90_000 }).toBe('succeeded')
    const download = await teacher.request.get(job.download_url!)
    expect(download.status()).toBe(200)
    const archive = info.outputPath('all-types-graded-standard-export.zip')
    writeFileSync(archive, await download.body())
    const result = spawnSync(requireServerPython(), ['-c', [
      'import csv,io,json,sys,zipfile',
      'with zipfile.ZipFile(sys.argv[1]) as z:',
      " rows=list(csv.DictReader(io.StringIO(z.read('05_单次实验_逐题作答表.csv').decode('utf-8-sig'))))",
      " print(json.dumps({'files':len(z.namelist()),'rows':len(rows),'sessions':len({r['sessionID'] for r in rows}),'comments':sorted(r['comment'] for r in rows if r['comment']),'empty_comments':sum(not r['comment'] for r in rows)},ensure_ascii=False))",
    ].join('\n'), archive], { encoding: 'utf8', env: { ...process.env, PYTHONUTF8: '1' } })
    expect(result.status, result.stderr).toBe(0)
    expect(JSON.parse(result.stdout)).toEqual({ files: 8, rows: 22, sessions: 1, comments: [...comments].sort(), empty_comments: 18 })
    await info.attach('all-types-export-comments', { body: result.stdout, contentType: 'application/json' })
  } finally { await teacherContext.close() }
  await info.attach('site-v2-all-types-independent-attempts', { body: JSON.stringify(audit, null, 2), contentType: 'application/json' })
})
