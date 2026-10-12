import { expect, test } from '@playwright/test'
import { spawnSync } from 'node:child_process'
import { writeFileSync } from 'node:fs'
import { requireServerPython, serverDir } from '../scripts/e2e-runtime.mjs'
import { requireIsolatedPostgres } from '../scripts/e2e-database.mjs'
import { acceptResearchConsentIfRequired } from './research-consent'
import { captureProtocol, digest, projectPayload } from './capture-protocol-witness.mjs'

const witnesses = new WeakMap<object, Awaited<ReturnType<typeof captureProtocol>>>()
test.afterEach(async ({ page }, testInfo) => {
  const witness = witnesses.get(page)
  if (!witness) return
  await witness.settle()
  const path = testInfo.outputPath('capture-protocol-witness.json')
  writeFileSync(path, JSON.stringify({ status: testInfo.status, requests: witness.requests, client_acknowledgements: witness.clientAcknowledgements, audit: witness.audit }, null, 2), { flag: 'wx' })
  await testInfo.attach('capture-protocol-witness', { path, contentType: 'application/json' })
})

const root = serverDir
const python = requireServerPython()
function django(args: string[]) {
  const result = spawnSync(python, ['manage.py', ...args], { cwd: root, encoding: 'utf8',
    env: { ...process.env, ...requireIsolatedPostgres(process.env), PYTHONUTF8: '1' } })
  if (result.error) throw result.error
  if (result.status !== 0) throw new Error(result.stderr || result.stdout)
  return JSON.parse(result.stdout.trim().split('\n').at(-1)!)
}

