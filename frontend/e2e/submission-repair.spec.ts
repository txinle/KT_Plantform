import { expect, test } from '@playwright/test'
import { spawnSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { join } from 'node:path'
import { connect, createServer, type Server, type Socket } from 'node:net'
import { requireIsolatedPostgres } from '../scripts/e2e-database.mjs'

const root = join(import.meta.dirname, '../..')
const localPython = join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
const python = process.env.PYTHON || (existsSync(localPython) ? localPython : 'python')
let proxy: Server | undefined
const sockets = new Set<Socket>()
async function startNetwork() {
  proxy = createServer(client => {
    const upstream = connect({ host: '127.0.0.1', port: 8126 })
    for (const socket of [client, upstream]) {
      sockets.add(socket)
      socket.once('close', () => sockets.delete(socket))
    }
    client.once('error', () => upstream.destroy())
    upstream.once('error', () => client.destroy())
    client.pipe(upstream).pipe(client)
  })
  await new Promise<void>((resolve, reject) => { proxy!.once('error', reject); proxy!.listen(8127, '127.0.0.1', resolve) })
}
async function stopNetwork() {
  for (const socket of sockets) socket.destroy()
  sockets.clear()
  if (proxy) await new Promise<void>(resolve => proxy!.close(() => resolve()))
  proxy = undefined
}
test.use({ baseURL: 'http://127.0.0.1:8127' })
test.beforeEach(startNetwork)
test.afterEach(stopNetwork)
function django(args: string[]) {
  const result = spawnSync(python, ['manage.py', ...args], { cwd: root, encoding: 'utf8',
    env: { ...process.env, ...requireIsolatedPostgres(process.env), PYTHONUTF8: '1' } })
  if (result.error) throw result.error
  if (result.status !== 0) throw new Error(result.stderr || result.stdout)
  return JSON.parse(result.stdout.trim().split('\n').at(-1)!)
}

test('initial-password reminder, pre-submit confidence and response-loss recovery preserve one formal answer', async ({ page, browser, browserName }, testInfo) => {
  test.setTimeout(240_000)
  const runKey = `repair-${browserName}-${Date.now()}`
  const fixture = django(['seed_e2e_submission_repair', '--run-key', runKey])
  const username = `CI-${runKey}`
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  const reminderUrl = '**/api/v1/auth/password/reminder-shown/'
  await page.route(reminderUrl, route => route.abort())
  await page.goto('/login/')
  await page.getByLabel('账号或学号').fill(username)
  await page.getByLabel('密码', { exact: true }).fill(`S${username}`)
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.getByRole('heading', { name: '建议修改初始密码' })).toBeVisible()
  await page.getByRole('button', { name: '暂不修改', exact: true }).click()
  expect((await page.request.get('/api/v1/student/courses/')).ok()).toBe(true)
  expect((await (await page.request.get('/api/v1/auth/session/')).json()).user.initial_password_reminder_pending).toBe(true)
  await page.unroute(reminderUrl)
  await page.reload()
  await expect(page.getByRole('heading', { name: '建议修改初始密码' })).toBeVisible()
  await expect.poll(async () => (await (await page.request.get('/api/v1/auth/session/')).json()).user.initial_password_reminder_pending).toBe(false)
  await page.getByRole('button', { name: '暂不修改', exact: true }).click()
  await page.reload()
  await expect(page.getByRole('heading', { name: '建议修改初始密码' })).toHaveCount(0)

  await page.goto('/password/change/')
  await page.getByLabel('当前密码', { exact: true }).fill('incorrect-current-password')
  await page.getByLabel('新密码', { exact: true }).fill('Personal-password-2026')
  await page.getByLabel('再次输入新密码', { exact: true }).fill('Personal-password-2026')
  await page.getByRole('button', { name: '保存新密码', exact: true }).click()
  await expect(page.getByRole('alert')).toBeVisible()
  expect((await (await page.request.get('/api/v1/auth/session/')).json()).user.uses_initial_password).toBe(true)
  await page.getByLabel('当前密码', { exact: true }).fill(`S${username}`)
  await page.getByRole('button', { name: '保存新密码', exact: true }).click()
  await expect(page).toHaveURL(/\/student\/$/, { timeout: 15000 })
  expect((await (await page.request.get('/api/v1/auth/session/')).json()).user.uses_initial_password).toBe(false)

  await page.route('**/api/v1/student/tasks/*/start/', async route => {
    const response = await route.fetch()
    expect(response.ok()).toBe(true)
    await route.abort()
  })
  await page.goto(`/student/courses/${fixture.course.id}/`)
  await page.getByRole('button', { name: '开始作业', exact: true }).click()
  await expect(page.locator('[name=answer]').first()).toBeEnabled()
  await page.unroute('**/api/v1/student/tasks/*/start/')
  const sessionId = Number(page.url().match(/sessions\/(\d+)/)![1])
  const rows: Array<{ id: number; kind: string }> = django(['shell', '-c',
    `import json; from apps.assessment.models import Attempt; print(json.dumps([{'id':a.assignment_id,'kind':a.assignment.snapshot.question_type} for a in Attempt.objects.filter(session_id=${sessionId}).select_related('assignment__snapshot')]))`])
  const single = rows.find(row => row.kind === 'single_choice')!
  await page.goto(`/student/sessions/${sessionId}/questions/${single.id}/`)
  const radios = page.getByRole('radio')
  await expect(radios.first()).toBeEnabled()
  await radios.nth(0).check()
  await page.locator('[data-submit-request]').click()
  await expect(page.locator('[data-confidence-panel]')).toBeVisible()
  await expect(page.locator('[data-confidence-submit]')).toBeDisabled()
  await page.locator('[data-confidence-input]').press('End')
  await page.locator('[data-confidence-input]').press('ArrowLeft')
  await page.locator('[data-confidence-input]').press('ArrowLeft')
  await page.locator('[data-confidence-submit]').click()
  await expect(page.getByRole('dialog')).toContainText('80%')
  await page.locator('[data-return-answer]').click()
  await radios.nth(1).check()
  await page.locator('[data-submit-request]').click()
  await expect(page.locator('[data-confidence-submit]')).toBeDisabled()
  for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }, { width: 720, height: 450 }]) {
    await page.setViewportSize(viewport)
    await page.screenshot({ path: testInfo.outputPath(`confidence-${viewport.width}.png`), fullPage: true })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
  }
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.locator('[data-confidence-input]').press('End')
  await page.locator('[data-confidence-input]').press('ArrowLeft')
  await page.locator('[data-confidence-input]').press('ArrowLeft')
  await page.locator('[data-confidence-submit]').click()
  expect(django(['shell', '-c', `import json; from apps.assessment.models import SubmissionEvent; print(json.dumps(SubmissionEvent.objects.filter(attempt__session_id=${sessionId}).count()))`])).toBe(0)
  let submitRequests = 0
  await page.route('**/student/attempts/*/submit/', async route => {
    submitRequests += 1
    const response = await route.fetch()
    expect(response.ok(), await response.text()).toBe(true)
    await route.abort()
  })
  await page.locator('[data-submit-confirm]').click()
  await expect(page).not.toHaveURL(new RegExp(`/questions/${single.id}/$`))
  expect(submitRequests).toBe(1)
  const formal = django(['shell', '-c', `import json; from apps.assessment.models import Attempt,SubmissionEvent,ConfidenceAssignment; a=Attempt.objects.get(session_id=${sessionId},assignment_id=${single.id}); c=ConfidenceAssignment.objects.get(attempt=a); print(json.dumps({'attempt_id':a.pk,'count':SubmissionEvent.objects.filter(attempt=a).count(),'status':a.status,'confidence':c.probability,'head':str(a.final_response_event_id)},ensure_ascii=False))`])
  expect(formal).toMatchObject({ count: 1, status: 'submitted', confidence: 80 })
  expect(formal.head).not.toBe('None')
  await page.unroute('**/student/attempts/*/submit/')

  const short = rows.find(row => row.kind === 'short_answer')!
  await page.goto(`/student/sessions/${sessionId}/questions/${short.id}/`)
  const input = page.locator('[name=answer]').first()
  await expect(input).toBeEnabled()
  await input.fill('第一版')
  await expect(page.locator('[data-save-status]')).toHaveText('草稿已保存')
  expect(await page.evaluate(async () => ({ controlled: Boolean(navigator.serviceWorker.controller), cached: Boolean(await caches.match(location.href)) }))).toEqual({ controlled: true, cached: true })
  await stopNetwork()
  expect(await page.evaluate(() => fetch('/api/v1/auth/csrf/?network-probe=1', { cache: 'no-store' }).then(() => false, () => true))).toBe(true)
  await page.evaluate(() => dispatchEvent(new Event('offline')))
  await input.fill('离线保留的答案')
  await expect(page.locator('[data-save-status]')).toHaveText('已保存到本机，等待联网同步')
  const previousDocument = await page.evaluate(() => performance.timeOrigin)
  await page.evaluate(() => { setTimeout(() => location.reload(), 0) })
  await expect.poll(() => page.evaluate(() => performance.timeOrigin).catch(() => previousDocument)).not.toBe(previousDocument)
  await expect(page.locator('#kt-offline-bootstrap')).toHaveCount(1)
  await expect(input).toHaveValue('离线保留的答案', { timeout: 20000 })
  await startNetwork()
  await page.evaluate(() => dispatchEvent(new Event('online')))
  await expect(page.locator('[data-save-status]')).toHaveText('草稿已保存', { timeout: 45000 })
  await page.route('**/student/attempts/*/submit/', async route => {
    submitRequests += 1
    const response = await route.fetch()
    expect(response.ok(), await response.text()).toBe(true)
    await route.abort()
  })
  await page.locator('[data-submit-request]').click()
  await page.getByRole('button', { name: '跳过评价', exact: true }).click()
  await page.route('**/recovery/**', route => route.abort())
  await page.locator('[data-submit-confirm]').click()
  await expect(page.locator('[data-recover-submit]')).toBeVisible()
  await page.unroute('**/recovery/**')
  await page.reload()
  await expect(page).not.toHaveURL(new RegExp(`/questions/${short.id}/$`))
  const evidence = django(['shell', '-c', `import json; from apps.assessment.models import SubmissionEvent,ConfidenceAssignment,ExperimentSession; print(json.dumps({'submissions':SubmissionEvent.objects.filter(attempt__session_id=${sessionId}).count(),'skip':ConfidenceAssignment.objects.get(attempt__session_id=${sessionId},attempt__assignment_id=${short.id}).response_status,'sessions':ExperimentSession.objects.filter(student__username='${username}').count()}))`])
  expect(evidence).toEqual({ submissions: 2, skip: 'skipped', sessions: 1 })
  expect(submitRequests).toBe(2)
  expect(errors).toEqual([])
  const teacherContext = await browser.newContext({ baseURL: 'http://127.0.0.1:8127' })
  try {
    const teacher = await teacherContext.newPage()
    await teacher.goto('/login/')
    await teacher.getByLabel('账号或学号').fill('teacher')
    await teacher.getByLabel('密码', { exact: true }).fill('teacher123')
    await teacher.getByRole('button', { name: '登录', exact: true }).click()
    await expect(teacher).toHaveURL(/\/teacher\/courses\/$/)
    const task = fixture.tasks[0]
    await teacher.goto(`/teacher/courses/${fixture.course.id}/data/advanced/?experiment_id=${task.experiment_id}&class_id=${task.class_group_id}&table=attempts&search=${username}`)
    await expect(teacher.getByRole('heading', { name: '高级数据工具', exact: true })).toBeVisible()
    for (const group of ['实验与参与者', '题目与知识结构', '作答与评分', '行为过程', '扩展观测', '派生研究数据']) {
      await expect(teacher.getByText(group, { exact: true })).toBeVisible()
    }
    await expect(teacher.getByLabel('按学生姓名或学号搜索')).toHaveValue(username)
    const readablePreview = teacher.getByTestId('readable-preview')
    await expect(readablePreview).toContainText(username)
    await expect(readablePreview).not.toContainText('attempt_id')
    await readablePreview.getByRole('button', { name: '查看完整记录', exact: true }).first().click()
    const fullRecord = teacher.getByRole('dialog', { name: '完整记录' })
    await expect(fullRecord).toContainText('作答记录唯一标识')
    await expect(fullRecord).toContainText(String(formal.attempt_id))
    await teacher.evaluate(() => scrollTo(0, 0))
    await teacher.screenshot({ path: testInfo.outputPath('advanced-record-desktop-1440.png'), fullPage: true })
    await teacher.getByRole('button', { name: '关闭完整记录', exact: true }).click()
    for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
      await teacher.setViewportSize(viewport)
      await teacher.evaluate(() => scrollTo(0, 0))
      await teacher.screenshot({ path: testInfo.outputPath(`advanced-preview-${viewport.width}.png`), fullPage: true })
      expect(await teacher.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
    }
    await teacher.goto(`/teacher/courses/${fixture.course.id}/classes/${task.class_group_id}/students/${fixture.student.id}/experiments/${task.experiment_id}/`)
    await expect(teacher.getByRole('heading', { name: '逐题事实', exact: true })).toBeVisible()
    await expect(teacher.locator('tbody tr')).toHaveCount(2)
    await teacher.getByRole('link', { name: '查看作答过程', exact: true }).first().click()
    await expect(teacher.locator('.kt-process-node').first()).toBeVisible()
    await expect(teacher.locator('.kt-process-timeline')).toContainText('正式提交')
    const visible = await teacher.locator('main').innerText()
    for (const key of ['session_id', 'attempt_id', 'snapshot_id', 'event_id', 'submission_id', 'state_hash', 'Schema', 'response_transition']) expect(visible).not.toContain(key)
    for (const viewport of [{ width: 1777, height: 885 }, { width: 1440, height: 900 }, { width: 390, height: 844 }]) {
      await teacher.setViewportSize(viewport)
      await teacher.screenshot({ path: testInfo.outputPath(`process-${viewport.width}.png`), fullPage: true })
      expect(await teacher.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
    }
    await teacher.setViewportSize({ width: 1440, height: 900 })
    await teacher.evaluate(() => { document.documentElement.style.zoom = '2' })
    await teacher.screenshot({ path: testInfo.outputPath('process-zoom200.png'), fullPage: true })
    expect(await teacher.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
    await teacher.keyboard.press('Tab')
    expect(await teacher.evaluate(() => document.activeElement?.tagName !== 'BODY')).toBe(true)
  } finally { await teacherContext.close() }
})
