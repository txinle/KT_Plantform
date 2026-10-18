import { execFileSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { resolve } from 'node:path'
import { expect, test, type Page, type TestInfo } from '@playwright/test'

const repository = resolve(import.meta.dirname, '../..')
const python = resolve(repository, '.venv/Scripts/python.exe')
const visualDatabase = 'kt_teacher_visual_20261018'

test.skip(process.env.POSTGRES_DB !== visualDatabase, 'Requires the dedicated synthetic teacher visual database; never uses the shared E2E database.')

function django(args: string[]) {
  if (process.env.POSTGRES_DB !== visualDatabase) throw new Error('Refusing a non-visual database')
  return execFileSync(python, ['manage.py', ...args], {
    cwd: resolve(repository, 'server'), env: { ...process.env, PYTHONUTF8: '1' }, encoding: 'utf8', timeout: 60_000,
  })
}

function deletionFixture() {
  const output = django(['shell', '-c', String.raw`
import json, uuid
from pathlib import Path
from django.conf import settings
from django.db import transaction
from apps.accounts.models import User
from apps.analytics.models import ExportJob, ExportSnapshot
from apps.experiments.models import Experiment, ExperimentQuestion, ExperimentSamplingKC
from apps.experiments.publication_services import get_or_create_publish_plan, save_plan_selection, save_plan_schedules, generate_preview, commit_publish_plan
assert settings.DATABASES['default']['NAME'] == 'kt_teacher_visual_20261018'
with transaction.atomic():
    teacher=User.objects.get(username='teacher')
    source=Experiment.objects.get(pk=1)
    course=source.course
    experiment=Experiment.objects.create(course=course,owner=teacher,created_by=teacher,name='浏览器合成删除实验 '+uuid.uuid4().hex[:8],question_bank=source.question_bank,sampling_mode=Experiment.SamplingMode.TOTAL_COUNT,sampling_total_count=1,ordering_mode=Experiment.Ordering.FIXED)
    ExperimentQuestion.objects.create(experiment=experiment,question=source.question_bank.questions.order_by('sequence').first(),display_order=1)
    ExperimentSamplingKC.objects.bulk_create([ExperimentSamplingKC(experiment=experiment,concept_id=item.concept_id) for item in source.sampling_kcs.all()])
    plan=get_or_create_publish_plan(experiment,teacher)
    plan=save_plan_selection(plan,teacher,expected_revision=plan.plan_revision,class_ids=[course.classes.first().pk],exclusions_by_class={})
    plan=save_plan_schedules(plan,teacher,expected_revision=plan.plan_revision,default_opens_at=None,default_closes_at=None,overrides={})
    generate_preview(plan,teacher,expected_revision=plan.plan_revision)
    plan.refresh_from_db()
    commit_publish_plan(plan,teacher,expected_revision=plan.plan_revision)
    version=experiment.versions.first()
    snapshot=ExportSnapshot.objects.create(course=course,created_by=teacher,selected_versions=[{'id':version.pk,'version':1}],filter_definition={'experiment_ids':[experiment.pk]},source_bundle={'synthetic':'browser-e2e'})
    job=ExportJob.objects.create(course=course,snapshot=snapshot,created_by=teacher,status='succeeded')
    path=Path(settings.MEDIA_ROOT)/'exports'/'teacher-visual-20261018'/str(job.pk)/'synthetic.zip'
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(b'SYNTHETIC BROWSER TEST FILE')
    job.artifact_path=str(path)
    job.save(update_fields=['artifact_path'])
    print('VISUAL_FIXTURE='+json.dumps({'course_id':course.pk,'experiment_id':experiment.pk,'name':experiment.name,'artifact':str(path)}))
`])
  return JSON.parse(output.split('\n').find(line => line.startsWith('VISUAL_FIXTURE='))!.slice('VISUAL_FIXTURE='.length)) as {
    course_id: number; experiment_id: number; name: string; artifact: string
  }
}

async function login(page: Page) {
  await page.goto('/login/', { waitUntil: 'commit' })
  await page.getByLabel('账号或学号', { exact: true }).fill('teacher')
  await page.getByLabel('密码', { exact: true }).fill('teacher123')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL(/\/teacher\/courses\/$/)
}

async function captureSizes(page: Page, info: TestInfo, name: string, dialog = false) {
  for (const size of [
    { label: 'desktop-1440x900', width: 1440, height: 900 },
    { label: 'mobile-390x844', width: 390, height: 844 },
    // Browser zoom 200% at a physical 1440x900 display yields this CSS viewport.
    { label: 'zoom200-css720x450', width: 720, height: 450 },
  ]) {
    await page.setViewportSize({ width: size.width, height: size.height })
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
    if (dialog) {
      const bounds = await page.getByRole('dialog').boundingBox()
      expect(bounds).toBeTruthy()
      expect(bounds!.width).toBeLessThanOrEqual(size.width)
      expect(bounds!.height).toBeLessThanOrEqual(size.height)
      await page.getByRole('dialog').locator('button:enabled').first().focus()
    } else await page.getByTestId('refresh-research-data').focus()
    await page.keyboard.press('Tab')
    expect(await page.locator(':focus').evaluate(element => element.getClientRects().length > 0)).toBe(true)
    await page.screenshot({ path: info.outputPath(`${name}-${size.label}.png`), fullPage: true })
  }
}

async function openDeletion(page: Page, fixture: ReturnType<typeof deletionFixture>) {
  await page.goto(`/teacher/courses/${fixture.course_id}/experiments/`)
  await page.locator(`[data-delete-experiment="${fixture.experiment_id}"]`).filter({ visible: true }).click()
  await expect(page.getByRole('dialog')).toContainText(fixture.name)
  await expect(page.locator('[data-deletion-confirm]')).toBeEnabled()
}

test('real PostgreSQL prepared deletion recovers a lost response via its receipt and completes asynchronous cleanup', async ({ page }, info) => {
  const fixture = deletionFixture()
  await login(page)
  await openDeletion(page, fixture)
  await captureSizes(page, info, 'real-deletion-preview', true)
  let deletionPosts = 0
  let committedResult: { operation_id: string; deleted: boolean; cleanup_pending: boolean; cleanup_id: string } | undefined
  await page.route(`**/api/v1/teacher/courses/${fixture.course_id}/experiments/${fixture.experiment_id}/deletion/`, async route => {
    if (route.request().method() !== 'POST') { await route.continue(); return }
    deletionPosts += 1
    const response = await route.fetch()
    expect(response.status()).toBe(200)
    committedResult = (await response.json()).data
    // The real backend commits; only delivery of the response to Vue is lost.
    await route.abort('failed')
  })
  const admitted = page.waitForResponse(response => response.url().endsWith('/deletion-operations/') && response.request().method() === 'POST')
  await page.locator('[data-deletion-confirm]').focus()
  await page.keyboard.press('Enter')
  expect((await admitted).status()).toBe(201)
  await expect(page.getByRole('dialog')).toContainText('数据已删除，文件清理中')
  expect(deletionPosts).toBe(1)
  expect(committedResult).toMatchObject({ deleted: true, cleanup_pending: true })
  expect(existsSync(fixture.artifact)).toBe(true)
  await captureSizes(page, info, 'real-deleted-cleanup-pending', true)
  django(['run_research_artifact_cleanups', '--once'])
  // An automatic read may finish and remove the dialog while the worker exits.
  await page.locator('[data-cleanup-refresh]').evaluateAll(buttons => {
    const button = buttons[0] as HTMLButtonElement | undefined
    if (button && !button.disabled) button.click()
  })
  await expect(page.getByRole('dialog')).not.toBeVisible()
  expect(existsSync(fixture.artifact)).toBe(false)
  const operation = await page.request.get(`/api/v1/teacher/courses/${fixture.course_id}/deletion-operations/${committedResult!.operation_id}/`)
  expect(operation.status()).toBe(200)
  expect((await operation.json()).data).toMatchObject({ state: 'succeeded', result: { deleted: true } })
  const verification = django(['shell', '-c', `from apps.experiments.models import Experiment; from apps.assessment.models import PreparedSessionArtifact; print('EXPERIMENT_EXISTS='+str(Experiment.objects.filter(pk=${fixture.experiment_id}).exists())); print('PREPARED_REMAINS='+str(PreparedSessionArtifact.objects.filter(session__version__experiment_id=${fixture.experiment_id}).exists()))`])
  expect(verification).toContain('EXPERIMENT_EXISTS=False')
  expect(verification).toContain('PREPARED_REMAINS=False')
  await info.attach('real-deletion-receipt', { body: JSON.stringify({ fixture, result: committedResult, deletionPosts, verification }), contentType: 'application/json' })
})

test('real materialized advanced data remains usable at desktop, mobile and zoom with keyboard navigation', async ({ page }, info) => {
  await login(page)
  const response = page.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/teacher/courses/1/data/advanced/')
  await page.goto('/teacher/courses/1/data/advanced/?experiment_ids=1&class_id=1&table=sessions')
  const result = await response
  expect(result.status()).toBe(200)
  expect((await result.json()).data.preparation.status_checked_at).toBeTruthy()
  await expect(page.getByRole('heading', { name: '研究数据预览', exact: true })).toBeVisible()
  await captureSizes(page, info, 'real-materialized-advanced')
  await page.locator('[data-card-key="questions"]').click()
  await expect(page).toHaveURL(/table=questions/)
  await expect(page.getByTestId('refresh-research-data')).toBeEnabled()
})

test('presentation fixture: readable partial data with failures has clear sample time and preserves rows', async ({ page }, info) => {
  info.annotations.push({ type: 'presentation-fixture', description: 'Advanced response status is mocked from a real authorized payload; this test makes no backend readiness claim.' })
  await login(page)
  const real = await page.request.get('/api/v1/teacher/courses/1/data/advanced/?experiment_ids=1&class_id=1&table=sessions')
  const envelope = await real.json()
  envelope.data.preparation = { ...envelope.data.preparation, state: 'failed', ready_count: 1, total_count: 3,
    pending_count: 1, failed_count: 1, status_checked_at: '2026-10-18T05:00:00Z', oldest_pending_seconds: 300,
    message: '部分研究数据已可读取，另有任务整理失败。截至状态采样时，最早未就绪整理任务的年龄为 300 秒，包含排队、处理和重试。' }
  await page.route(/\/api\/v1\/teacher\/courses\/1\/data\/advanced\/\?/, route => route.fulfill({ json: envelope }))
  await page.goto('/teacher/courses/1/data/advanced/?experiment_ids=1&class_id=1&table=sessions')
  await expect(page.getByTestId('preparation-state')).toContainText('部分研究数据已可读取')
  await expect(page.getByTestId('preparation-counts')).toContainText('整理失败 1')
  await captureSizes(page, info, 'presentation-advanced-partial-failed')
})

for (const state of ['unknown', 'cleanup-failed'] as const) {
  test(`presentation fixture: deletion ${state} remains explicit and cannot resubmit`, async ({ page }, info) => {
    info.annotations.push({ type: 'presentation-fixture', description: 'Operation admission, deletion and status are mocked; no experiment is deleted in this test.' })
    await login(page)
    const fixture = { course_id: 1, experiment_id: 1, name: '演示交互数据采集', artifact: '' }
    let operationId = ''
    await page.route('**/api/v1/teacher/courses/1/deletion-operations/', async route => {
      operationId = route.request().postDataJSON().operation_id
      await route.fulfill({ json: { data: { operation_id: operationId, state: 'pending', result: null }, meta: { request_id: 'presentation-admission' } } })
    })
    await page.route('**/api/v1/teacher/courses/1/deletion-operations/*/', route => route.fulfill({ json: {
      data: { operation_id: operationId, state: 'pending', result: null }, meta: { request_id: 'presentation-operation' },
    } }))
    await page.route('**/api/v1/teacher/courses/1/experiments/1/deletion/', async route => {
      if (route.request().method() !== 'POST') { await route.continue(); return }
      if (state === 'unknown') await route.fulfill({ status: 503, json: { error: { code: 'SERVER_ERROR', message: '结果待核实' } } })
      else await route.fulfill({ json: { data: { operation_id: operationId, deleted: true, cleanup_pending: true, cleanup_id: 'presentation-cleanup' }, meta: { request_id: 'presentation-deleted' } } })
    })
    await page.route('**/api/v1/teacher/courses/1/deletion-cleanups/presentation-cleanup/', route => route.fulfill({ json: {
      data: { state: 'failed', status: 'pending', cleanup_pending: true, error: '演示状态：存储清理失败，请核对记录。', attempts: 8, next_retry_at: null, retained_count: 0 },
      meta: { request_id: 'presentation-cleanup' },
    } }))
    await openDeletion(page, fixture)
    await page.locator('[data-deletion-confirm]').click()
    await expect(page.getByRole('dialog')).toContainText(state === 'unknown' ? '删除结果待核实' : '数据已删除，文件清理失败')
    await expect(page.locator('[data-deletion-confirm]')).toBeDisabled()
    await captureSizes(page, info, `presentation-deletion-${state}`, true)
  })
}