test('five short-response types retain trajectories through offline reload, IME and formal feedback', async ({ browserName, page, context }, testInfo) => {
  test.setTimeout(240_000)
  test.skip(browserName !== 'chromium', 'Chromium provides the CDP IME controls used by this capture journey.')
  const run = process.env.E2E_CAPTURE_RUN_ID
  if (run && !/^[a-z0-9-]{1,24}$/.test(run)) throw new Error('Invalid capture fixture run identifier')
  const fixture = run ? django(['shell', '-c', `from apps.accounts.management.commands.seed_e2e_capture_v2 import Command; command=Command(); command.course_term+='-${run}'; command.handle()`]) : django(['seed_e2e_capture_v2'])
  const witness = await captureProtocol(page)
  witnesses.set(page, witness)
  const pageErrors: string[] = []
  page.on('pageerror', (error) => pageErrors.push(error.message))
  await page.goto('/login/')
  await page.getByLabel('账号或学号').fill('S002')
  await page.getByLabel('密码').fill('student123')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL(/\/student\/$/)
  await acceptResearchConsentIfRequired(page)
  await page.goto(`/student/courses/${fixture.course.id}/`)
  await page.getByRole('article').filter({ has: page.getByRole('heading', { name: 'E2E Capture V2 五题回放', exact: true }) })
    .getByRole('button', { name: '开始作业' }).click()
  await expect(page.locator('[name=answer]').first()).toBeEnabled()
  const sessionId = Number(page.url().match(/sessions\/(\d+)/)![1])
  const rows: Array<{ assignment_id: number; kind: string }> = django(['shell', '-c',
    `import json; from apps.assessment.models import Attempt; print(json.dumps([{'assignment_id':a.assignment_id,'kind':a.assignment.snapshot.question_type} for a in Attempt.objects.filter(session_id=${sessionId}).select_related('assignment__snapshot').order_by('assignment__assigned_sequence_index')]))`])
  expect(new Set(rows.map((row) => row.kind)).size).toBe(5)
  for (const row of rows) {
    await page.goto(`/student/sessions/${sessionId}/questions/${row.assignment_id}/`)
    const input = page.locator('[name=answer]').first()
    await expect(input).toBeEnabled()
    if (row.kind === 'single_choice' || row.kind === 'true_false') {
      const choices = page.getByRole('radio')
      await choices.nth(0).check(); await choices.nth(1).check(); await choices.nth(0).check()
      await page.getByRole('button', { name: '清除选择', exact: true }).click()
      await choices.nth(0).check()
    } else if (row.kind === 'multiple_choice') {
      const choices = page.getByRole('checkbox')
      await choices.nth(0).check(); await choices.nth(1).check()
      await choices.nth(0).uncheck(); await choices.nth(1).uncheck(); await choices.nth(0).check()
    } else {
      await input.fill('A'); await input.fill('AB'); await input.fill('A'); await input.fill('')
      if (row.kind === 'short_answer') {
        const cdp = await context.newCDPSession(page)
        await input.focus()
        await cdp.send('Input.imeSetComposition', { text: 'zhong', selectionStart: 5, selectionEnd: 5 })
        await cdp.send('Input.insertText', { text: '中文' })
        await expect(input).toHaveValue('中文')
        await page.waitForTimeout(1100)
        await context.setOffline(true)
        await input.fill('离线甲'); await input.fill('离线甲乙')
        await page.waitForTimeout(950)
        await page.reload()
        await expect(input).toBeEnabled()
        await expect(input).toHaveValue('离线甲乙')
        await input.fill('离线刷新后')
        await page.waitForTimeout(950)
        await context.setOffline(false)
        await expect(page.getByText('草稿已保存', { exact: true })).toBeVisible({ timeout: 45_000 })
      } else await input.fill('填空答案')
    }
    if (row.kind === 'single_choice') {
      await page.getByRole('button', { name: '标记疑点', exact: true }).click()
      await page.getByRole('button', { name: '待检查', exact: true }).click()
      const rule = page.locator('[aria-label="题目工具"] .kt-alert').filter({ hasText: '本题要求完成' })
      await rule.getByRole('button', { name: '开始检查', exact: true }).click()
      await rule.getByRole('button', { name: '完成检查', exact: true }).click()
    }
    if (row.kind === 'fill_blank') {
      await expect(page.getByRole('button', { name: '查看第 1 级提示', exact: true })).toBeVisible()
      await page.getByRole('button', { name: '查看第 1 级提示', exact: true }).click()
      await page.getByRole('button', { name: '关闭提示', exact: true }).click()
      await expect(page.getByRole('alert')).toHaveCount(0)
    }
    for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
      await page.setViewportSize(viewport)
      await page.evaluate(() => window.scrollTo(0, 0))
    await page.screenshot({ path: testInfo.outputPath(`${row.kind}-${viewport.width}.png`), fullPage: true })
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
    }
    // Compact layout coverage; this does not exercise actual browser zoom.
    await page.setViewportSize({ width: 720, height: 450 })
    await page.evaluate(() => window.scrollTo(0, 0))
    await page.screenshot({ path: testInfo.outputPath(`${row.kind}-compact720.png`), fullPage: true })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.getByRole('button', { name: '确认提交本题', exact: true }).click()
    if (await page.locator('[data-confidence-panel]').isVisible()) await page.getByRole('button', { name: /跳过评价/ }).click()
    const submitted = page.waitForResponse((response) => response.url().endsWith('/submit/'), { timeout: 15000 })
    await page.getByRole('dialog').getByRole('button', { name: '提交并锁定', exact: true }).click()
    const submitResponse = await submitted
    const body = (await submitResponse.json()).data
    expect(submitResponse.ok(), JSON.stringify(body)).toBe(true)
    if (row !== rows.at(-1)) expect(body).not.toHaveProperty('correct_answer')
    await expect(page).toHaveURL((url) => url.pathname === new URL(body.next_url, page.url()).pathname)
    if (row !== rows.at(-1)) expect(new URL(page.url()).searchParams.get('reason')).toBe('submit_next')
  }
  const single = fixture.question_refs.find((row: { question_type: string }) => row.question_type === 'single_choice')
  const learningResponse = await page.request.get(`/api/v1/student/courses/${fixture.course.id}/learning/experiments/${fixture.tasks[0].experiment_id}/`)
  expect(learningResponse.ok(), await learningResponse.text()).toBe(true)
  const learning = (await learningResponse.json()).data
  const feedbackItem = learning.item_results.find((item: { code: string }) => item.code === single.code)
  expect(feedbackItem.question_id).toBe(single.snapshot_id)
  await page.goto(`/student/courses/${fixture.course.id}/learning/experiments/${fixture.tasks[0].experiment_id}/questions/${feedbackItem.question_id}/`)
  await page.getByRole('button', { name: '展开正确答案', exact: true }).click()
  await page.getByRole('button', { name: '展开解析', exact: true }).click()
  await expect(page.getByRole('complementary', { name: '题目目录', exact: true })).toBeVisible()
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.screenshot({ path: testInfo.outputPath('feedback.png'), fullPage: true })
  for (const viewport of [{ width: 390, height: 844 }, { width: 720, height: 450 }]) {
    await page.setViewportSize(viewport)
    await page.screenshot({ path: testInfo.outputPath(`feedback-${viewport.width}.png`), fullPage: true })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
  }
  await page.getByRole('link', { name: '返回作答详情', exact: true }).click()
  await page.waitForTimeout(1500)
  const evidence = django(['shell', '-c', `import json; from apps.assessment.models import AssessmentEvent,Attempt; events=AssessmentEvent.objects.filter(session_id=${sessionId}); print(json.dumps({'states':list(events.filter(event_type='response_transition').values_list('payload__after_state__value',flat=True)),'feedback':list(events.filter(event_type='feedback_exposure').order_by('server_ingest_sequence_no').values_list('payload__stage',flat=True)),'pending':Attempt.objects.get(session_id=${sessionId},assignment__snapshot__question_type='short_answer').score is None},ensure_ascii=False))`])
  expect(evidence.states).toEqual(expect.arrayContaining(['A', 'AB', '中文', '离线甲', '离线甲乙', '离线刷新后']))
  expect(evidence.states).not.toContain('zhong')
  expect(evidence.feedback).toEqual(['detail_entered', 'answer_presented', 'explanation_presented', 'detail_left'])
  expect(evidence.pending).toBe(true)
  expect(pageErrors).toEqual([])
  await witness.settle()
  const expected = new Map<string, { client_instance_id: string; payload_sha256: string; payload_fields: string[]; event_type: string; client_sequence_no: number; session_id: number; attempt_id: number }>()
  const acknowledged = new Set<string>()
  for (const request of witness.requests) {
    for (const event of request.events) {
      const key = `${request.client_instance_id}:${event.client_sequence_no}`
      const prior = expected.get(key)
      if (prior) expect(event.payload_sha256).toBe(prior.payload_sha256)
      expected.set(key, { ...event, client_instance_id: request.client_instance_id })
    }
    for (const ack of request.acknowledgements || []) {
      const key = `${request.client_instance_id}:${ack.client_sequence_no}`
      expect(expected.has(key)).toBe(true)
      const client = expected.get(key) as { event_id?: string }
      if (client.event_id) expect(ack.event_id).toBe(client.event_id)
      acknowledged.add(key)
    }
  }
  for (const row of witness.clientAcknowledgements) {
    for (const ack of row.acknowledgements) {
      const key = `${row.client_instance_id}:${ack.client_sequence_no}`
      expect(expected.has(key)).toBe(true)
      const client = expected.get(key) as { event_id?: string }
      if (client.event_id) expect(ack.event_id).toBe(client.event_id)
      acknowledged.add(key)
    }
  }
  const stored = django(['shell', '-c', `import json; from apps.assessment.models import AssessmentEvent; print(json.dumps(list(AssessmentEvent.objects.filter(session_id=${sessionId},client_instance_id__isnull=False).values('event_id','client_instance_id','client_sequence_no','session_id','attempt_id','student_id','payload')),default=str,ensure_ascii=False))`])
  const observed = new Set<string>()
  for (const event of stored) {
    const key = `${event.client_instance_id}:${event.client_sequence_no}`
    const client = expected.get(key)
    expect(client, `Unexpected persisted client event ${event.event_id}`).toBeDefined()
    expect(event.client_instance_id).toBe(client!.client_instance_id)
    expect(event.client_sequence_no).toBe(client!.client_sequence_no)
    expect(event.attempt_id).toBe(client!.attempt_id)
    expect(event.session_id).toBe(client!.session_id)
    expect(event.student_id).toBe(fixture.student.id)
    expect(digest(projectPayload(event.payload, client!.payload_fields, client!.event_type))).toBe(client!.payload_sha256)
    observed.add(key)
  }
  expect(observed.size).toBe(expected.size)
  expect(acknowledged.size).toBe(expected.size)
  witness.audit = { session_id: sessionId, student_id: fixture.student.id, unique_client_events: expected.size,
    stored_client_events: observed.size, acknowledged_client_events: acknowledged.size, checks: { hashes: true, sequence: true, ownership: true, no_missing_events: true } }
  expect(witness.requests.some((request) => request.path.endsWith('/writer-lease/acquire/') && [200, 201].includes(request.status))).toBe(true)
})

