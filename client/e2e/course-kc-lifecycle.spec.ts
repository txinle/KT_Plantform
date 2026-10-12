import { expect, test } from '@playwright/test'

test('creates, archives and restores a course KC while retaining its identity', async ({ page }, testInfo) => {
  await page.goto('/login/', { waitUntil: 'commit' })
  await page.getByLabel('账号或学号', { exact: true }).fill('teacher')
  await page.getByLabel('密码', { exact: true }).fill('teacher123')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL(/\/teacher\/courses\/$/)
  const url = '/api/v1/teacher/courses/1/concepts/'
  const before = (await (await page.request.get(`${url}?all=1`)).json()).data
  const sequence = Math.max(...before.items.map((row: { kc_code: string }) => Number(row.kc_code.slice(2)))) + 1
  const name = `UAT-KC-RESTORE-${Date.now()}`
  await page.goto('/teacher/courses/1/banks/?tab=course-kcs', { waitUntil: 'commit' })
  await page.locator('[data-create-concept] input[name="sequence"]').fill(String(sequence))
  await page.locator('[data-create-concept] input[name="name"]').fill(name)
  const createdResponse = page.waitForResponse((response) => response.url().endsWith(url) && response.request().method() === 'POST')
  await page.locator('[data-create-concept] button[type="submit"]').click()
  const response = await createdResponse
  expect(response.status()).toBe(201)
  const created = (await response.json()).data
  expect(created.kc_code).toBe(`KC${sequence}`)
  for (const [layout, width, height] of [['desktop', 1440, 900], ['mobile', 390, 844], ['zoom200-equivalent', 720, 450]] as const) {
    await page.setViewportSize({ width, height })
    const row = page.locator(layout === 'desktop' ? '.kt-qb-desktop-table tr' : '.kt-qb-mobile-list li').filter({ hasText: name })
    await expect(row.locator('[data-concept-archive]')).toBeVisible()
    await row.locator('[data-concept-archive]').click()
    await expect(row.locator('[data-concept-restore]')).toBeVisible()
    await expect(row).toContainText('已归档')
    await page.screenshot({ path: testInfo.outputPath(`archived-${layout}.png`), fullPage: true })
    await row.locator('[data-concept-restore]').click()
    await expect(row.locator('[data-concept-archive]')).toBeVisible()
    await expect(row.locator('.kt-qb-badge')).toHaveText('有效')
    const listed = (await (await page.request.get(`${url}?all=1`)).json()).data
    expect(listed.items.find((item: { id: number }) => item.id === created.id)).toMatchObject({ kc_code: created.kc_code, name, status: 'active' })
    expect(listed.summary.total).toBe(before.summary.total + 1)
    expect(listed.summary.archived).toBe(before.summary.archived)
    await page.screenshot({ path: testInfo.outputPath(`restored-${layout}.png`), fullPage: true })
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width + 1)
  }
})
