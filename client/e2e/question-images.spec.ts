import { spawnSync } from 'node:child_process'
import { expect, test, type Page, type TestInfo } from '@playwright/test'
import { requireIsolatedPostgres } from '../scripts/e2e-database.mjs'
import { requireServerPython, serverDir } from '../scripts/e2e-runtime.mjs'
import { acceptResearchConsentIfRequired } from './research-consent'
import { submitWholePaper } from './whole-paper'

function django(source: string) {
  const result = spawnSync(requireServerPython(), ['manage.py', 'shell', '-c', source], {
    cwd: serverDir, env: { ...process.env, ...requireIsolatedPostgres(process.env), PYTHONUTF8: '1' }, encoding: 'utf8', timeout: 60_000,
  })
  expect(result.status, result.stderr + result.stdout).toBe(0)
  return result.stdout.trim().split('\n').at(-1)!
}

async function login(page: Page, username: string, password: string) {
  await page.goto('/login/')
  await page.getByLabel('账号或学号', { exact: true }).fill(username)
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).not.toHaveURL(/\/login\//)
  if (username === 'S001') await acceptResearchConsentIfRequired(page)
}

async function capture(page: Page, info: TestInfo, name: string) {
  for (const [label, width, height] of [['desktop', 1440, 900], ['mobile', 390, 844], ['zoom-equivalent', 720, 450]] as const) {
    await page.setViewportSize({ width, height })
    await page.evaluate(() => scrollTo(0, 0))
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width + 1)
    await page.screenshot({ path: info.outputPath(`${name}-${label}.png`), fullPage: true })
  }
  await page.setViewportSize({ width: 1440, height: 900 })
}