test('ordinary navigation confirms one durable save and replays a lost response after reload', async ({ page }) => {
  test.setTimeout(120_000)
  const fixture = django(['shell', '-c', `from apps.accounts.management.commands.seed_e2e_capture_v2 import Command; command=Command(); command.course_term+='-wait-navigation-${Date.now()}'; command.handle()`])
  const witness = await captureProtocol(page)
  witnesses.set(page, witness)
  await page.goto('/login/')
  await page.getByLabel('账号或学号').fill('S002')
  await page.getByLabel('密码').fill('student123')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL(/\/student\/$/)
  await acceptResearchConsentIfRequired(page)
  await page.goto(`/student/courses/${fixture.course.id}/`)
  await page.getByRole('article').filter({ has: page.getByRole('heading', { name: 'E2E Capture V2 五题回放', exact: true }) })
    .getByRole('button', { name: '开始作业' }).click()
  await expect(page.locator('[name=answer]').first()).toBeEnabled()
  const sessionId = Number(page.url().match(/sessions\/(\d+)/)![1])
  const firstId = Number(page.url().match(/questions\/(\d+)/)![1])
  await page.getByRole('radio').first().check()
  await expect(page.getByText('草稿已保存', { exact: true })).toBeVisible()
  const nav = page.locator('[data-nav-assignment]').filter({ hasNot: page.locator('[aria-current=page]') })
  const nextId = await nav.evaluateAll((nodes, current) => Number(nodes.find((node) => Number(node.getAttribute('data-nav-assignment')) !== current)?.getAttribute('data-nav-assignment')), firstId)
  const envelopes: unknown[] = []
  let drop = true
  await page.route('**/api/v1/student/attempts/*/draft/', async (route) => {
    const body = route.request().postDataJSON()
    if (!body.save_and_leave) { await route.continue(); return }
    envelopes.push(body)
    if (drop) {
      drop = false
      const committed = await route.fetch()
      expect(committed.status()).toBe(200)
      await route.abort('failed')
    } else await route.continue()
  })
  await expect(page.locator(`[data-nav-assignment="${nextId}"]`)).toHaveAttribute('href', `/student/sessions/${sessionId}/questions/${nextId}/`)
  await page.locator(`[data-nav-assignment="${nextId}"]`).click()
  await expect(page.locator('[data-navigation-retry]')).toBeVisible()
  await expect(page.locator('[name=answer]').first()).toBeDisabled()
  const persisted = django(['shell', '-c', `import json; from apps.assessment.models import Attempt,NavigationReceipt,SubmissionEvent; a=Attempt.objects.get(session_id=${sessionId},assignment_id=${firstId}); print(json.dumps({'status':a.status,'answer':a.draft_answer,'receipts':NavigationReceipt.objects.filter(attempt=a).count(),'submissions':SubmissionEvent.objects.filter(attempt=a).count()}))`])
  expect(persisted).toEqual({ status: 'draft', answer: 'A', receipts: 1, submissions: 0 })
  await page.reload()
  await expect(page.locator('[data-navigation-retry]')).toBeVisible()
  await page.locator('[data-navigation-retry]').click()
  await expect(page).toHaveURL(new RegExp(`/questions/${nextId}/`))
  await expect(page.locator('[name=answer]').first()).toBeEnabled()
  expect(envelopes[1]).toEqual(envelopes[0])
  await expect(page.locator(`[data-nav-assignment="${firstId}"]`)).toHaveAttribute('href', `/student/sessions/${sessionId}/questions/${firstId}/`)
  await page.locator(`[data-nav-assignment="${firstId}"]`).click()
  await expect(page).toHaveURL(new RegExp(`/questions/${firstId}/`))
  await expect(page.getByRole('radio').first()).toBeChecked()
  const final = django(['shell', '-c', `import json; from apps.assessment.models import NavigationReceipt,SubmissionEvent,AssessmentEvent; print(json.dumps({'receipts':NavigationReceipt.objects.filter(attempt__session_id=${sessionId}).count(),'submissions':SubmissionEvent.objects.filter(attempt__session_id=${sessionId}).count(),'left':AssessmentEvent.objects.filter(session_id=${sessionId},event_type='item_left').count()}))`])
  expect(final.receipts).toBe(2)
  expect(final.submissions).toBe(0)
  expect(final.left).toBe(2)
})

