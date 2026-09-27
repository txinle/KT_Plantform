import { expect, test, type Page } from '@playwright/test'


async function login(page: import('@playwright/test').Page) {
  await page.goto('/login/', { waitUntil: 'commit' })
  await page.getByLabel('账号或学号').fill('teacher')
  await page.getByLabel('密码').fill('teacher123')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL(/\/teacher\/courses\/$/)
}

async function openSingleClassExport(page: Page, path: string) {
  const catalogResponse = page.waitForResponse(response => (
    new URL(response.url()).pathname === '/api/v1/teacher/courses/1/data/exports/'
    && response.request().method() === 'GET'
  ))
  await page.goto(path)
  const response = await catalogResponse
  expect(response.status()).toBe(200)
  expect((await response.json()).scope.values).toMatchObject({
    experiment_id: 1, class_mode: 'single', class_id: 1,
  })
  const scope = page.getByRole('region', { name: '当前数据范围', exact: true })
  await expect(scope).toContainText('认知追踪实验班')
  await scope.locator('summary').click()
  await expect(scope.locator('input[name="experiment_id"][value="1"]')).toBeChecked()
  await expect(scope.locator('select[name="export_class_range"]')).toHaveValue('1')
  await expect(scope.locator('select[name="export_class_range"]')).toBeVisible()
  await scope.locator('summary').click()
}


test('research export v2 scope is usable on desktop, mobile and 200 percent zoom', async ({ page }, info) => {
  await login(page)
  const path = '/teacher/courses/1/data/exports/?scope_version=2&experiment_id=1&class_mode=single&class_id=1'

  for (const viewport of [
    { name: 'desktop', width: 1440, height: 900 },
    { name: 'mobile', width: 390, height: 844 },
  ]) {
    await page.setViewportSize({ width: viewport.width, height: viewport.height })
    await openSingleClassExport(page, path)
    await expect(page.getByRole('heading', { name: '导出研究数据' })).toBeVisible()
    await expect(page.getByTestId('export-table')).toHaveCount(8)
    await expect(page.getByText('表2覆盖当前实验全部已发布班级，并包含班级列')).toBeVisible()
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),
      `${viewport.name} has horizontal overflow`,
    ).toBe(true)
    await page.screenshot({
      path: info.outputPath(`research-export-v2-${viewport.name}.png`),
      fullPage: true,
    })
  }

  // A 1440px display at 200% browser zoom exposes a 720px CSS viewport.
  await page.setViewportSize({ width: 720, height: 450 })
  await openSingleClassExport(page, path)
  await expect(page.getByRole('button', { name: '导出实验数据包' })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
  await page.screenshot({
    path: info.outputPath('research-export-v2-zoom-200.png'),
    fullPage: true,
  })
})