test('images remain ordinary question content through import, preview, answering and feedback', async ({ page, browser }, info) => {
  test.setTimeout(240_000)
  const image = Buffer.from(django("from PIL import Image, ImageDraw; import io,base64; image=Image.new('RGB',(800,300),'#f7f9fc'); draw=ImageDraw.Draw(image); draw.rectangle((70,50,250,230),fill='#c9d9ed',outline='#285dbb',width=4); draw.polygon([(410,230),(540,50),(670,230)],fill='#dbe9dc',outline='#327354',width=4); data=io.BytesIO(); image.save(data,format='PNG'); print(base64.b64encode(data.getvalue()).decode())"), 'base64')
  await login(page, 'teacher', 'teacher123')
  const csrf = (await (await page.request.get('/api/v1/auth/csrf/')).json()).data.csrf_token
  const name = `Image compatibility ${Date.now()}`
  const created = await page.request.post('/api/v1/teacher/courses/1/banks/', { headers: { 'X-CSRFToken': csrf }, data: { name, visibility: 'private' } })
  expect(created.status(), await created.text()).toBe(201)
  const bank = (await created.json()).data
  const concepts = (await (await page.request.get('/api/v1/teacher/courses/1/concepts/?all=1')).json()).data.items
  const kc = concepts.find((item: { status: string }) => item.status === 'active').kc_code
  const template = await page.request.get('/api/v1/teacher/import-templates/question-groups.csv')
  const columns = (await template.text()).replace(/^\ufeff/, '').split(/\r?\n/)[0]!.split(',')
  const rows: Record<string, string>[] = [
    { 序号: '1', 题目: 'Observe the shapes.\n{{image:shapes.png}}', 题型: '单选题', 选项A: '{{image:shapes.png}}', 选项B: 'Another view {{image:shapes.png}}', 选项C: 'Neither shape', 选项D: 'Both shapes', 答案: 'A', 解析: 'Reference view {{image:shapes.png}}', 一级知识点: kc },
    { 序号: '2', 题目: 'Select the first word.', 题型: '单选题', 选项A: 'First word', 选项B: 'Second word', 答案: 'A', 一级知识点: kc },
    { 序号: '3', 题目: '{{image:missing.png}}', 题型: '单选题', 选项A: 'First', 选项B: 'Second', 答案: 'A', 一级知识点: kc },
  ]
  const cell = (value: string) => `"${value.replaceAll('"', '""')}"`
  const csv = [columns.join(','), ...rows.map(row => columns.map(column => cell(row[column] || '')).join(','))].join('\n')
  await page.goto(`/teacher/courses/1/banks/${bank.id}/import/`)
  const previewResponse = page.waitForResponse(response => response.url().endsWith('/imports/questions/preview/') && response.request().method() === 'POST')
  await page.locator('input[type=file]').setInputFiles([{ name: 'questions.csv', mimeType: 'text/csv', buffer: Buffer.from('\ufeff' + csv) }, { name: 'shapes.png', mimeType: 'image/png', buffer: image }])
  const preview = (await (await previewResponse).json()).data
  expect(preview.preview.summary).toMatchObject({ ready: 2, invalid: 1 })
  expect(preview.preview.rows[0].question_type).toBe('single_choice')
  expect(Object.keys(preview.preview.rows[0].images)).toHaveLength(1)
  const first = page.locator('[data-import-row]').first()
  await expect(first).toContainText('[图片]')
  await expect(first).not.toContainText('{{image:')
  await first.getByRole('button', { name: '查看题目 1' }).click()
  await expect(page.locator('[data-question-detail] img')).toHaveCount(4)
  await capture(page, info, 'teacher-import-detail')
  await page.getByRole('button', { name: '关闭题目详情' }).click()
  await page.getByRole('button', { name: '确认导入 2 道题', exact: true }).click()
  await page.getByRole('button', { name: '返回题库详情', exact: true }).click()
  await expect(page.locator('.kt-bank-question-section')).not.toContainText('{{image:')
  await page.setViewportSize({ width: 1022, height: 900 })
  const heading = (await page.locator('.kt-bank-detail-header h1').boundingBox())!
  const action = (await page.locator('[data-question-import]').boundingBox())!
  expect(heading.x + heading.width).toBeLessThan(action.x)
  expect(Math.abs(heading.y - action.y)).toBeLessThan(25)
  await page.screenshot({ path: info.outputPath('teacher-bank-1022.png'), fullPage: true })
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.locator('.kt-bank-question-section tbody tr').first().getByRole('button', { name: '查看', exact: true }).click()
  await expect(page.getByRole('dialog', { name: '完整题目预览' }).locator('img')).toHaveCount(3)
  await page.keyboard.press('Escape')

  const experimentId = Number(django([
    'from apps.accounts.models import User,CourseEnrollment',
    'from apps.question_bank.models import QuestionBank',
    'from apps.experiments.models import Experiment',
    'from apps.experiments.services import sync_experiment_questions,assign_students',
    'from apps.experiments.publication_services import get_or_create_publish_plan,save_plan_selection,generate_preview,commit_publish_plan',
    `bank=QuestionBank.objects.get(pk=${bank.id}); teacher=bank.owner; student=User.objects.get(username='S001')`,
    `experiment=Experiment.objects.create(course=bank.course,owner=teacher,created_by=teacher,question_bank=bank,name='${name}',selection_mode=Experiment.SelectionMode.ALL,sampling_mode=Experiment.SamplingMode.TOTAL_COUNT,sampling_total_count=2,ordering_mode=Experiment.Ordering.FIXED,feedback_mode=Experiment.Feedback.FULL,feedback_release_policy=Experiment.FeedbackReleasePolicy.AFTER_PERSONAL_SUBMISSION,confidence_mode=Experiment.ConfidenceMode.OFF)`,
    'sync_experiment_questions(experiment); assign_students(experiment,[student]); plan=get_or_create_publish_plan(experiment,teacher)',
    'plan=save_plan_selection(plan,teacher,expected_revision=plan.plan_revision,class_ids=[CourseEnrollment.objects.get(course=bank.course,student=student).class_group_id],exclusions_by_class={})',
    'generate_preview(plan,teacher,expected_revision=plan.plan_revision); plan.refresh_from_db(); commit_publish_plan(plan,teacher,expected_revision=plan.plan_revision)',
    'print(experiment.pk)',
  ].join('\n')))
  await page.goto(`/teacher/courses/1/experiments/${experimentId}/student-preview/`)
  await expect(page.locator('.kt-assessment-stem img')).toHaveCount(1)
  await capture(page, info, 'teacher-student-preview')

  const context = await browser.newContext({ baseURL: 'http://127.0.0.1:8000', viewport: { width: 1440, height: 900 } })
  try {
    const student = await context.newPage()
    await login(student, 'S001', 'student123')
    await student.goto('/student/courses/1/')
    await student.locator('.kt-student-task-row').filter({ has: student.getByRole('heading', { name, exact: true }) }).locator('[data-task-action=start]').click()
    await expect(student.locator('.kt-assessment-focus')).toHaveAttribute('data-init-state', 'ready', { timeout: 30_000 })
    await expect(student.locator('.kt-assessment-stem img')).toHaveCount(1)
    await expect(student.locator('#assessment-question-title')).toContainText('单选题')
    const options = student.locator('.kt-assessment-option')
    const bounds = await options.evaluateAll(elements => elements.map(element => element.getBoundingClientRect().toJSON()))
    expect(bounds[0].y).toBe(bounds[1].y)
    expect(bounds[0].x).toBeLessThan(bounds[1].x)
    const centered = await options.first().evaluate(element => {
      const option = element.getBoundingClientRect()
      const input = element.querySelector('input')!.getBoundingClientRect()
      const content = element.querySelector('.kt-question-content')!.getBoundingClientRect()
      const image = element.querySelector('img')!.getBoundingClientRect()
      return { controlOffset: Math.abs(input.y + input.height / 2 - option.y - option.height / 2), imageOffset: Math.abs(image.x + image.width / 2 - content.x - content.width / 2) }
    })
    expect(centered.controlOffset).toBeLessThan(2)
    expect(centered.imageOffset).toBeLessThan(2)
    await expect(student.locator('.kt-assessment-question-body')).not.toHaveCSS('overflow-y', 'auto')
    const stemImage = student.locator('.kt-assessment-stem img')
    const stemBounds = (await stemImage.boundingBox())!
    expect(stemBounds.width).toBeLessThanOrEqual(420)
    expect(stemBounds.height).toBeLessThanOrEqual(180)
    await options.first().getByRole('button', { name: '放大图片' }).click()
    await expect(student.getByRole('dialog', { name: '图片预览' })).toBeVisible()
    await student.keyboard.press('Escape')
    await expect(options.first().locator('input')).not.toBeChecked()
    await capture(student, info, 'student-image-choices')
    await options.first().click({ position: { x: 15, y: 15 } })
    await expect(options.first().locator('input')).toBeChecked()
    await student.locator('[data-next-question]').click()
    await expect(student.locator('.kt-assessment-stem')).toContainText('Select the first word.')
    const textBounds = await student.locator('.kt-assessment-option').evaluateAll(elements => elements.map(element => element.getBoundingClientRect().toJSON()))
    expect(textBounds[0].x).toBe(textBounds[1].x)
    expect(textBounds[0].y).toBeLessThan(textBounds[1].y)
    await capture(student, info, 'student-text-choices')
    await student.locator('input[value=A]').check()
    await submitWholePaper(student)
    await student.goto(`/student/courses/1/learning/experiments/${experimentId}/`)
    await student.getByRole('link', { name: '查看题目反馈' }).first().click()
    await expect(student.locator('.kt-feedback-option--student img')).toBeVisible()
    await expect(student.locator('.kt-feedback-answer-panel--analysis img')).toHaveCount(0)
    await student.locator('[data-show-answer]').click()
    await student.locator('[data-show-explanation]').click()
    await expect(student.locator('.kt-feedback-answer-panel--analysis img')).toBeVisible()
    await capture(student, info, 'student-image-feedback')
  } finally { await context.close() }
})