test('capture pointer writes wait for the target event acknowledgement under delayed upload', async ({ page }) => {
  const fixture = django(['shell', '-c', `from apps.accounts.management.commands.seed_e2e_capture_v2 import Command; command=Command(); command.course_term+='-wait-delayed-${Date.now()}'; command.handle()`])
  const witness = await captureProtocol(page)
  witnesses.set(page, witness)
  const earlyPointers: string[] = []
  await page.route('**/api/v1/assessment/process/capture-v2/batch/', async (route) => {
    const body = route.request().postDataJSON()
    if (body.events?.some((row: { event_type: string }) => row.event_type === 'response_transition')) {
      await new Promise((resolve) => setTimeout(resolve, 2500))
    }
    await route.continue()
  })
  page.on('request', (request) => {
    if (!request.url().endsWith('/draft/') || request.method() !== 'POST') return
    const body = request.postDataJSON()
    const confirmed = witness.clientAcknowledgements.flatMap((row: { acknowledgements: { event_id: string }[] }) => row.acknowledgements.map(ack => ack.event_id))
    if (body.target_event_id && !confirmed.includes(body.target_event_id)) earlyPointers.push(body.target_event_id)
  })
  await page.goto('/login/')
  await page.getByLabel('账号或学号').fill('S002')
  await page.getByLabel('密码').fill('student123')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL(/\/student\/$/)
  await acceptResearchConsentIfRequired(page)
  await page.goto(`/student/courses/${fixture.course.id}/`)
  await page.getByRole('article').filter({ has: page.getByRole('heading', { name: 'E2E Capture V2 五题回放', exact: true }) })
    .getByRole('button', { name: '开始作业' }).click()
  await expect(page.locator('[name=answer]').first()).toBeEnabled()
  const response = page.waitForResponse((row) => row.url().endsWith('/draft/'))
  await page.getByRole('radio').first().check()
  const saved = await response
  expect(earlyPointers).toEqual([])
  expect(saved.status()).toBe(200)
})

