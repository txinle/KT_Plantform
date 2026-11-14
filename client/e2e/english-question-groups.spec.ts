import { downloadTemplateImages } from './question-template-images'
import { spawnSync } from 'node:child_process'
import { randomUUID } from 'node:crypto'
import { readFileSync, writeFileSync } from 'node:fs'
import { expect, test, type APIResponse, type Locator, type Page, type TestInfo } from '@playwright/test'

import { requireIsolatedPostgres } from '../scripts/e2e-database.mjs'
import { requireServerPython, serverDir } from '../scripts/e2e-runtime.mjs'
import { openLoginPage } from './login-navigation'
import { acceptResearchConsentIfRequired } from './research-consent'
import { submitWholePaper } from './whole-paper'

interface Credentials { username: string; password: string }
interface Fixture {
  teacher: Credentials; student: Credentials; course_id: number; class_id: number; student_id: number
  kc_code: string; private_kc_id: number; course_bank_id: number; standalone_bank_id: number
  course_experiment_id: number
}
interface Member {
  item_key: string; question_type: string; group_order: number; blank_id: string
  max_score?: string | number; response_value?: unknown; is_correct?: boolean
}
interface Group {
  id: number; revision: number; instructions: string
  group_id: string; kind: string; title: string; item_count: number; members: Member[]
  material_segments: Array<{ kind: string; item_key?: string }>
}
interface Preview {
  preview_id: string
  preview: {
    summary: { ready: number; draft: number; duplicate: number; invalid: number }
    rows: Array<{ row_id: string; group_id: string; max_score: string; classification: string }>
    groups: Array<{ group_id: string; source_group_code: string; kind: string; item_count: number; total_max_score: string; classification: string; member_row_ids: string[] }>
  }
}
interface Plan { id: number; plan_revision: number; can_preview: boolean; blocking_issues: unknown[] }
interface Runtime {
  session_id: number; question_group: Group; group_responses: Record<string, unknown>
  actions: { save_draft_url: string }
}
interface Result {
  feedback_phase: string; groups: Group[]; items: Member[]
  score_progress: { earned: string | number; paper_maximum: string | number; graded_maximum: string | number; item_count: number; pending_count: number; graded_count: number }
}
interface Summary { status: string; submission_count: number; attempt_count: number; result: Result }
interface ExportJob { job_id: string; status: string; download_url: string | null }

const baseURL = process.env.KT_E2E_BASE_URL || 'http://127.0.0.1:8000'
const clozeAnswers = ['Saturday', 'museum', 'bus', 'nine', 'pairs', 'maps', 'notes', 'lunch', 'tree', 'warm', 'water', 'guide', 'wrong', 'wrong', 'wrong']
test.use({ actionTimeout: 20_000 })

function django(args: string[], environment: Record<string, string> = {}) {
  const result = spawnSync(requireServerPython(), ['manage.py', ...args], {
    cwd: serverDir,
    env: { ...process.env, ...environment, ...requireIsolatedPostgres(process.env), PYTHONUTF8: '1' },
    encoding: 'utf8', timeout: 60_000,
  })
  if (result.error) throw result.error
  if (args[0]?.includes('materialization') || args[0] === 'run_export_jobs') {
    writeFileSync(test.info().outputPath('research-worker.log'), `${args.join(' ')}\n${result.stdout}\n${result.stderr}\n`, { flag: 'a' })
  }
  expect(result.status, `${args.join(' ')}\n${result.stdout}\n${result.stderr}`).toBe(0)
  return result.stdout
}

