import { expect, test } from '@playwright/test'

test('published class timing controls remain usable on desktop, mobile and 200% effective viewport', async ({ page }, info) => {
  await page.goto('/login/')
  await page.getByLabel('账号或学号').fill('teacher')
  await page.getByLabel('密码').fill('teacher123')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL(/\/teacher\/courses\//)
  await page.goto('/teacher/courses/1/experiments/1/')
  await expect(page.locator('[data-action="adjust-time"]')).toBeVisible()
  await page.locator('[data-action="adjust-time"]').click()
  const form = page.locator('.kt-inline-panel').filter({ hasText: '调整该班时间' })
  await expect(form.getByLabel('新截止时间')).toBeVisible()
  await expect(form.getByRole('button', { name: '保存调整' })).toBeVisible()

  for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }, { width: 720, height: 900 }]) {
    await page.setViewportSize(viewport)
    await form.scrollIntoViewIfNeeded()
    await expect(form.getByLabel('新截止时间')).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
    const panel = await form.boundingBox()
    expect(panel?.width).toBeLessThanOrEqual(viewport.width)
    await page.screenshot({ path: info.outputPath(`adjust-time-${viewport.width === 720 ? 'zoom-200-equivalent' : viewport.width}.png`), fullPage: true })
  }
})
