import { expect, test, type Page, type TestInfo } from '@playwright/test'
import { copyFileSync, mkdirSync } from 'node:fs'
import { resolve } from 'node:path'

const origin = 'http://127.0.0.1:8000'
const requestedOutputDir = process.env.KT_VISUAL_OUTPUT_DIR

test('KC quota inventory agrees with saved preflight and permits publication', async ({ page }, testInfo) => {
  test.setTimeout(90_000)
  await login(page)
  await page.goto(`${origin}/teacher/courses/1/experiments/create/`)
  await page.getByLabel('实验名称', { exact: true }).fill(`KC 库存回归 ${Date.now()}`)
  await page.getByLabel('题库', { exact: true }).selectOption({ label: '演示题库' })
  await page.locator('input[name="sampling_mode"][value="kc_quota"]').check()
  await page.getByText('＋ 添加 KC', { exact: true }).click()
  await page.locator('.kt-quota-panel input[type="checkbox"]').first().check()
  const row = page.locator('.kt-quota-table tbody tr').first()
  await expect(row.locator('select')).toHaveValue('primary')
  await expect(row.locator('td').nth(2)).toHaveText(/^\d+$/)
  const available = Number(await row.locator('td').nth(2).innerText())
  expect(available).toBeGreaterThan(0)
  await row.locator('input[type="number"]').fill('1')
  await page.getByRole('button', { name: '保存实验', exact: true }).click()
  await expect(page).toHaveURL(/\/teacher\/courses\/1\/experiments\/\d+\/$/)
  const detailUrl = page.url()
  await page.goto(`${detailUrl}edit/`)
  const preflight = page.getByLabel('已保存抽题配置库存预检', { exact: true })
  await expect(preflight.locator('tbody td').last()).toHaveText(String(available))
  await expect(page.locator('.kt-quota-table tbody tr td').nth(2)).toHaveText(String(available))
  await expect(preflight).toContainText('计划抽取 1 题')
  for (const [name, width, height] of [
    ['reference', 2200, 1100], ['desktop', 1440, 900],
    ['mobile', 390, 844], ['zoom-equivalent', 720, 450],
  ] as const) {
    await page.setViewportSize({ width, height })
    await page.locator('[aria-labelledby="definition-selection"]').scrollIntoViewIfNeeded()
    await page.screenshot({ path: testInfo.outputPath(`kc-inventory-${name}.png`), fullPage: true })
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width + 1)
  }
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.locator('.kt-quota-table input[type="number"]').fill('2')
  await expect(preflight).toBeHidden()
  await expect(page.getByText('抽题配置尚未保存。', { exact: false })).toBeVisible()
  await page.locator('.kt-quota-table input[type="number"]').fill('1')
  await expect(preflight).toBeVisible()
  await page.goto(detailUrl)
  await page.getByRole('button', { name: '发布到班级', exact: true }).click()
  await page.getByRole('checkbox', { name: '选择 认知追踪实验班', exact: true }).check()
  await page.getByRole('button', { name: '运行发布前检查', exact: true }).click()
  await page.getByRole('button', { name: '进入发布确认', exact: true }).click()
  await page.getByRole('button', { name: '确认发布', exact: true }).click()
  await expect(page).toHaveURL(detailUrl)
  await expect(page.getByRole('heading', { name: '已发布班级任务', exact: true })).toBeVisible()
})

async function login(page: Page) {
  await page.goto(`${origin}/login/`)
  await page.getByLabel('账号或学号').fill('teacher')
  await page.getByLabel('密码').fill('teacher123')
  await page.getByRole('button', { name: '登录' }).click()
  await expect(page).toHaveURL(new RegExp(`${origin}/teacher/courses/`))
}

async function selectFirstKnowledgeComponent(page: Page) {
  await page.getByText('选择知识点', { exact: true }).click()
  await page
    .locator('.kt-kc-config-panel', { hasText: '知识点范围' })
    .locator('.kt-kc-tree-selector input[type="checkbox"]')
    .first()
    .check()
}

