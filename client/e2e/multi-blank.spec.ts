import { expect, test, type Page } from '@playwright/test'
import { spawnSync } from 'node:child_process'
import { writeFileSync } from 'node:fs'
import { requireServerPython, serverDir } from '../scripts/e2e-runtime.mjs'
import { requireIsolatedPostgres } from '../scripts/e2e-database.mjs'
import { acceptResearchConsentIfRequired } from './research-consent'
import { submitWholePaper } from './whole-paper'

function django(args: string[]) {
  const result = spawnSync(requireServerPython(), ['manage.py', ...args], {
    cwd: serverDir, encoding: 'utf8', timeout: 60_000,
    env: { ...process.env, ...requireIsolatedPostgres(process.env), PYTHONUTF8: '1',
      ADVANCED_DATA_MATERIALIZED_ENABLED: '1', ADVANCED_DATA_MATERIALIZED_COURSE_IDS: '*' },
  })
  if (result.status !== 0) throw new Error(result.stderr || result.stdout)
  return result.stdout
}
async function login(page: Page, credentials: { username: string; password: string }) {
  await page.goto('/login/')
  await page.getByLabel('账号或学号').fill(credentials.username)
  await page.getByLabel('密码').fill(credentials.password)
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).not.toHaveURL(/\/login\//)
}

