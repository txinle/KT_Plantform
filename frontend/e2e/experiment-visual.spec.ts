import { expect, test, type Page, type TestInfo } from '@playwright/test'
import { copyFileSync, mkdirSync } from 'node:fs'
import { resolve } from 'node:path'

const origin = 'http://127.0.0.1:8011'
const requestedOutputDir = process.env.KT_VISUAL_OUTPUT_DIR

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
  await page.setViewportSize({ width: 1586, height: 992 })
  await login(page)

  await page.goto(`${origin}/teacher/courses/1/experiments/create/`)
  const experimentName = `UI 视觉验收 ${Date.now()}`
  await page.getByLabel('实验名称').fill(experimentName)
  await page.getByLabel('题库', { exact: true }).selectOption({ index: 1 })
  await page.getByRole('button', { name: '保存实验' }).click()
  await expect(page).toHaveURL(/\/teacher\/courses\/1\/experiments\/\d+\/$/)
  const detailUrl = page.url()
  await expect(page.getByRole('heading', { name: experimentName })).toBeVisible()
  await capture(page, testInfo, '01-draft-detail.png')

  await page.goto(`${detailUrl}edit/`)
  await expect(page.getByRole('heading', { name: '编辑实验定义' })).toBeVisible()
  await expect(page.getByLabel('实验名称')).toHaveValue(experimentName)
  await capture(page, testInfo, '02-edit-definition.png')
  await expect(page.getByRole('button', { name: '保存更改' })).toBeInViewport()

  await page.goto(detailUrl)
  await expect(page.getByRole('heading', { name: experimentName })).toBeVisible()
  await page.getByText(/正在加载/).first().waitFor({ state: 'hidden' })
  await page.getByRole('button', { name: '发布到班级' }).click()
  await expect(page).toHaveURL(/\/publish-plan\/\d+\/step\/1\/$/)
  await capture(page, testInfo, '03-class-schedule.png')

  await page.locator('[id^="publish-class-"]').first().check()
  await page.getByRole('button', { name: '修改时间' }).first().click()
  await page.getByLabel('本班排期已配置').first().check()
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
