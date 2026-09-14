import { expect, test } from '@playwright/test'
import { spawnSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { join } from 'node:path'
import { requireIsolatedPostgres } from '../scripts/e2e-database.mjs'
import { acceptResearchConsentIfRequired } from './research-consent'

const root = join(import.meta.dirname, '../..')
const localPython = join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
const python = process.env.PYTHON || (existsSync(localPython) ? localPython : 'python')
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
  const fixture = django(['seed_e2e_capture_v2'])
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
    // A 1440×900 browser at 200% exposes a 720×450 CSS layout viewport.
    await page.setViewportSize({ width: 720, height: 450 })
    await page.evaluate(() => window.scrollTo(0, 0))
    await page.screenshot({ path: testInfo.outputPath(`${row.kind}-zoom200.png`), fullPage: true })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.getByRole('button', { name: '确认提交本题', exact: true }).click()
    if (await page.locator('[data-confidence-panel]').isVisible()) await page.getByRole('button', { name: /跳过评价/ }).click()
    const submitted = page.waitForResponse((response) => response.url().endsWith('/submit/'), { timeout: 15000 })
    await page.getByRole('dialog').getByRole('button', { name: '提交并锁定', exact: true }).click()
    const submitResponse = await submitted
    const body = await submitResponse.json()
    expect(submitResponse.ok(), JSON.stringify(body)).toBe(true)
    if (row !== rows.at(-1)) expect(body).not.toHaveProperty('correct_answer')
    await expect(page).toHaveURL((url) => url.pathname === new URL(body.next_url, page.url()).pathname)
    if (row !== rows.at(-1)) expect(new URL(page.url()).searchParams.get('reason')).toBe('submit_next')
  }
  const single = fixture.question_refs.find((row: { question_type: string }) => row.question_type === 'single_choice')
  await page.goto(`/student/courses/${fixture.course.id}/learning/experiments/${fixture.tasks[0].experiment_id}/questions/${single.question_id}/`)
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
})

test('teacher feedback defaults and optional short-response tools remain usable at supported sizes', async ({ page }, testInfo) => {
  await page.goto('/login/')
  await page.getByLabel('账号或学号').fill('teacher')
  await page.getByLabel('密码').fill('teacher123')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL(/\/teacher\/courses\//)
  await page.goto('/teacher/courses/1/experiments/create/')
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
  await page.screenshot({ path: testInfo.outputPath('teacher-tools-zoom200.png'), fullPage: true })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
})
