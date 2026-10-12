import { expect, test } from '@playwright/test'
import { readFile } from 'node:fs/promises'

test('independent pages, authoritative home redirects and v1 binary downloads work through the gateway', async ({ page, context }, info) => {
  test.setTimeout(120_000)
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  const backend = new URL(info.project.use.baseURL!)
  backend.port = String(Number(backend.port) + 1)
  expect((await page.request.get(new URL('/login/', backend).href)).status()).toBe(404)
  expect((await page.request.get('/not-a-registered-page/')).status()).toBe(404)
  expect((await page.request.post('/login/', { data: {} })).status()).toBe(405)
  await page.goto('/')
  await page.getByLabel('账号或学号').fill('teacher')
  await page.getByLabel('密码', { exact: true }).fill('teacher123')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL(/\/teacher\/courses\/$/)
  await expect(page.getByRole('heading', { name: '我的课程', exact: true })).toBeVisible()
  await expect(page.getByText('正在加载课程…', { exact: true })).toBeHidden()
  await page.goto('/teacher/')
  await expect(page).toHaveURL(/\/teacher\/courses\/$/)
  await expect(page.getByRole('heading', { name: '我的课程', exact: true })).toBeVisible()
  await expect(page.getByText('正在加载课程…', { exact: true })).toBeHidden()
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 900 })
    await expect(page.getByRole('heading', { name: '我的课程', exact: true })).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
    await page.screenshot({ path: info.outputPath(`home-${width}.png`), fullPage: true })
  }
  await page.setViewportSize({ width: 1440, height: 900 })
  const csrf = (await context.cookies()).find((cookie) => cookie.name === 'csrftoken')!.value
  const headers = { 'X-CSRFToken': csrf }
  const bankResponse = await page.request.post('/api/v1/teacher/banks/', { headers, data: { name: `Gateway download bank ${Date.now()}`, description: '', visibility: 'private' } })
  expect(bankResponse.status()).toBe(201)
  const bank = (await bankResponse.json()).data
  const experimentResponse = await page.request.post('/api/v1/teacher/standalone-experiments/', { headers, data: {
    name: `Gateway binary export ${Date.now()}`, description: '', question_bank_id: bank.id,
    selection_mode: 'all', question_quotas: [], ordering_mode: 'fixed', feedback_mode: 'none', feedback_release_policy: 'after_personal_submission',
    time_mode: 'none', total_time_limit_minutes: null, per_question_time_limit_seconds: null, resume_mode: 'resumable', confidence_mode: 'off', confidence_sampling_unit: 'attempt',
    confidence_probability: null, confidence_randomization_seed: '', confidence_assignment_version: 'v1', confidence_scale_version: 'v2', confidence_prompt_version: 'v1', heartbeat_interval_seconds: 30,
  } })
  expect(experimentResponse.status()).toBe(201)
  const experiment = (await experimentResponse.json()).data.experiment
  await page.goto(`/teacher/experiments/${experiment.id}/results/`)
  await expect(page.locator('[data-export="identified"]')).toBeVisible()
  const exported = page.waitForEvent('download')
  const exportRequest = page.waitForRequest((request) => request.method() === 'POST' && request.url().endsWith(`/api/v1/teacher/experiments/${experiment.id}/identified.xlsx`))
  await page.getByRole('button', { name: '下载所选字段 Excel', exact: true }).click()
  expect((await exportRequest).headers()['x-csrftoken']).toMatch(/^[a-zA-Z0-9]{64}$/)
  const file = await exported
  expect(file.suggestedFilename()).toMatch(/\.xlsx$/)
  const bytes = await readFile((await file.path())!)
  expect(bytes.subarray(0, 2).toString()).toBe('PK')
  const navigationDownload = page.waitForEvent('download')
  await page.getByRole('button', { name: '下载实名导航事件 CSV', exact: true }).click()
  expect((await navigationDownload).suggestedFilename()).toMatch(/\.csv$/)
  await page.goto('/teacher/accounts/students/import/')
  const templateDownload = page.waitForEvent('download')
  await page.getByRole('link', { name: '下载 Excel 参考模板', exact: true }).click()
  expect((await templateDownload).suggestedFilename()).toMatch(/\.xlsx$/)
  const icon = await page.request.get('/static/spa-assets/icons/kt-sprite.svg')
  expect(icon.status()).toBe(200)
  expect(icon.headers()['content-type']).toContain('image/svg+xml')
  expect(errors).toEqual([])
})