async function capture(page: Page, testInfo: TestInfo, name: string) {
  await page.getByText(/正在加载/).first().waitFor({ state: 'hidden' })
  await page.evaluate(() => document.fonts.ready)
  const artifactPath = testInfo.outputPath(name)
  await page.screenshot({ path: artifactPath, fullPage: false })
  await testInfo.attach(name, { path: artifactPath, contentType: 'image/png' })
  if (requestedOutputDir) {
    mkdirSync(requestedOutputDir, { recursive: true })
    copyFileSync(artifactPath, resolve(requestedOutputDir, name))
  }
}

test('captures the six real experiment workflow states', async ({ browserName, page }, testInfo) => {
  test.setTimeout(120_000)
  test.skip(browserName !== 'chromium', 'Chromium is the visual acceptance engine.')
  await page.setViewportSize({ width: 1586, height: 992 })
  await login(page)

  await page.goto(`${origin}/teacher/courses/1/experiments/create/`)
  const experimentName = `UI 视觉验收 ${Date.now()}`
  await page.getByLabel('实验名称').fill(experimentName)
  await page.getByLabel('题库', { exact: true }).selectOption({ index: 1 })
  await selectFirstKnowledgeComponent(page)
  await page.getByRole('button', { name: '保存实验' }).click()
  await expect(page).toHaveURL(/\/teacher\/courses\/1\/experiments\/\d+\/$/)
  const detailUrl = page.url()
  await expect(page.getByRole('heading', { name: experimentName })).toBeVisible()
  await capture(page, testInfo, '01-draft-detail.png')

  await page.goto(`${detailUrl}edit/`)
  await expect(page.getByRole('heading', { name: '编辑实验定义' })).toBeVisible()
  await expect(page.getByLabel('实验名称')).toHaveValue(experimentName)
  await capture(page, testInfo, '02-edit-definition.png')
  await expect(page.getByRole('button', { name: '保存更改' })).toBeVisible()

  await page.goto(detailUrl)
  await expect(page.getByRole('heading', { name: experimentName })).toBeVisible()
  await page.getByText(/正在加载/).first().waitFor({ state: 'hidden' })
  await page.getByRole('button', { name: '发布到班级' }).click()
  await expect(page).toHaveURL(/\/publish-plan\/\d+\/step\/1\/$/)
  await capture(page, testInfo, '03-class-schedule.png')

  await page.locator('[id^="publish-class-"]').first().check()
  await page.getByRole('button', { name: '运行发布前检查' }).click()
  await expect(page).toHaveURL(/\/publish-plan\/\d+\/step\/2\/$/)
  await capture(page, testInfo, '04-preflight-check.png')

  await page.getByRole('button', { name: '进入发布确认' }).click()
  await page.getByRole('button', { name: '确认发布' }).waitFor()
  await capture(page, testInfo, '05-publish-confirmation.png')

  await page.getByRole('button', { name: '确认发布' }).click()
  await expect(page).toHaveURL(/\/teacher\/courses\/1\/experiments\/\d+\/$/)
  await capture(page, testInfo, '06-published-detail.png')

  await page.setViewportSize({ width: 1440, height: 900 })
  await capture(page, testInfo, 'responsive-1440x900.png')
  await page.setViewportSize({ width: 390, height: 844 })
  await capture(page, testInfo, 'responsive-390x844.png')
  const overflow = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }))
  expect(overflow.scrollWidth).toBeLessThanOrEqual(overflow.clientWidth + 1)

  await page.setViewportSize({ width: 720, height: 450 })
  await capture(page, testInfo, 'responsive-200-percent-equivalent.png')
  const zoomOverflow = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }))
  expect(zoomOverflow.scrollWidth).toBeLessThanOrEqual(zoomOverflow.clientWidth + 1)
})
