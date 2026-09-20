import { expect, test } from '@playwright/test'


async function login(page: import('@playwright/test').Page) {
  await page.goto('/login/', { waitUntil: 'commit' })
  await page.getByLabel('账号或学号').fill('teacher')
  await page.getByLabel('密码').fill('teacher123')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL(/\/teacher\/courses\/$/)
}


test('research export v2 scope is usable on desktop, mobile and 200 percent zoom', async ({ page }, info) => {
  await login(page)
  const path = '/teacher/courses/1/data/exports/?scope_version=2&experiment_id=1&class_mode=single&class_id=1'

  for (const viewport of [
    { name: 'desktop', width: 1440, height: 900 },
    { name: 'mobile', width: 390, height: 844 },
  ]) {
    await page.setViewportSize({ width: viewport.width, height: viewport.height })
    await page.goto(path)
    await page.waitForLoadState('networkidle')
    await expect(page.getByRole('heading', { name: '导出研究数据' })).toBeVisible()
    await expect(page.getByTestId('class-mode')).toHaveValue('single')
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
  await page.goto(path)
  await page.waitForLoadState('networkidle')
  await expect(page.getByTestId('class-mode')).toBeVisible()
  await expect(page.getByRole('button', { name: '导出实验数据包' })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
  await page.screenshot({
    path: info.outputPath('research-export-v2-zoom-200.png'),
    fullPage: true,
  })
})
