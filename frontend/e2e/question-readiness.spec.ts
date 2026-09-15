import { expect, test } from '@playwright/test'
import { spawnSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { join } from 'node:path'
import { requireIsolatedPostgres } from '../scripts/e2e-database.mjs'

test('imports usable questions and explained drafts, repairs old drafts, and publishes usable inventory', async ({ page }, testInfo) => {
  test.setTimeout(120_000)
  await page.goto('/login/', { waitUntil: 'commit' })
  await page.getByLabel('账号或学号', { exact: true }).fill('teacher')
  await page.getByLabel('密码', { exact: true }).fill('teacher123')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL(/\/teacher\/courses\/$/)
  const csrf = (await (await page.request.get('/api/v1/auth/csrf/')).json()).csrf_token
  const headers = { 'X-CSRFToken': csrf }
  const created = await page.request.post('/api/v1/teacher/courses/1/banks/', {
    headers, data: { name: `UAT-READINESS-${Date.now()}`, description: '自动可用性回归', visibility: 'private' },
  })
  expect(created.status()).toBe(201)
  const bank = await created.json()
  const apiBase = `/api/v1/teacher/courses/1/banks/${bank.id}/`
  await page.goto(`/teacher/courses/1/banks/${bank.id}/import/`, { waitUntil: 'commit' })
  const columns = ['序号', '题目', '题型', '选项A', '选项B', '选项C', '选项D', '选项E', '选项F', '答案', '可接受答案', '解析', '难度', '一级知识点', '二级知识点', '三级知识点']
  const rows: Array<Record<string, string>> = [
    { 序号: '10', 题目: '完整题', 题型: '单选题', 选项A: '甲', 选项B: '乙', 答案: 'A', 一级知识点: 'KC1' },
    { 序号: '20', 题目: '待补答案', 题型: '数值题', 答案: '', 一级知识点: 'KC1' },
    { 序号: '30', 题目: '待补知识点', 题型: '简答题', 答案: '说明' },
    { 序号: '40', 题目: '无法识别题型', 题型: '未知类型', 答案: '说明', 一级知识点: 'KC1' },
  ]
  const csv = '\ufeff' + [columns.join(','), ...rows.map((row) => columns.map((column) => `"${(row[column] || '').replaceAll('"', '""')}"`).join(','))].join('\n')
  const previewResponse = page.waitForResponse((response) => response.url().endsWith(`${apiBase}imports/questions/preview/`) && response.status() === 200)
  await page.getByLabel('上传题库文件').setInputFiles({ name: 'readiness.csv', mimeType: 'text/csv', buffer: Buffer.from(csv) })
  const preview = await (await previewResponse).json()
  expect(preview.preview.summary).toEqual({ ready: 1, draft: 2, duplicate: 0, invalid: 1 })
  await expect(page.locator('[data-confirm-import]')).toContainText('3 道题')
  await page.screenshot({ path: testInfo.outputPath('import-classification.png'), fullPage: true })
  const importResponse = page.waitForResponse((response) => response.url().endsWith(`${apiBase}imports/questions/confirm/`) && response.status() === 201)
  await page.locator('[data-confirm-import]').click()
  const imported = await (await importResponse).json()
  expect([imported.created, imported.active, imported.draft, imported.failed]).toEqual([3, 1, 2, 1])
  await page.locator('[data-return-detail]').click()
  await expect(page).toHaveURL(new RegExp(`/teacher/courses/1/banks/${bank.id}/$`))
  let questions = (await (await page.request.get(`${apiBase}questions/`)).json()).items
  const complete = questions.find((item: { text: string }) => item.text === '完整题')
  const numeric = questions.find((item: { text: string }) => item.text === '待补答案')
  expect(complete.status).toBe('active')
  expect(complete.stable_code).toBeTruthy()
  expect(complete.course_sequence).toBeGreaterThan(0)
  await expect(page.locator('.kt-bank-question-section th')).toHaveText(['题库内序号', '题目', '题型', '知识点结构', '难度', '状态', '操作'])
  await expect(page.locator('.kt-bank-question-section')).not.toContainText(complete.stable_code)
  expect(complete.teacher_difficulty).toBeNull()
  expect(numeric.status).toBe('draft')
  expect(numeric.draft_reasons.length).toBeGreaterThan(0)
  await page.goto(numeric.edit_url, { waitUntil: 'commit' })
  await expect(page.getByRole('heading', { name: `编辑第 ${numeric.sequence} 题`, exact: true })).toBeVisible()
  const toggle = page.locator('[data-answer-toggle]')
  if (await toggle.getAttribute('aria-expanded') === 'false') await toggle.click()
  await page.getByLabel('正确答案', { exact: true }).fill('0')
  await page.getByRole('button', { name: '保存题目', exact: true }).click()
  await expect(page).toHaveURL(new RegExp(`/teacher/courses/1/banks/${bank.id}/$`))
  questions = (await (await page.request.get(`${apiBase}questions/`)).json()).items
  expect(questions.find((item: { id: number }) => item.id === numeric.id).status).toBe('active')

  // Reproduce pre-fix persisted state only inside this test's isolated bank.
  const repository = join(import.meta.dirname, '..', '..')
  const localPython = join(repository, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
  const python = process.env.PYTHON || (existsSync(localPython) ? localPython : 'python')
  const seeded = spawnSync(python, ['manage.py', 'shell', '-c', `import os; from apps.question_bank.models import QuestionBank, Question; bank=QuestionBank.objects.get(pk=int(os.environ['READINESS_BANK'])); assert bank.name.startswith('UAT-READINESS-'); assert bank.questions.count()==3; Question.objects.filter(bank=bank,pk=int(os.environ['READINESS_COMPLETE'])).update(status='draft'); Question.objects.filter(bank=bank,pk=int(os.environ['READINESS_NUMERIC'])).update(status='archived')`], {
    cwd: repository, encoding: 'utf8',
    env: { ...process.env, ...requireIsolatedPostgres(process.env), READINESS_BANK: String(bank.id), READINESS_COMPLETE: String(complete.id), READINESS_NUMERIC: String(numeric.id) },
  })
  if (seeded.error) throw seeded.error
  expect(seeded.status, seeded.stderr || seeded.stdout || `signal=${seeded.signal}`).toBe(0)
  await page.reload({ waitUntil: 'commit' })
  const recheckResponse = page.waitForResponse((response) => response.url().endsWith(`${apiBase}questions/recheck/`) && response.status() === 200)
  await page.locator('[data-recheck-drafts]').click()
  const rechecked = await (await recheckResponse).json()
  expect([rechecked.checked, rechecked.activated, rechecked.draft]).toEqual([2, 1, 1])
  questions = (await (await page.request.get(`${apiBase}questions/`)).json()).items
  expect(questions.find((item: { id: number }) => item.id === complete.id).status).toBe('active')
  expect(questions.find((item: { id: number }) => item.id === numeric.id).status).toBe('archived')
  await expect(page.getByRole('heading', { name: '题目清单', exact: true })).toBeVisible()
  await expect(page.locator('.kt-alert--success')).toContainText('检查 2 道，转为可用 1 道，仍为草稿 1 道')
  for (const [name, width, height] of [['desktop', 1440, 900], ['mobile', 390, 844]] as const) {
    await page.setViewportSize({ width, height })
    await page.screenshot({ path: testInfo.outputPath(`rechecked-bank-${name}.png`), fullPage: true })
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width + 1)
  }
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.evaluate(() => { document.documentElement.style.zoom = '2' })
  await expect(page.locator('[data-recheck-drafts]')).toBeVisible()
  await page.screenshot({ path: testInfo.outputPath('rechecked-bank-zoom200.png'), fullPage: true })
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(1441)
  await page.evaluate(() => { document.documentElement.style.zoom = '' })
  const experimentResponse = await page.request.post('/api/v1/teacher/courses/1/experiments/', {
    headers, data: { name: `UAT-READINESS-PUBLISH-${bank.id}`, question_bank_id: bank.id, sampling: { mode: 'total_count', total_count: 1, match_scope: 'primary', nodes: [{ kc_code: 'KC1', quota: null, match_scope: 'primary' }] }, ordering_mode: 'fixed', feedback_mode: 'none', time_mode: 'none', resume_mode: 'resumable', confidence_mode: 'off' },
  })
  expect(experimentResponse.status()).toBe(201)
  const experiment = await experimentResponse.json()
  const readiness = await (await page.request.get(`/api/v1/teacher/courses/1/experiments/${experiment.id}/readiness/`)).json()
  expect(readiness.ready).toBe(true)
  expect(readiness.candidate_count).toBe(1)
  await page.goto(`/teacher/courses/1/experiments/${experiment.id}/`, { waitUntil: 'commit' })
  await page.getByRole('button', { name: '发布到班级', exact: true }).click()
  await page.getByRole('checkbox', { name: '选择 认知追踪实验班', exact: true }).check()
  await page.getByRole('button', { name: '运行发布前检查', exact: true }).click()
  await page.getByRole('button', { name: '进入发布确认', exact: true }).click()
  await page.getByRole('button', { name: '确认发布', exact: true }).click()
  await expect(page.getByRole('heading', { name: '已发布班级任务', exact: true })).toBeVisible({ timeout: 30_000 })
})
