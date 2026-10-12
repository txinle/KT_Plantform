import { expect, test } from '@playwright/test'

test('published class timing controls remain usable on desktop, mobile and 200% effective viewport', async ({ page }, info) => {
  await page.route('**/api/v1/teacher/experiments/1/class-tasks/', async (route) => {
    const response = await route.fetch()
    const envelope = await response.json()
    const payload = envelope.data
    await route.fulfill({
      response,
      json: {
        ...envelope,
        data: { ...payload, items: payload.items.map((task: Record<string, unknown>, index: number) => index === 0
          ? { ...task, closes_at: '2026-10-03T00:00:00Z', total_time_limit_minutes: 15 }
          : task) },
      },
    })
  })
  await page.goto('/login/')
  await page.getByLabel('账号或学号').fill('teacher')
  await page.getByLabel('密码').fill('teacher123')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL(/\/teacher\/courses\//)
  await page.goto('/teacher/courses/1/experiments/1/')
  await expect(page.locator('[data-action="adjust-time"]').first()).toBeVisible()
  await page.locator('[data-action="adjust-time"]').first().click()
  const form = page.locator('.kt-inline-panel').filter({ hasText: '调整该班时间' })
  await expect(form.getByLabel(/延长本班实验截止时间/)).toBeVisible()
  await expect(form.getByLabel(/学生作答时间延长/)).toBeVisible()
  await form.getByLabel(/学生作答时间延长/).fill('10')
  await expect(form.getByText('调整后：25 分钟')).toBeVisible()
  await page.keyboard.press('Tab')
  await expect(form.getByLabel('调整原因')).toBeFocused()
  await expect(form.getByRole('button', { name: '保存调整' })).toBeVisible()

  for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }, { width: 720, height: 900 }]) {
    await page.setViewportSize(viewport)
    await form.scrollIntoViewIfNeeded()
    await expect(form.getByLabel(/延长本班实验截止时间/)).toBeVisible()
    await expect(form.getByLabel(/学生作答时间延长/)).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
    const panel = await form.boundingBox()
    expect(panel?.width).toBeLessThanOrEqual(viewport.width)
    await page.screenshot({ path: info.outputPath(`adjust-time-${viewport.width === 720 ? 'zoom-200-equivalent' : viewport.width}.png`), fullPage: true })
  }
})