test('multi-blank simple import preserves two scores, inputs, feedback and export rows', async ({ page, browser }, info) => {
  test.setTimeout(240_000)
  const fixture = JSON.parse(django(['shell', '-c', `import apps.question_bank.management.commands.seed_e2e_english_groups as m; m.COURSE_TERM='blanks-${Date.now()}'; m.STANDALONE_BANK+='-blanks-${Date.now()}'; m.Command().handle()`]).trim().split('\n').at(-1)!)
  await login(page, fixture.teacher)
  const api = `/api/v1/teacher/courses/${fixture.course_id}/banks/${fixture.course_bank_id}/`
  await page.goto(`/teacher/courses/${fixture.course_id}/banks/${fixture.course_bank_id}/import/`)
  await expect(page.locator('a[href*="import-templates/question-groups."]')).toHaveCount(3)
  await page.screenshot({ path: info.outputPath('unified-template-downloads.png'), fullPage: true })
  const template = await page.request.get('/api/v1/teacher/import-templates/question-groups.csv')
  expect(template.status()).toBe(200)
  const selectedExample = JSON.parse(django(['shell', '-c', `import csv,io,json; rows=list(csv.reader(io.StringIO(${JSON.stringify(await template.text())}.lstrip('\\ufeff')))); out=io.StringIO(); writer=csv.writer(out); writer.writerow(rows[0]); writer.writerows(rows[-2:]); print(json.dumps(out.getvalue()))`]).trim().split('\n').at(-1)!)
  await page.locator('input[type=file]').first().setInputFiles({ name: 'blanks.csv', mimeType: 'text/csv', buffer: Buffer.from(selectedExample) })
  await expect(page.getByRole('button', { name: '确认导入 1 道题', exact: true })).toBeEnabled()
  await page.locator('.kt-desktop-only [data-import-row]').first().getByRole('button', { name: /查看/ }).click()
  await page.locator('[data-student-group-preview]').click()
  await expect(page.locator('[data-question-detail] [data-group-item]')).toHaveCount(2)
  await expect(page.locator('[data-question-detail] article input')).toHaveCount(0)
  await expect(page.locator('[data-question-detail] [data-group-answer-area] input')).toHaveCount(2)
  for (const width of [1440, 390, 720]) {
    await page.setViewportSize({ width, height: width === 720 ? 450 : 900 })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
    await page.screenshot({ path: info.outputPath(`import-${width}.png`), fullPage: true })
  }
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.getByRole('button', { name: '关闭题目详情', exact: true }).click()
  const confirmed = page.waitForResponse(response => response.url().endsWith('imports/questions/confirm/') && response.request().method() === 'POST')
  await page.locator('[data-confirm-import]').click()
  expect((await confirmed).status()).toBe(201)
  const groups = (await (await page.request.get(api + 'groups/')).json()).data
  expect(groups).toHaveLength(1)
  expect(groups[0].members.map((member: { max_score: string }) => member.max_score)).toEqual(['2.00', '1.00'])
  await page.goto(`/teacher/courses/${fixture.course_id}/banks/${fixture.course_bank_id}/questions/0/edit/?group=${groups[0].id}`)
  const editor = page.getByRole('region', { name: '完整题目编辑', exact: true })
  await expect(editor.locator('.kt-group-editor__material')).toHaveValue(/k=____/)
  await expect(editor.locator('[data-group-max-score]')).toHaveCount(2)
  await expect(editor).not.toContainText('{{b01}}')
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 900 })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
    await page.screenshot({ path: info.outputPath(`editor-${width}.png`), fullPage: true })
  }
  await page.setViewportSize({ width: 1440, height: 900 })
  const savedEditor = page.waitForResponse(response => response.url().endsWith(`groups/${groups[0].id}/`) && response.request().method() === 'PUT')
  await editor.getByRole('button', { name: '保存题目', exact: true }).click()
  expect((await savedEditor).status()).toBe(200)
  const published = JSON.parse(django(['shell', '-c', [
    'import json',
    'from apps.experiments.models import Experiment',
    'from apps.experiments.publication_services import get_or_create_publish_plan,save_plan_selection,generate_preview,commit_publish_plan',
    `e=Experiment.objects.get(pk=${fixture.course_experiment_id})`,
    'p=get_or_create_publish_plan(e,e.owner)',
    `p=save_plan_selection(p,e.owner,expected_revision=p.plan_revision,class_ids=[${fixture.class_id}],exclusions_by_class={})`,
    'generate_preview(p,e.owner,expected_revision=p.plan_revision)',
    'p.refresh_from_db()',
    'commit_publish_plan(p,e.owner,expected_revision=p.plan_revision)',
    'print(json.dumps({"task":e.class_tasks.get().pk}))',
  ].join('\n')]).trim().split('\n').at(-1)!)
  expect(published.task).toBeTruthy()
  let completedSessionId = 0
  const studentContext = await browser.newContext({ baseURL: info.project.use.baseURL })
  try {
    const student = await studentContext.newPage()
    await login(student, fixture.student)
    await acceptResearchConsentIfRequired(student)
    await student.goto(`/student/courses/${fixture.course_id}/`)
    await student.locator('[data-task-action="start"]').click()
    await expect(student.locator('.kt-assessment-focus')).toHaveAttribute('data-init-state', 'ready')
    const group = student.locator('.kt-question-group--fill_blank')
    await expect(group.locator('[data-group-item]')).toHaveCount(2)
    await expect(group.locator('article input')).toHaveCount(0)
    await expect(group.getByRole('region', { name: '作答区', exact: true })).toBeVisible()
    const sessionId = Number(student.url().match(/sessions\/(\d+)/)![1])
    completedSessionId = sessionId
    for (const [index, answer] of ['-0.5', '6/1'].entries()) {
      const saved = student.waitForResponse(response => response.url().includes('/draft/') && response.request().method() === 'POST' && response.request().postDataJSON()?.answer === answer)
      await group.getByRole('textbox', { name: `第 ${index + 1} 空`, exact: true }).fill(answer)
      expect((await saved).status()).toBe(200)
    }
    await student.reload()
    await expect(group.getByRole('textbox', { name: '第 1 空', exact: true })).toHaveValue('-0.5')
    await expect(group.getByRole('textbox', { name: '第 2 空', exact: true })).toHaveValue('6/1')
    for (const width of [1440, 390, 720]) {
      await student.setViewportSize({ width, height: width === 720 ? 450 : 900 })
      expect(await student.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
      await student.screenshot({ path: info.outputPath(`student-${width}.png`), fullPage: true })
    }
    await student.setViewportSize({ width: 1440, height: 900 })
    await submitWholePaper(student)
    const summary = (await (await student.request.get(`/api/v1/student/sessions/${sessionId}/summary/`)).json()).data
    expect(summary.attempt_count).toBe(2)
    expect(Number(summary.result.score_progress.earned)).toBe(3)
    expect(summary.result.groups[0].members.map((member: { is_correct: boolean }) => member.is_correct)).toEqual([true, true])
    await expect(student.locator('.kt-group-results')).toContainText('-0.5')
    await expect(student.locator('.kt-group-results')).toContainText('6/1')
    await expect(student.locator('.kt-group-results article.kt-question-group__material input')).toHaveCount(0)
    await expect(student.locator('.kt-group-results [data-group-answer-area] input')).toHaveCount(2)
    for (const width of [1440, 390]) {
      await student.setViewportSize({ width, height: 900 })
      await student.screenshot({ path: info.outputPath(`feedback-${width}.png`), fullPage: true })
    }
  } finally { await studentContext.close() }
  django(['shell', '-c', `from apps.analytics.research_materialization_jobs import _enqueue_eligible_ids; _enqueue_eligible_ids([${completedSessionId}])`])
  const catalogUrl = `/api/v1/teacher/courses/${fixture.course_id}/data/exports/?scope_version=2&experiment_id=${fixture.course_experiment_id}&class_mode=all`
  await expect.poll(async () => {
    const catalog = (await (await page.request.get(catalogUrl)).json()).data
    if (catalog.preparation.pending_count) django(['run_research_materializations', '--once'])
    return catalog.preparation.pending_count
  }, { timeout: 60_000 }).toBe(0)
  const csrf = (await (await page.request.get('/api/v1/auth/csrf/')).json()).data.csrf_token
  const response = await page.request.post(`/api/v1/teacher/courses/${fixture.course_id}/data/export-jobs/`, { headers: { 'X-CSRFToken': csrf },
    data: { package: 'experiment', confirm_identified: true, scope: { scope_version: 2, experiment_id: fixture.course_experiment_id, class_mode: 'all' } } })
  expect(response.status(), await response.text()).toBe(201)
  let job = (await response.json()).data
  await expect.poll(async () => {
    if (job.status === 'queued') django(['run_export_jobs', '--once'])
    job = (await (await page.request.get(`/api/v1/teacher/courses/${fixture.course_id}/data/export-jobs/${job.job_id}/`)).json()).data
    return job.status
  }, { timeout: 60_000 }).toBe('succeeded')
  const exportPath = info.outputPath('multi-blank-export.zip')
  writeFileSync(exportPath, await (await page.request.get(job.download_url)).body())
  const check = spawnSync(requireServerPython(), ['-c', [
    'import csv,io,json,sys,zipfile',
    'with zipfile.ZipFile(sys.argv[1]) as z:',
    " rows=list(csv.DictReader(io.StringIO(z.read('05_单次实验_逐题作答表.csv').decode('utf-8-sig'))))",
    " library=json.loads(z.read('课程题目固定库.json'))",
    " print(json.dumps({'files':len(z.namelist()),'answers':[r['最终答案'] for r in rows],'correct':[r['是否正确'] for r in rows],'ids':len({r['课程内题目ID'] for r in rows}),'blanks':[q['题组']['空位编号'] for q in library],'weights':[q['题组']['分值'] for q in library]}))",
  ].join('\n'), exportPath], { encoding: 'utf8', env: { ...process.env, PYTHONUTF8: '1' } })
  expect(check.status, check.stderr).toBe(0)
  expect(JSON.parse(check.stdout)).toEqual({ files: 8, answers: ['-0.5', '6/1'], correct: ['是', '是'], ids: 2, blanks: ['b01', 'b02'], weights: ['2.00', '1.00'] })
})

