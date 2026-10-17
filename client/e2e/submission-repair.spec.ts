import { requestWholePaperSubmission } from './whole-paper'
import { expect, test } from '@playwright/test'
import { spawnSync } from 'node:child_process'
import { requireServerPython, serverDir } from '../scripts/e2e-runtime.mjs'
import { connect, createServer, type Server, type Socket } from 'node:net'
import { requireIsolatedPostgres } from '../scripts/e2e-database.mjs'
import { openLoginPage } from './login-navigation'
import { acceptResearchConsentIfRequired } from './research-consent'
import type { StudentSessionEntry } from '../src/features/student-courses/api'

const root = serverDir
const python = requireServerPython()
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

test('initial-password reminder, inline confidence and lost whole-paper receipt preserve one final paper', async ({ page, browser, browserName }, testInfo) => {
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
  await acceptResearchConsentIfRequired(page)
  await expect(page.getByRole('heading', { name: '建议修改初始密码' })).toBeVisible()
  await page.getByRole('button', { name: '暂不修改', exact: true }).click()
  expect((await page.request.get('/api/v1/student/courses/')).ok()).toBe(true)
  expect((await (await page.request.get('/api/v1/auth/session/')).json()).data.user.initial_password_reminder_pending).toBe(true)
  await page.unroute(reminderUrl)
  await page.reload()
  await expect(page.getByRole('heading', { name: '建议修改初始密码' })).toBeVisible()
  await expect.poll(async () => (await (await page.request.get('/api/v1/auth/session/')).json()).data.user.initial_password_reminder_pending).toBe(false)
  await page.getByRole('button', { name: '暂不修改', exact: true }).click()
  await page.reload()
  await expect(page.getByRole('heading', { name: '建议修改初始密码' })).toHaveCount(0)
  await expect(page.getByRole('heading', { name: '我的课程', exact: true })).toBeVisible()

  await page.goto('/password/change/')
  await page.getByLabel('当前密码', { exact: true }).fill('incorrect-current-password')
  await page.getByLabel('新密码', { exact: true }).fill('Personal-password-2026')
  await page.getByLabel('再次输入新密码', { exact: true }).fill('Personal-password-2026')
  await page.getByRole('button', { name: '保存新密码', exact: true }).click()
  await expect(page.getByRole('alert')).toBeVisible()
  expect((await (await page.request.get('/api/v1/auth/session/')).json()).data.user.uses_initial_password).toBe(true)
  await page.getByLabel('当前密码', { exact: true }).fill(`S${username}`)
  await page.getByRole('button', { name: '保存新密码', exact: true }).click()
  await expect(page).toHaveURL(/\/student\/$/, { timeout: 15000 })
  await expect(page.getByRole('heading', { name: '我的课程', exact: true })).toBeVisible()
  expect((await (await page.request.get('/api/v1/auth/session/')).json()).data.user.uses_initial_password).toBe(false)

  const startReceipts: StudentSessionEntry[] = []
  await page.route('**/api/v1/student/tasks/*/start/', async route => {
    const response = await route.fetch()
    expect(response.ok()).toBe(true)
    startReceipts.push((await response.json()).data)
    if (startReceipts.length === 1) await route.abort()
    else await route.fulfill({ response })
  })
  await page.goto(`/student/courses/${fixture.course.id}/`)
  await page.getByRole('button', { name: '开始作业', exact: true }).click()
  await expect(page.locator('[name=answer]').first()).toBeEnabled()
  await page.unroute('**/api/v1/student/tasks/*/start/')
  const sessionId = Number(page.url().match(/sessions\/(\d+)/)![1])
  expect(startReceipts).toHaveLength(2)
  const initialRuntime = startReceipts[0]!.runtime!
  expect(initialRuntime.entry_receipt).toBeDefined()
  expect(initialRuntime.initial_writer_lease).toBeDefined()
  expect(startReceipts[1]).toMatchObject({ session_id: sessionId, runtime: {
    entry_receipt: initialRuntime.entry_receipt,
    initial_writer_lease: {
      writer_lease_token: initialRuntime.initial_writer_lease!.writer_lease_token,
      writer_client_instance_id: initialRuntime.initial_writer_lease!.writer_client_instance_id,
    },
  } })
  expect(startReceipts[0]!.session_id).toBe(sessionId)
  const rows: Array<{ id: number; kind: string }> = django(['shell', '-c',
    `import json; from apps.assessment.models import Attempt; print(json.dumps([{'id':a.assignment_id,'kind':a.assignment.snapshot.question_type} for a in Attempt.objects.filter(session_id=${sessionId}).select_related('assignment__snapshot')]))`])
  const single = rows.find(row => row.kind === 'single_choice')!
  await page.goto(`/student/sessions/${sessionId}/questions/${single.id}/`)
  const radios = page.getByRole('radio')
  await expect(radios.first()).toBeEnabled()
  await radios.nth(0).check()
  const confidence = page.locator('[data-inline-confidence]')
  await expect(confidence).toHaveValue('')
  await confidence.fill('98')
  await expect(page.locator('[data-save-status]')).toHaveText('草稿已保存')
  await radios.nth(1).check()
  await expect(confidence).toHaveValue('')
  for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }, { width: 720, height: 450 }]) {
    await page.setViewportSize(viewport)
    await page.screenshot({ path: testInfo.outputPath(`confidence-${viewport.width}.png`), fullPage: true })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
  }
  await page.setViewportSize({ width: 1440, height: 900 })
  await confidence.fill('98')
  await expect(page.locator('[data-save-status]')).toHaveText('草稿已保存')
  expect(django(['shell', '-c', `import json; from apps.assessment.models import SubmissionEvent; print(json.dumps(SubmissionEvent.objects.filter(attempt__session_id=${sessionId}).count()))`])).toBe(0)

  const short = rows.find(row => row.kind === 'short_answer')!
  const shortLink = page.locator(`[data-nav-assignment="${short.id}"]`)
  await expect(shortLink).toHaveAttribute('href', `/student/sessions/${sessionId}/questions/${short.id}/`)
  await shortLink.click()
  await expect(page).toHaveURL(new RegExp(`/questions/${short.id}/$`))
  const input = page.locator('[name=answer]').first()
  await expect(input).toBeEnabled()
  await input.fill('第一版')
  await expect(page.locator('[data-save-status]')).toHaveText('草稿已保存')
  expect(await page.evaluate(async ({ sessionId, assignmentId }) => {
    const identities = await caches.open('kt-capture-v2-runtime-3-identity')
    const identityResponse = await identities.match('/assessment/process/capture-v2/offline-identity')
    if (!identityResponse) throw new Error('Authorized offline identity is missing')
    const identity = await identityResponse.json() as { userId: number; sessionId: number; buildVersion: string }
    const cache = await caches.open(`kt-capture-v2-runtime-3-question-${identity.userId}-${sessionId}-${encodeURIComponent(identity.buildVersion)}`)
    const envelopeResponse = await cache.match(`/assessment/process/capture-v2/offline-envelope${location.pathname}`)
    const envelope = envelopeResponse ? await envelopeResponse.json() as { shell: string; bootstrap: { assessment_session_id: number; assignment_id: number } } : undefined
    const assets = await caches.open(`kt-capture-v2-runtime-3-assets-${encodeURIComponent(identity.buildVersion)}`)
    return {
      controlled: Boolean(navigator.serviceWorker.controller), identityMatches: identity.sessionId === sessionId,
      envelopeMatches: envelope?.bootstrap.assessment_session_id === sessionId && envelope.bootstrap.assignment_id === assignmentId,
      shellCached: Boolean(envelope && await assets.match(envelope.shell)),
      questionCached: Boolean(await cache.match(`/api/v1/student/sessions/${sessionId}/questions/${assignmentId}/`)),
    }
  }, { sessionId, assignmentId: short.id })).toEqual({ controlled: true, identityMatches: true, envelopeMatches: true, shellCached: true, questionCached: true })
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
  const submittedTokens: string[] = []
  await page.route('**/student/sessions/*/finish/', async route => {
    submittedTokens.push(route.request().postDataJSON().submission_token)
    const response = await route.fetch()
    expect(response.status(), await response.text()).toBe(202)
    await route.abort()
  })
  const confirmation = await requestWholePaperSubmission(page)
  await confirmation.locator('[data-paper-submit-confirm]').click()
  await expect(page.locator('[data-paper-submission-retry]')).toBeVisible()
  await expect(input).toBeDisabled()
  await page.unroute('**/student/sessions/*/finish/')
  page.on('request', request => {
    if (/\/student\/sessions\/\d+\/finish\/$/.test(new URL(request.url()).pathname)) submittedTokens.push(request.postDataJSON().submission_token)
  })
  await page.reload()
  await expect(page).toHaveURL(new RegExp(`/sessions/${sessionId}/complete/$`), { timeout: 60_000 })
  const evidence = django(['shell', '-c', `import json; from apps.assessment.models import SubmissionEvent,ConfidenceAssignment,ExperimentSession; from apps.assessment.session_submission_models import SessionSubmission; print(json.dumps({'submissions':SubmissionEvent.objects.filter(attempt__session_id=${sessionId}).count(),'skip':ConfidenceAssignment.objects.get(attempt__session_id=${sessionId},attempt__assignment_id=${short.id}).response_status,'single_confidence':ConfidenceAssignment.objects.get(attempt__session_id=${sessionId},attempt__assignment_id=${single.id}).probability,'sessions':ExperimentSession.objects.filter(student__username='${username}').count(),'receipts':SessionSubmission.objects.filter(session_id=${sessionId}).count()}))`])
  expect(evidence).toEqual({ submissions: 2, skip: 'skipped', single_confidence: 98, sessions: 1, receipts: 1 })
  expect(submittedTokens.length).toBeGreaterThanOrEqual(2)
  expect(new Set(submittedTokens).size).toBe(1)
  expect(errors).toEqual([])
  const teacherContext = await browser.newContext({ baseURL: 'http://127.0.0.1:8127' })
  try {
    const teacher = await teacherContext.newPage()
    await openLoginPage(teacher, 'http://127.0.0.1:8127')
    await teacher.getByLabel('账号或学号').fill('teacher')
    await teacher.getByLabel('密码', { exact: true }).fill('teacher123')
    await teacher.getByRole('button', { name: '登录', exact: true }).click()
    await expect(teacher).toHaveURL(/\/teacher\/courses\/$/)
    const task = fixture.tasks[0]
    await teacher.goto(`/teacher/courses/${fixture.course.id}/data/advanced/?experiment_id=${task.experiment_id}&class_id=${task.class_group_id}&table=responses&student_id=${fixture.student.id}`)
    await expect(teacher.getByRole('heading', { name: '研究数据预览', exact: true })).toBeVisible()
    for (const key of ['sessions', 'questions', 'knowledge_points', 'question_kc_mapping', 'responses', 'behavior_events']) {
      await expect(teacher.locator(`[data-card-key="${key}"]`)).toBeVisible()
    }
    await expect(teacher.getByLabel('按学生姓名或学号搜索')).toHaveValue(new RegExp(username))
    const readablePreview = teacher.getByRole('table')
    await expect(readablePreview.locator('tbody tr')).toHaveCount(rows.length)
    await expect(readablePreview.locator('tbody tr').filter({ hasText: '已提交' })).toHaveCount(2)
    await expect(readablePreview).toContainText('离线保留的答案')
    await expect(readablePreview).not.toContainText('attempt_id')
    await teacher.evaluate(() => scrollTo(0, 0))
    await teacher.screenshot({ path: testInfo.outputPath('advanced-record-desktop-1440.png'), fullPage: true })
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
