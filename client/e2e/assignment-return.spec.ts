import { expect, test } from '@playwright/test'
import { spawnSync } from 'node:child_process'
import { requireServerPython, serverDir } from '../scripts/e2e-runtime.mjs'
import { requireIsolatedPostgres } from '../scripts/e2e-database.mjs'
import { acceptResearchConsentIfRequired } from './research-consent'

function django(script: string) {
  const result = spawnSync(requireServerPython(), ['manage.py', 'shell', '-c', script], {
    cwd: serverDir, encoding: 'utf8', env: { ...process.env, ...requireIsolatedPostgres(process.env), PYTHONUTF8: '1' },
  })
  if (result.status !== 0) throw new Error(result.stderr || result.stdout)
  return JSON.parse(result.stdout.trim().split('\n').at(-1)!)
}

test('teacher returns one student assignment through the platform dialog and the student starts fresh', async ({ page, browser }, testInfo) => {
  test.setTimeout(120_000)
  const fixture = django(`from apps.accounts.management.commands.seed_e2e_capture_v2 import Command; c=Command(); c.course_term+='-return-${Date.now()}'; c._research_tools=lambda questions: {}; c.handle()`)
  await page.goto('/login/')
  await page.getByLabel('账号或学号').fill('S002')
  await page.getByLabel('密码').fill('student123')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL(/\/student\/$/)
  await acceptResearchConsentIfRequired(page)
  await page.goto(`/student/courses/${fixture.course.id}/`)
  await page.getByRole('article').filter({ has: page.getByRole('heading', { name: 'E2E Capture V2 五题回放', exact: true }) })
    .getByRole('button', { name: '开始作业' }).click()
  await page.getByRole('radio').first().check()
  await expect(page.getByText('草稿已保存', { exact: true })).toBeVisible()
  const oldUrl = page.url()
  const oldSessionId = Number(oldUrl.match(/sessions\/(\d+)/)![1])
  const scope = django(`import json; from apps.assessment.models import ExperimentSession; s=ExperimentSession.objects.get(pk=${oldSessionId}); print(json.dumps({'experiment':s.class_task.experiment_id,'task':s.class_task_id,'participant':s.participant_snapshot_id,'order':list(s.assignments.order_by('assigned_sequence_index').values_list('snapshot_id',flat=True)),'peer_ids':list(ExperimentSession.objects.filter(class_task=s.class_task).exclude(pk=s.pk).values_list('pk',flat=True))}))`)
  const teacherContext = await browser.newContext({ baseURL: testInfo.project.use.baseURL, viewport: { width: 1440, height: 900 } })
  const teacher = await teacherContext.newPage()
  try {
    await teacher.goto('/login/')
    await teacher.getByLabel('账号或学号').fill('teacher')
    await teacher.getByLabel('密码').fill('teacher123')
    await teacher.getByRole('button', { name: '登录', exact: true }).click()
    await expect(teacher).toHaveURL(/\/teacher\/courses\//)
    await teacher.goto(`/teacher/courses/${fixture.course.id}/experiments/${scope.experiment}/?tab=collection`)
    await teacher.locator(`[data-assignment-return="${scope.task}"]`).click()
    const dialog = teacher.getByRole('dialog', { name: '作业打回', exact: true })
    await expect(dialog).toBeVisible()
    await dialog.getByRole('textbox', { name: '搜索学生' }).fill('S002')
    await dialog.getByRole('button', { name: '搜索', exact: true }).click()
    await expect(dialog.locator(`[data-return-select="${scope.participant}"]`)).toBeVisible()
    for (const width of [1440, 390]) {
      await teacher.setViewportSize({ width, height: 844 })
      await teacher.screenshot({ path: testInfo.outputPath(`return-search-${width}.png`), fullPage: true })
      expect(await teacher.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
    }
    await teacher.setViewportSize({ width: 1440, height: 900 })
    await dialog.locator(`[data-return-select="${scope.participant}"]`).click()
    await expect(dialog.getByText('将清除作答、提交及评分记录，不保留答案备份。此操作不可撤销。')).toBeVisible()
    for (const width of [1440, 390]) {
      await teacher.setViewportSize({ width, height: 900 })
      expect(await teacher.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
      await expect(dialog.locator('[data-return-confirm]')).toBeInViewport()
      await teacher.screenshot({ path: testInfo.outputPath(`return-confirm-${width}.png`), fullPage: true })
    }
    // Half the CSS viewport exercises the reflow of 200% desktop browser zoom.
    await teacher.setViewportSize({ width: 720, height: 450 })
    await expect(dialog.locator('[data-return-confirm]')).toBeInViewport()
    expect(await teacher.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
    await teacher.screenshot({ path: testInfo.outputPath('return-confirm-200.png'), fullPage: true })
    await teacher.setViewportSize({ width: 1440, height: 900 })
    await dialog.locator('[data-return-confirm]').focus()
    await teacher.keyboard.press('Enter')
    await expect(dialog.getByText(/已恢复为未开始/)).toBeVisible()
    await dialog.getByRole('button', { name: '完成', exact: true }).click()
    await expect(dialog).not.toBeVisible()
    const result = django(`import json; from apps.assessment.models import ExperimentSession,AssessmentEvent; s=ExperimentSession.objects.get(participant_snapshot_id=${scope.participant}); print(json.dumps({'id':s.pk,'status':s.status,'started':s.started_at is not None,'answers':[a.draft_answer for a in s.attempts.all()],'order':list(s.assignments.order_by('assigned_sequence_index').values_list('snapshot_id',flat=True)),'old_events':AssessmentEvent.objects.filter(session_id=${oldSessionId}).count(),'peer_ids':list(ExperimentSession.objects.filter(class_task=s.class_task).exclude(pk=s.pk).values_list('pk',flat=True))}))`)
    expect(result.id).not.toBe(oldSessionId)
    expect(result.status).toBe('prepared')
    expect(result.started).toBe(false)
    expect(result.answers.every((answer: unknown) => answer == null || answer === '')).toBe(true)
    expect(result.old_events).toBe(0)
    expect(result.order).toEqual(scope.order)
    expect(result.peer_ids).toEqual(scope.peer_ids)
    await page.goto(`/student/courses/${fixture.course.id}/`)
    await page.getByRole('article').filter({ has: page.getByRole('heading', { name: 'E2E Capture V2 五题回放', exact: true }) })
      .getByRole('button', { name: '开始作业' }).click()
    await expect(page).not.toHaveURL(oldUrl)
    await expect(page.getByRole('radio').first()).not.toBeChecked()
    await page.getByRole('radio').nth(1).check()
    await expect(page.getByText('草稿已保存', { exact: true })).toBeVisible()
  } finally { await teacherContext.close() }
})