test('legacy published two-blank question restores separate inputs and retains one submission', async ({ page }, info) => {
  test.setTimeout(180_000)
  const fixture = JSON.parse(django(['shell', '-c', `import apps.question_bank.management.commands.seed_e2e_english_groups as m; m.COURSE_TERM='legacy-${Date.now()}'; m.STANDALONE_BANK+='-legacy-${Date.now()}'; m.Command().handle()`]).trim().split('\n').at(-1)!)
  django(['shell', '-c', [
    'from apps.question_bank.models import Question,QuestionConcept,Concept',
    'from apps.experiments.models import Experiment',
    'from apps.experiments.publication_services import get_or_create_publish_plan,save_plan_selection,generate_preview,commit_publish_plan',
    `q=Question.objects.create(bank_id=${fixture.course_bank_id},sequence=1,question_type='fill_blank',text='k=____; P=____ W.',correct_answer='-1/2；6')`,
    `QuestionConcept.objects.create(question=q,concept=Concept.objects.filter(course_id=${fixture.course_id}).first(),is_primary=True)`,
    `e=Experiment.objects.get(pk=${fixture.course_experiment_id})`,
    "e.sampling_mode='total_count'; e.sampling_total_count=1; e.save()",
    'from apps.experiments.models import ExperimentSamplingKC',
    'ExperimentSamplingKC.objects.get_or_create(experiment=e,concept=q.concept_links.first().concept)',
    'p=get_or_create_publish_plan(e,e.owner)',
    `p=save_plan_selection(p,e.owner,expected_revision=p.plan_revision,class_ids=[${fixture.class_id}],exclusions_by_class={})`,
    'generate_preview(p,e.owner,expected_revision=p.plan_revision)',
    'p.refresh_from_db()',
    'commit_publish_plan(p,e.owner,expected_revision=p.plan_revision)',
  ].join('\n')])
  await login(page, fixture.student)
  await acceptResearchConsentIfRequired(page)
  await page.goto(`/student/courses/${fixture.course_id}/`)
  await page.locator('[data-task-action="start"]').click()
  await expect(page.locator('.kt-assessment-focus')).toHaveAttribute('data-init-state', 'ready')
  const area = page.locator('[data-legacy-answer-area]')
  await expect(area.locator('input')).toHaveCount(2)
  const sessionId = Number(page.url().match(/sessions\/(\d+)/)![1])
  for (const [index, value] of ['-0.5', '6'].entries()) {
    const saved = page.waitForResponse(response => response.url().includes('/draft/') && response.request().method() === 'POST'
      && response.request().postDataJSON()?.answer === (index === 0 ? '-0.5；' : '-0.5；6'))
    await area.getByRole('textbox', { name: `第 ${index + 1} 空`, exact: true }).fill(value)
    expect((await saved).status()).toBe(200)
  }
  await page.reload()
  await expect(area.getByRole('textbox', { name: '第 1 空', exact: true })).toHaveValue('-0.5')
  await expect(area.getByRole('textbox', { name: '第 2 空', exact: true })).toHaveValue('6')
  for (const [width, height] of [[1440, 900], [390, 844], [720, 450]]) {
    await page.setViewportSize({ width: width!, height: height! })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
    await page.screenshot({ path: info.outputPath(`legacy-input-${width}.png`), fullPage: true })
  }
  await submitWholePaper(page)
  const summary = (await (await page.request.get(`/api/v1/student/sessions/${sessionId}/summary/`)).json()).data
  expect(summary.attempt_count).toBe(1)
  const stored = JSON.parse(django(['shell', '-c', `import json; from apps.assessment.models import SubmissionEvent; print(json.dumps(list(SubmissionEvent.objects.filter(attempt__session_id=${sessionId}).values('final_answer','is_correct','score')),default=str))`]).trim().split('\n').at(-1)!)
  expect(stored).toEqual([{ final_answer: '-0.5；6', is_correct: true, score: '1.00' }])
})
