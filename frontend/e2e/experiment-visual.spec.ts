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
  await page.getByText('＋ 添加 KC', { exact: true }).click()
  await page.locator('input[name="cross_experiment_item_policy"][value="unrestricted"]').check()
  await page.getByRole('button', { name: '保存草稿', exact: true }).click()
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
  await expect(page).toHaveURL(detailUrl, { timeout: 30_000 })
  await expect(page.getByRole('heading', { name: '已发布班级任务', exact: true })).toBeVisible()
})

test('total-count mode uses the whole bank and keeps type allocation usable', async ({ browserName, page }, testInfo) => {
  test.setTimeout(90_000)
  test.skip(browserName !== 'chromium', 'Chromium is the visual acceptance engine.')
  await page.setViewportSize({ width: 1672, height: 941 })
  await login(page)
  await page.goto(origin + '/teacher/courses/1/experiments/create/')
  await page.getByLabel('实验名称', { exact: true }).fill('全题库题型回归 ' + Date.now())
  await page.getByLabel('题库', { exact: true }).selectOption({ label: '演示题库' })
  await expect(page.getByText('当前使用整个题库', { exact: true })).toBeVisible()
  await expect(page.locator('.kt-match-scope')).toHaveCount(0)

  await page.getByLabel('每位参与者题目数', { exact: true }).fill('1')
  await page.getByText('选择题型', { exact: true }).click()
  const singleType = page.locator('.kt-type-tree-selector label').filter({ hasText: '单选题' })
  await expect(singleType.locator('small')).not.toHaveText('正在读取')
  await expect(singleType.locator('small')).toHaveText(/[1-9]\d* 道可用/)
  await singleType.locator('input[type="checkbox"]').check()
  await singleType.locator('input[type="number"]').fill('1')
  await page.getByText('选择题型', { exact: true }).click()
  await page.locator('input[name="cross_experiment_item_policy"][value="unrestricted"]').check()
  await expect(page.getByRole('button', { name: '保存草稿', exact: true })).toBeEnabled()

  await page.locator('[aria-labelledby="definition-runtime"]').scrollIntoViewIfNeeded()
  await capture(page, testInfo, 'runtime-control-1672x941.png')
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(1673)
  const runtimeHeadingBox = await page.locator('[aria-labelledby="definition-runtime"] > .kt-numbered-form-section__heading').boundingBox()
  const runtimeContentBox = await page.locator('.kt-runtime-primary').boundingBox()
  const runtimeSummaryBox = await page.locator('.kt-runtime-summary').boundingBox()
  const runtimeCards = page.locator('.kt-runtime-settings-card')
  const leftCardBox = await runtimeCards.nth(0).boundingBox()
  const rightCardBox = await runtimeCards.nth(1).boundingBox()
  expect(runtimeHeadingBox?.x).toBeCloseTo(182, 0)
  expect(runtimeHeadingBox?.width).toBeCloseTo(160, 0)
  expect(runtimeContentBox?.x).toBeCloseTo(352, 0)
  expect(runtimeSummaryBox?.width).toBeGreaterThanOrEqual(493)
  expect(runtimeSummaryBox?.width).toBeLessThanOrEqual(501)
  expect(leftCardBox?.height).toBeCloseTo(350, 0)
  expect(rightCardBox?.height).toBeCloseTo(350, 0)
  expect(Math.abs(Number(leftCardBox?.width) - Number(rightCardBox?.width))).toBeLessThanOrEqual(1)
  expect(Number(rightCardBox?.x) - Number(leftCardBox?.x) - Number(leftCardBox?.width)).toBeCloseTo(10, 0)
  for (const [name, width, height] of [
    ['1440x900', 1440, 900],
    ['390x844', 390, 844],
    ['200-percent-equivalent', 720, 450],
  ] as const) {
    await page.setViewportSize({ width, height })
    await page.locator('[aria-labelledby="definition-runtime"]').scrollIntoViewIfNeeded()
    await capture(page, testInfo, 'runtime-control-' + name + '.png')
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width + 1)
  }
  await page.setViewportSize({ width: 1440, height: 900 })

  await page.getByRole('button', { name: '保存草稿', exact: true }).click()
  await expect(page).toHaveURL(/\/teacher\/courses\/1\/experiments\/\d+\/$/)
  await page.getByRole('button', { name: '发布到班级', exact: true }).click()
  await page.locator('[id^="publish-class-"]').first().check()
  await page.getByRole('button', { name: '运行发布前检查', exact: true }).click()
  await expect(page).toHaveURL(/\/publish-plan\/\d+\/step\/2\/$/, { timeout: 30_000 })
  await expect(page.getByText('发布前检查', { exact: false }).first()).toBeVisible()
})