async function data<T>(response: APIResponse, statuses = [200]): Promise<T> {
  expect(statuses, await response.text()).toContain(response.status())
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

async function mutation<T>(page: Page, url: string, body: unknown = {}, method = 'POST', statuses = [200]) {
  const { csrf_token } = await data<{ csrf_token: string }>(await page.request.get('/api/v1/auth/csrf/'))
  return data<T>(await page.request.fetch(url, {
    method, data: body, headers: { 'X-CSRFToken': csrf_token, 'Idempotency-Key': randomUUID() },
  }), statuses)
}

async function screenshots(page: Page, info: TestInfo, prefix: string, content: Locator) {
  for (const size of [
    { label: 'desktop-1440x900', width: 1440, height: 900 },
    { label: 'mobile-390x844', width: 390, height: 844 },
    // Native 200% zoom on a 1440x900 display exposes a 720x450 CSS viewport.
    { label: 'zoom-200-percent-equivalent', width: 720, height: 450 },
  ]) {
    await page.setViewportSize({ width: size.width, height: size.height })
    await page.evaluate(() => window.scrollTo(0, 0))
    await expect(content).toBeVisible()
    const layout = await page.evaluate(() => {
      const workarea = document.querySelector('.kt-assessment-layout')?.getBoundingClientRect()
      const question = document.querySelector('.kt-assessment-question')?.getBoundingClientRect()
      const tabs = document.querySelector('.kt-group-tabs')?.getBoundingClientRect()
      const answerCard = document.querySelector('.kt-assessment-nav')?.getBoundingClientRect()
      const detail = document.querySelector<HTMLElement>('[data-question-detail]')
      const detailBox = detail?.getClientRects().length ? detail.getBoundingClientRect() : undefined
      const overflowingNavigation = Array.from(document.querySelectorAll<HTMLElement>('.kt-assessment-nav__item'))
        .filter(element => element.getClientRects().length && element.scrollWidth > element.clientWidth + 1)
        .map(element => ({ text: element.textContent, scrollWidth: element.scrollWidth, width: element.clientWidth }))
      return {
        scrollWidth: document.documentElement.scrollWidth, viewport: innerWidth,
        headerHeight: document.querySelector('.kt-app-header')?.getBoundingClientRect().height,
        mainWidth: document.querySelector('main')?.getBoundingClientRect().width,
        workareaWidth: workarea?.width, questionTop: question?.top,
        tabsBottom: tabs?.bottom, answerCardWidth: answerCard?.width, answerCardTop: answerCard?.top, overflowingNavigation,
        detailBox: detailBox && { left: detailBox.left, right: detailBox.right, top: detailBox.top, bottom: detailBox.bottom, width: detailBox.width },
        bodyFont: getComputedStyle(document.body).fontFamily,
      }
    })
    const fonts = await content.locator('p, legend, label, button, small, input, textarea, select, th, td').evaluateAll(elements => elements
      .filter(element => element.getClientRects().length && getComputedStyle(element).visibility !== 'hidden')
      .filter(element => !(element instanceof HTMLInputElement) || !['radio', 'checkbox', 'hidden', 'range', 'color', 'file', 'image'].includes(element.type))
      .map(element => ({ tag: element.tagName.toLowerCase(), type: element instanceof HTMLInputElement ? element.type : undefined,
        class: element.getAttribute('class') || '', text: (element.textContent || (element instanceof HTMLInputElement ? element.value : '')).trim().slice(0, 120),
        fontSize: Number.parseFloat(getComputedStyle(element).fontSize) })))
    const minimumFont = Math.min(...fonts.map(element => element.fontSize))
    const lowFontNodes = fonts.filter(element => element.fontSize < 12)
    const metrics = info.outputPath(`${prefix}-${size.label}-layout.json`)
    writeFileSync(metrics, JSON.stringify({ ...layout, minimumFont, lowFontNodes }))
    await info.attach(`${prefix}-${size.label}-layout`, { path: metrics, contentType: 'application/json' })
    await page.screenshot({ path: info.outputPath(`${prefix}-${size.label}.png`), fullPage: true })
    expect(layout.scrollWidth, `${prefix} ${size.label}: horizontal page overflow`).toBeLessThanOrEqual(layout.viewport + 1)
    // Site v2 uses 12px status labels and 13px import filters; body/table text stays larger.
    expect(minimumFont, `${prefix} ${size.label}: text smaller than the Site v2 minimum`).toBeGreaterThanOrEqual(12)
    expect(layout.bodyFont).toMatch(/Segoe UI.*Microsoft YaHei.*PingFang SC.*sans-serif/)
    if (size.label === 'desktop-1440x900' && layout.headerHeight) expect(layout.headerHeight).toBe(68)
    if (layout.detailBox) {
      expect(layout.detailBox.left).toBeGreaterThanOrEqual(15)
      expect(layout.detailBox.right).toBeLessThanOrEqual(size.width - 15)
      expect(layout.detailBox.top).toBeGreaterThanOrEqual(15)
      expect(layout.detailBox.bottom).toBeLessThanOrEqual(size.height - 15)
      expect(layout.detailBox.width).toBeCloseTo(Math.min(1060, size.width - 32), 0)
    }
    if (layout.workareaWidth && size.label === 'desktop-1440x900') {
      expect(layout.workareaWidth / layout.viewport).toBeGreaterThanOrEqual(0.9)
      expect(layout.answerCardWidth).toBeCloseTo(260, 0)
      expect(layout.questionTop).toBeCloseTo(layout.answerCardTop!, 0)
      if (layout.tabsBottom) expect(layout.questionTop! - layout.tabsBottom).toBeCloseTo(16, 0)
      expect(layout.overflowingNavigation).toEqual([])
    }
  }
  await page.setViewportSize({ width: 1440, height: 900 })
}

async function importGroups(page: Page, fixture: Fixture, scope: 'course' | 'standalone', info: TestInfo) {
  const bank = scope === 'course' ? fixture.course_bank_id : fixture.standalone_bank_id
  const path = scope === 'course' ? `/teacher/courses/${fixture.course_id}/banks/${bank}/` : `/teacher/banks/${bank}/`
  const api = `/api/v1${path}`
  const template = await page.request.get('/api/v1/teacher/import-templates/question-groups.csv')
  expect(template.status()).toBe(200)
  const fullTemplate = spawnSync(requireServerPython(), ['-c',
    'import csv,io,sys; rows=list(csv.DictReader(io.StringIO(sys.stdin.read().lstrip(chr(65279))))); writer=csv.DictWriter(sys.stdout,fieldnames=list(rows[0])); writer.writeheader(); writer.writerows([{**row,"一级知识点":sys.argv[1],"二级知识点":"","三级知识点":""} for row in rows])',
    scope === 'course' ? fixture.kc_code : `id:${fixture.private_kc_id}`],
    { input: await template.text(), encoding: 'utf8', env: { ...process.env, PYTHONUTF8: '1' } })
  expect(fullTemplate.status, fullTemplate.stderr).toBe(0)
  await page.goto(`${path}import/`)
  const images = await downloadTemplateImages(page, info)
  const csvPath = info.outputPath('all-eleven-types.csv')
  writeFileSync(csvPath, fullTemplate.stdout)
  const fullPreviewResponse = page.waitForResponse(response => new URL(response.url()).pathname === `${api}imports/questions/preview/` && response.request().method() === 'POST')
  await page.locator('input[type="file"]').first().setInputFiles([csvPath, ...images])
  const fullPreview = await data<Preview>(await fullPreviewResponse)
  expect(fullPreview.preview.rows).toHaveLength(24)
  expect(fullPreview.preview.summary).toEqual({ ready: 24, draft: 0, duplicate: 0, invalid: 0 })
  expect(fullPreview.preview.groups.map(group => group.kind).sort()).toEqual(['cloze', 'fill_blank', 'reading', 'words'])
  await expect(page.locator('[data-import-preview] .kt-desktop-only [data-import-row]')).toHaveCount(10)
  await page.getByRole('button', { name: '下一页', exact: true }).click()
  await expect(page.locator('[data-import-preview] .kt-desktop-only [data-import-row]')).toHaveCount(3)
  await page.getByRole('combobox', { name: /每页题目数/ }).selectOption('20')
  await expect(page.locator('[data-import-preview] .kt-desktop-only [data-import-row]')).toHaveCount(13)
  await expect(page.getByRole('button', { name: '确认导入 13 道题', exact: true })).toBeEnabled()
  await screenshots(page, info, `${scope}-all-eleven-types`, page.locator('main'))
  // A stable 18-item stress fixture exercises long grouped input independently of downloadable examples.
  const csv = readFileSync(new URL('./fixtures/english-group-stress.csv', import.meta.url), 'utf8')
    .replaceAll('KC1', scope === 'course' ? fixture.kc_code : `id:${fixture.private_kc_id}`)
  await page.goto(`${path}import/`)
  const previewResponse = page.waitForResponse(response => new URL(response.url()).pathname === `${api}imports/questions/preview/` && response.request().method() === 'POST')
  await page.locator('input[type="file"]').first().setInputFiles({ name: 'english-reading3-cloze15.csv', mimeType: 'text/csv', buffer: Buffer.from(csv) })
  const response = await previewResponse
  expect(response.status(), await response.text()).toBe(200)
  const preview = (await response.json()).data as Preview
  expect(preview.preview.rows).toHaveLength(18)
  expect(preview.preview.summary).toEqual({ ready: 18, draft: 0, duplicate: 0, invalid: 0 })
  expect(preview.preview.groups.map(group => [group.source_group_code, group.kind, group.item_count, Number(group.total_max_score), group.classification])).toEqual([
    ['reading-3', 'reading', 3, 6, 'ready'], ['cloze-15', 'cloze', 15, 15, 'ready'],
  ])
  expect(preview.preview.rows.every(row => row.classification === 'ready')).toBe(true)
  const rows = page.locator('[data-import-preview] .kt-desktop-only [data-import-row]')
  await expect(rows).toHaveCount(2)
  for (const group of preview.preview.groups) await expect(page.locator(`.kt-desktop-only [data-import-row="group:${group.group_id}"] input[type="checkbox"]`)).toBeChecked()
  await rows.nth(0).getByRole('button', { name: /查看/ }).click()
  const groupPreview = page.locator('[data-question-detail]').getByRole('region', { name: '完整题目预览', exact: true })
  await groupPreview.locator('[data-student-group-preview]').click()
  await expect(groupPreview.locator('[data-group-question]')).toHaveCount(3)
  await expect(groupPreview).not.toContainText('答案：')
  await screenshots(page, info, `${scope}-import-reading`, groupPreview)
  await page.getByRole('button', { name: '关闭题目详情', exact: true }).click()
  await rows.nth(1).getByRole('button', { name: /查看/ }).click()
  await groupPreview.locator('[data-student-group-preview]').click()
  await expect(groupPreview.locator('[data-group-item]')).toHaveCount(15)
  await screenshots(page, info, `${scope}-import-cloze`, groupPreview)
  await page.getByRole('button', { name: '关闭题目详情', exact: true }).click()
  const confirmResponse = page.waitForResponse(result => new URL(result.url()).pathname === `${api}imports/questions/confirm/` && result.request().method() === 'POST')
  await page.locator(scope === 'course' ? '[data-confirm-import]' : '[data-confirm-standalone-import]').click()
  const confirmed = await confirmResponse
  expect(confirmed.status(), await confirmed.text()).toBe(201)
  expect(confirmed.request().postDataJSON()).toMatchObject({ selected_row_ids: [], selected_group_ids: preview.preview.groups.map(group => group.group_id) })
  const groups = await data<Group[]>(await page.request.get(`${api}groups/`))
  expect(groups).toHaveLength(2)
  expect(groups.reduce((count, group) => count + group.members.length, 0)).toBe(18)
  for (const group of groups) {
    await page.goto(`${path}questions/0/edit/?group=${group.id}`)
    const editor = page.getByRole('region', { name: '完整题目编辑', exact: true })
    await expect(editor.getByLabel('题目标题', { exact: true })).toHaveValue(group.title)
    await expect(editor.locator('[data-group-max-score]')).toHaveCount(group.members.length)
    const instructions = `${group.instructions}\nAnswer every item.`
    await editor.getByLabel('作答说明', { exact: true }).fill(instructions)
    await screenshots(page, info, `${scope}-edit-${group.kind}`, editor)
    const saved = page.waitForResponse(result => new URL(result.url()).pathname === `${api}groups/${group.id}/` && result.request().method() === 'PUT')
    await editor.getByRole('button', { name: '保存题目', exact: true }).click()
    const response = await saved
    expect(response.status(), await response.text()).toBe(200)
    const updated = (await response.json()).data as Group
    expect(updated.revision).toBe(group.revision + 1)
    expect(updated.instructions).toBe(instructions)
    expect(updated.members.map(member => member.item_key)).toEqual(group.members.map(member => member.item_key))
    expect(updated.members.map(member => Number(member.max_score))).toEqual(group.members.map(member => Number(member.max_score)))
  }
  return { bank, api }
}

async function publishCourse(page: Page, fixture: Fixture, info: TestInfo) {
  await page.goto(`/teacher/courses/${fixture.course_id}/experiments/${fixture.course_experiment_id}/edit/`)
  await page.locator('#sampling-advanced-mode').selectOption('total_count')
  const fullPool = page.getByLabel('每位学生总题数', { exact: true })
  await expect(fullPool).toBeEnabled()
  await fullPool.fill('2')
  const description = 'R3 English groups configured through the teacher form.'
  await page.getByLabel('实验描述（可选）', { exact: true }).fill(description)
  await screenshots(page, info, 'course-publish-form', page.locator('form').filter({ has: fullPool }))
  const saved = page.waitForResponse(response => response.request().method() === 'PATCH'
    && new URL(response.url()).pathname === `/api/v1/teacher/courses/${fixture.course_id}/experiments/${fixture.course_experiment_id}/`)
  await page.getByRole('button', { name: '保存草稿', exact: true }).click()
  const response = await saved
  expect(response.status(), await response.text()).toBe(200)
  expect(response.request().postDataJSON()).toMatchObject({ description,
    sampling: { mode: 'total_count', total_count: 2, match_scope: 'any_level', nodes: [], type_quotas: [] } })
  expect((await response.json()).data.sampling.mode).toBe('total_count')
  await expect(page).toHaveURL(new RegExp(`/teacher/courses/${fixture.course_id}/experiments/${fixture.course_experiment_id}/$`))
  await page.getByRole('button', { name: '发布到班级', exact: true }).click()
  const classRow = page.locator('.kt-publish-class-table tbody tr').filter({ has: page.locator(`#publish-class-${fixture.class_id}`) })
  await classRow.locator('input[type="checkbox"]').check()
  const savedPlan = page.waitForResponse(result => /\/publish-plans\/\d+\/setup\/$/.test(new URL(result.url()).pathname) && result.request().method() === 'PUT')
  await page.locator('[data-action="save-draft"]').click()
  const plan = await data<Plan>(await savedPlan)
  expect(plan.can_preview, JSON.stringify(plan.blocking_issues)).toBe(true)
  await screenshots(page, info, 'course-class-schedule', page.locator('.kt-sitev2-plan-layout'))
  await page.locator('[data-action="run-preview"]').click()
  await expect(page.locator('[data-action="enter-confirmation"]')).toBeVisible({ timeout: 90_000 })
  const review = await data<{ plan: Plan; preview: { item_count: number; participant_count: number } }>(await page.request.get(`/api/v1/teacher/publish-plans/${plan.id}/review/`))
  expect(review.preview.item_count).toBe(18)
  expect(review.preview.participant_count).toBe(1)
  await screenshots(page, info, 'course-publication-review', page.locator('main'))
  await page.locator('[data-action="enter-confirmation"]').click()
  await screenshots(page, info, 'course-publication-confirmation', page.locator('main'))
  await page.locator('[data-action="commit-publish"]').click()
  await expect(page).toHaveURL(new RegExp(`/teacher/courses/${fixture.course_id}/experiments/${fixture.course_experiment_id}/$`), { timeout: 90_000 })
  const tasks = await data<{ items: Array<{ id: number; class_id: number }> }>(await page.request.get(`/api/v1/teacher/experiments/${fixture.course_experiment_id}/class-tasks/`))
  expect(tasks.items).toHaveLength(1)
  expect(tasks.items[0]!.class_id).toBe(fixture.class_id)
  return tasks.items[0]!.id
}

function noGradingFields(group: Group) {
  for (const member of group.members) {
    for (const forbidden of ['correct_answer', 'accepted_answers', 'is_correct', 'score', 'explanation']) expect(member).not.toHaveProperty(forbidden)
  }
}

async function save(page: Page, answer: string, edit: () => Promise<unknown>) {
  const saved = page.waitForResponse(response => response.url().includes('/draft/') && response.request().method() === 'POST'
    && response.request().postDataJSON()?.answer === answer, { timeout: 25_000 })
  void saved.catch(() => undefined)
  try {
    await edit()
    const response = await saved
    expect(response.status(), await response.text()).toBe(200)
    await expect(page.locator('[data-save-status]')).toContainText('已保存')
  } catch (error) {
    const path = test.info().outputPath('draft-save-failure.png')
    await page.screenshot({ path, fullPage: true })
    writeFileSync(test.info().outputPath('draft-save-failure.html'), await page.content())
    throw error
  }
}

async function currentRuntime(page: Page) {
  const response = await page.request.get(`/api/v1${new URL(page.url()).pathname}`)
  return data<Runtime>(response)
}

async function answerPaper(page: Page, fixture: Fixture, info: TestInfo) {
  const scope = 'course'
  await page.goto(`/student/courses/${fixture.course_id}/`)
  const task = page.locator('.kt-student-task-row').filter({ has: page.getByRole('heading', { name: 'R3 E2E course English groups', exact: true }) })
  await task.locator('[data-task-action="start"]').click()
  await expect(page).toHaveURL(/\/student\/sessions\/\d+\/questions\/\d+\/$/)
  try {
    await expect(page.locator('.kt-assessment-focus')).toHaveAttribute('data-init-state', 'ready', { timeout: 30_000 })
  } catch (error) {
    await page.screenshot({ path: info.outputPath(`${scope}-initialization-failure.png`), fullPage: true })
    throw error
  }
  const reading = page.locator('.kt-question-group--reading')
  await expect(reading.locator('input[type="radio"]').first()).toBeEnabled({ timeout: 30_000 })
  const runtime = await currentRuntime(page)
  noGradingFields(runtime.question_group)
  expect(runtime.question_group.members).toHaveLength(3)
  const before = await data<{ result: Record<string, unknown> }>(await page.request.get(`/api/v1/student/sessions/${runtime.session_id}/summary/`))
  expect(before.result).not.toHaveProperty('groups')
  expect(before.result).not.toHaveProperty('score_progress')
  const typography = await reading.locator('.kt-question-group__material').evaluate(element => ({
    size: Number.parseFloat(getComputedStyle(element).fontSize), lineHeight: Number.parseFloat(getComputedStyle(element).lineHeight),
  }))
  expect(typography.size).toBeGreaterThanOrEqual(18)
  expect(typography.lineHeight / typography.size).toBeGreaterThanOrEqual(1.5)
  await screenshots(page, info, `${scope}-student-reading`, reading)
  const choices = ['A', 'B', 'A']
  for (let index = 0; index < choices.length; index += 1) {
    const radio = reading.locator('[data-group-question]').nth(index).locator(`input[value="${choices[index]}"]`)
    await save(page, choices[index]!, () => radio.check())
  }
  await page.getByRole('navigation', { name: '题目切换', exact: true }).getByRole('button', { name: /短文填空/ }).click()
  const cloze = page.locator('.kt-question-group--cloze')
  await expect(cloze.locator('[data-group-item]')).toHaveCount(15)
  noGradingFields((await currentRuntime(page)).question_group)
  const first = cloze.getByRole('textbox', { name: '第 1 空', exact: true })
  await save(page, 'temporary', () => first.fill('temporary'))
  await save(page, '', () => first.clear())
  await page.reload()
  await expect(page.locator('.kt-assessment-focus')).toHaveAttribute('data-init-state', 'ready', { timeout: 30_000 })
  await expect(first).toHaveValue('')
  await expect(first).toBeEnabled({ timeout: 30_000 })
  for (let index = 0; index < clozeAnswers.length; index += 1) {
    const field = cloze.getByRole('textbox', { name: `第 ${index + 1} 空`, exact: true })
    await save(page, clozeAnswers[index]!, () => field.fill(clozeAnswers[index]!))
  }
  await screenshots(page, info, `${scope}-student-cloze`, cloze)
  await page.getByRole('navigation', { name: '题目切换', exact: true }).getByRole('button', { name: /阅读理解/ }).click()
  for (let index = 0; index < choices.length; index += 1) await expect(reading.locator('[data-group-question]').nth(index).locator(`input[value="${choices[index]}"]`)).toBeChecked()
  await page.reload()
  await expect(page.locator('.kt-assessment-focus')).toHaveAttribute('data-init-state', 'ready', { timeout: 30_000 })
  await expect(reading.locator('input[value="A"]').first()).toBeChecked()
  await page.getByRole('navigation', { name: '题目切换', exact: true }).getByRole('button', { name: /短文填空/ }).click()
  for (let index = 0; index < clozeAnswers.length; index += 1) await expect(cloze.getByRole('textbox', { name: `第 ${index + 1} 空`, exact: true })).toHaveValue(clozeAnswers[index]!)
  await submitWholePaper(page)
  const summary = await data<Summary>(await page.request.get(`/api/v1/student/sessions/${runtime.session_id}/summary/`))
  expect(summary.status).toBe('completed')
  expect(summary.submission_count).toBe(18)
  expect(summary.attempt_count).toBe(18)
  expect(summary.result.score_progress).toMatchObject({ graded_count: 18, item_count: 18, pending_count: 0 })
  expect(Number(summary.result.score_progress.earned)).toBe(16)
  expect(Number(summary.result.score_progress.paper_maximum)).toBe(21)
  expect(Number(summary.result.score_progress.graded_maximum)).toBe(21)
  expect(summary.result.groups.map(group => group.members.length)).toEqual([3, 15])
  expect(summary.result.items.filter(item => item.is_correct)).toHaveLength(14)
  expect(summary.result.groups.flatMap(group => group.members).every(member => 'response_value' in member)).toBe(true)
  const results = page.getByRole('region', { name: '题目结果', exact: true })
  await expect(results.locator('.kt-question-group--reading input[value="A"]').first()).toBeChecked()
  await expect(results.locator('.kt-question-group--reading input[value="B"]').nth(1)).toBeChecked()
  await expect(results.getByRole('textbox', { name: '第 1 空', exact: true })).toHaveValue('Saturday')
  await expect(results.locator('[data-score-progress]')).toContainText(/16(?:\.0+)?\s*\/\s*21/)
  await screenshots(page, info, `${scope}-student-results`, results)
  return runtime.session_id
}

async function exportCourse(page: Page, fixture: Fixture, info: TestInfo) {
  const workerEnvironment = { ADVANCED_DATA_MATERIALIZED_ENABLED: '1', ADVANCED_DATA_MATERIALIZED_COURSE_IDS: String(fixture.course_id) }
  django(['reconcile_research_materializations', '--limit', '1'], workerEnvironment)
  const query = `scope_version=2&experiment_id=${fixture.course_experiment_id}&class_mode=all`
  const catalogURL = `/api/v1/teacher/courses/${fixture.course_id}/data/exports/?${query}`
  await expect.poll(async () => {
    const catalog = await data<{ tables: Array<{ path: string }>; preparation: { pending_count: number } }>(await page.request.get(catalogURL))
    expect(catalog.tables).toHaveLength(8)
    if (catalog.preparation.pending_count) django(['run_research_materializations', '--once'], workerEnvironment)
    return catalog.preparation.pending_count
  }, { timeout: 90_000, intervals: [300, 1000] }).toBe(0)
  let job = await mutation<ExportJob>(page, `/api/v1/teacher/courses/${fixture.course_id}/data/export-jobs/`, {
    package: 'experiment', confirm_identified: true,
    scope: { scope_version: 2, experiment_id: fixture.course_experiment_id, class_mode: 'all' },
  }, 'POST', [201])
  await expect.poll(async () => {
    if (job.status === 'queued') django(['run_export_jobs', '--once'], workerEnvironment)
    job = await data<ExportJob>(await page.request.get(`/api/v1/teacher/courses/${fixture.course_id}/data/export-jobs/${job.job_id}/`))
    expect(job.status).not.toBe('failed')
    return job.status
  }, { timeout: 90_000, intervals: [300, 1000] }).toBe('succeeded')
  expect(job.download_url).toBeTruthy()
  const download = await page.request.get(job.download_url!)
  expect(download.status()).toBe(200)
  const path = info.outputPath('english-groups-standard-export.zip')
  writeFileSync(path, await download.body())
  const inspected = spawnSync(requireServerPython(), ['-c', [
    'import csv,io,json,sys,zipfile',
    'with zipfile.ZipFile(sys.argv[1]) as z:',
    " rows=list(csv.DictReader(io.StringIO(z.read('05_单次实验_逐题作答表.csv').decode('utf-8-sig'))))",
    " library=json.loads(z.read('课程题目固定库.json'))",
    " print(json.dumps({'files':len(z.namelist()),'rows':len(rows),'sessions':len({r['sessionID'] for r in rows}),'distinct_items':len({r['课程内题目ID'] for r in rows}),'correct':sum(r['是否正确']=='是' for r in rows),'library_rows':len(library),'grouped_library_items':sum('题组' in item for item in library),'headers':list(rows[0])}))",
  ].join('\n'), path], { encoding: 'utf8', timeout: 20_000, env: { ...process.env, PYTHONUTF8: '1' } })
  expect(inspected.status, inspected.stderr).toBe(0)
  const result = JSON.parse(inspected.stdout)
  expect(result).toEqual({ files: 8, rows: 18, sessions: 1, distinct_items: 18, correct: 14, library_rows: 18, grouped_library_items: 18,
    headers: 'sessionID 学号 姓名 课程内题目ID 分配顺序 首次访问顺序 选项呈现顺序 访问次数 各次点击题目时间 各次进入题目时间 各次离开题目时间 正式提交时间 最终答案 答案状态 是否正确 comment'.split(' ') })
  await info.attach('english-groups-export-verification', { body: JSON.stringify(result), contentType: 'application/json' })
}

test('English groups import and edit both banks, then complete a course paper through standard export', async ({ browser, page }, info) => {
  test.setTimeout(600_000)
  const fixture = JSON.parse(django(['seed_e2e_english_groups'])) as Fixture
  await login(page, fixture.teacher, 'teacher')
  const course = await importGroups(page, fixture, 'course', info)
  const standalone = await importGroups(page, fixture, 'standalone', info)
  const taskId = await publishCourse(page, fixture, info)
  const context = await browser.newContext({ baseURL, viewport: { width: 1440, height: 900 } })
  try {
    const student = await context.newPage()
    const browserErrors = info.outputPath('student-browser-errors.log')
    student.on('pageerror', error => writeFileSync(browserErrors, `${error.stack || error.message}\n`, { flag: 'a' }))
    student.on('console', message => { if (message.type() === 'error') writeFileSync(browserErrors, `${message.text()}\n`, { flag: 'a' }) })
    await login(student, fixture.student, 'student')
    for (const api of [course.api, standalone.api]) expect((await student.request.get(`${api}groups/`)).status()).toBe(403)
    const courseSession = await answerPaper(student, fixture, info)
    const taskResult = await data<Result & { correctness: { correct: number; graded: number } }>(await student.request.get(`/api/v1/student/tasks/${taskId}/results/`))
    expect(taskResult.correctness).toMatchObject({ correct: 14, graded: 18 })
    expect((await page.request.get(`/api/v1/student/sessions/${courseSession}/summary/`)).status()).toBe(403)
    await exportCourse(page, fixture, info)
    await info.attach('english-groups-scopes', { body: JSON.stringify({ course_id: fixture.course_id, course_bank_id: course.bank, standalone_bank_id: standalone.bank, courseSession, taskId }), contentType: 'application/json' })
  } finally {
    await context.close()
  }
})
