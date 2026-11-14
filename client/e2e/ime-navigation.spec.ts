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

test('next question commits missing IME completion with both blank values and durable capture', async ({ page }) => {
  test.setTimeout(90_000)
  const fixture = django(`from apps.accounts.management.commands.seed_e2e_capture_v2 import Command
class IMECommand(Command):
 def _questions(self, *args):
  questions=super()._questions(*args)
  for q in questions:
   if q.question_type=='fill_blank':
    q.refresh_from_db(); q.sequence=0; q.text='数值为____；单位为____。'; q.correct_answer='-10；西门子'; q.save()
  return questions
c=IMECommand(); c.course_term+='-ime-${Date.now()}'; c._research_tools=lambda questions: {}; c.handle()`)
  await page.goto('/login/')
  await page.getByLabel('账号或学号').fill('S002')
  await page.getByLabel('密码').fill('student123')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL(/\/student\/$/)
  await acceptResearchConsentIfRequired(page)
  await page.goto(`/student/courses/${fixture.course.id}/`)
  await page.getByRole('button', { name: '开始作业', exact: true }).click()
  await expect(page.locator('[name=answer]').first()).toBeEnabled()
  const session = Number(page.url().match(/sessions\/(\d+)/)![1])
  const target = django(`import json; from apps.assessment.models import Attempt; a=Attempt.objects.get(session_id=${session},assignment__snapshot__question_type='fill_blank'); print(json.dumps({'assignment':a.assignment_id,'attempt':a.pk}))`)
  await page.locator(`[data-nav-assignment="${target.assignment}"]:visible`).click()
  await expect(page.getByLabel('第 1 空', { exact: true })).toBeEnabled()
  await page.getByLabel('第 1 空', { exact: true }).fill('-10')
  const second = page.getByLabel('第 2 空', { exact: true })
  await second.focus()
  await second.evaluate(element => {
    const input = element as HTMLInputElement
    input.dispatchEvent(new CompositionEvent('compositionstart', { bubbles: true }))
    input.value = '西门子'
    input.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertCompositionText', isComposing: true }))
    input.blur()
  })
  await expect(page.getByText('输入法尚未确认，请完成输入后重试。', { exact: true })).toBeVisible()
  await page.locator('[data-next-question]').click()
  await expect(page).not.toHaveURL(new RegExp(`/questions/${target.assignment}/`))
  await expect(page.locator('[name=answer]').first()).toBeEnabled()
  const evidence = django(`import json; from apps.assessment.models import Attempt,AssessmentEvent,NavigationReceipt; a=Attempt.objects.get(pk=${target.attempt}); e=AssessmentEvent.objects.filter(attempt=a); print(json.dumps({'answer':a.draft_answer,'states':list(e.filter(event_type='response_transition').order_by('client_sequence_no').values_list('payload__after_state__value',flat=True)),'commits':list(e.filter(event_type='input_operation',payload__input_type='navigationCommit').values_list('payload__composition',flat=True)),'receipts':NavigationReceipt.objects.filter(attempt=a).count(),'contract':a.session.capture_contract.contract_name}))`)
  expect(evidence.answer).toBe('-10；西门子')
  expect(evidence.states.at(-1)).toBe('-10；西门子')
  expect(evidence.states.filter((value: string) => value === '-10；西门子')).toHaveLength(1)
  expect(evidence.commits).toEqual(['none'])
  expect(evidence.receipts).toBe(1)
  expect(evidence.contract).toBe('capture-v2-stable-input-v1')
  await page.locator(`[data-nav-assignment="${target.assignment}"]:visible`).click()
  await expect(page.getByLabel('第 1 空', { exact: true })).toHaveValue('-10')
  await expect(second).toHaveValue('西门子')
})