async function login(page: Page) {
  await page.goto(`${origin}/login/`)
  await page.getByLabel('账号或学号').fill('teacher')
  await page.getByLabel('密码').fill('teacher123')
  await page.getByRole('button', { name: '登录' }).click()
  await expect(page).toHaveURL(new RegExp(`${origin}/teacher/courses/`))
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
  await page.setViewportSize({ width: 1672, height: 941 })
  await login(page)

  await page.goto(`${origin}/teacher/courses/1/experiments/create/`)
  const experimentName = `UI 视觉验收 ${Date.now()}`
  await page.getByLabel('题库', { exact: true }).selectOption({ index: 1 })
  await page.locator('input[name="sampling_mode"][value="kc_quota"]').check()
  await page.getByText('＋ 添加 KC', { exact: true }).click()
  const kcOptions = page.locator('.kt-quota-panel .kt-kc-tree-selector input[type="checkbox"]')
  for (let index = 0; index < Math.min(3, await kcOptions.count()); index += 1) {
    await kcOptions.nth(index).check()
  }
  await page.getByText('＋ 添加 KC', { exact: true }).click()
  for (const input of await page.locator('.kt-quota-table tbody input[type="number"]').all()) {
    await input.fill('1')
  }
  await expect(page.locator('[data-history-policy]')).toHaveCount(3)
  await expect(page.getByText('跨实验题目控制', { exact: true })).toBeVisible()
  await capture(page, testInfo, '00-create-cross-experiment-policy-1672x941.png')
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(1673)
  await page.setViewportSize({ width: 1586, height: 992 })
  await page.getByLabel('实验名称').fill(experimentName)
  await page.locator('input[name="sampling_mode"][value="total_count"]').check()
  await page.getByLabel('每位参与者题目数').fill('1')
  await page.locator('input[name="cross_experiment_item_policy"][value="unrestricted"]').check()
  await page.getByRole('button', { name: '保存草稿' }).click()
  await expect(page).toHaveURL(/\/teacher\/courses\/1\/experiments\/\d+\/$/)
  const detailUrl = page.url()
  await expect(page.getByRole('heading', { name: experimentName })).toBeVisible()
  await capture(page, testInfo, '01-draft-detail.png')

  await page.goto(`${detailUrl}edit/`)
  await expect(page.getByRole('heading', { name: '编辑实验定义' })).toBeVisible()
  await expect(page.getByLabel('实验名称')).toHaveValue(experimentName)
  await capture(page, testInfo, '02-edit-definition.png')
  await expect(page.getByRole('button', { name: '保存草稿' })).toBeVisible()

  await page.goto(detailUrl)
  await expect(page.getByRole('heading', { name: experimentName })).toBeVisible()
  await page.getByText(/正在加载/).first().waitFor({ state: 'hidden' })
  await page.getByRole('button', { name: '发布到班级' }).click()
  await expect(page).toHaveURL(/\/publish-plan\/\d+\/step\/1\/$/)
  await capture(page, testInfo, '03-class-schedule.png')

  await page.locator('[id^="publish-class-"]').first().check()
  await page.getByRole('button', { name: '运行发布前检查' }).click()
  await expect(page).toHaveURL(/\/publish-plan\/\d+\/step\/2\/$/, { timeout: 30_000 })
  await capture(page, testInfo, '04-preflight-check.png')

  await page.getByRole('button', { name: '进入发布确认' }).click()
  await page.getByRole('button', { name: '确认发布' }).waitFor()
  await capture(page, testInfo, '05-publish-confirmation.png')

  await page.getByRole('button', { name: '确认发布' }).click()
  await expect(page).toHaveURL(/\/teacher\/courses\/1\/experiments\/\d+\/$/, { timeout: 30_000 })
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