test('teacher feedback defaults and optional short-response tools remain usable at supported sizes', async ({ page }, testInfo) => {
  const fixture = django(['shell', '-c', `from apps.accounts.management.commands.seed_e2e_capture_v2 import Command; command=Command(); command.course_term+='-teacher-tools-${Date.now()}'; command.handle()`])
  await page.goto('/login/')
  await page.getByLabel('账号或学号').fill('teacher')
  await page.getByLabel('密码').fill('teacher123')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL(/\/teacher\/courses\//)
  await page.goto(`/teacher/courses/${fixture.course.id}/experiments/create/`)
  await expect(page.locator('[name="feedback_mode"][value="full"]')).toBeChecked()
  await expect(page.locator('[name="feedback_release_policy"][value="after_personal_submission"]')).toBeChecked()
  await page.getByText('可选研究工具', { exact: true }).click()
  await expect(page.getByLabel('提供疑点标记', { exact: true })).not.toBeChecked()
  await expect(page.getByLabel('提供待检查标记与检查操作', { exact: true })).not.toBeChecked()
  await page.getByRole('button', { name: '添加固定提示', exact: true }).click()
  await page.getByRole('button', { name: '添加出现规则', exact: true }).click()
  for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(viewport)
    await page.evaluate(() => window.scrollTo(0, 0))
    await page.screenshot({ path: testInfo.outputPath(`teacher-tools-${viewport.width}.png`), fullPage: true })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
  }
  await page.setViewportSize({ width: 720, height: 450 })
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.screenshot({ path: testInfo.outputPath('teacher-tools-compact720.png'), fullPage: true })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
})
