import { expect, test } from '@playwright/test'

test('creates, archives and restores a course KC while retaining its identity', async ({ page }, testInfo) => {
  test.setTimeout(90_000)
  await page.goto('/login/', { waitUntil: 'commit' })
  await page.getByLabel('账号或学号', { exact: true }).fill('teacher')
  await page.getByLabel('密码', { exact: true }).fill('teacher123')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL(/\/teacher\/courses\/$/)
  const url = '/api/v1/teacher/courses/1/concepts/'
  const before = (await (await page.request.get(`${url}?all=1&status=all`)).json()).data
  const sequence = Math.max(0, ...before.items.map((row: { kc_code: string }) => Number(row.kc_code.slice(2)))) + 1
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
  const listPage = async (status: 'active' | 'archived') => {
    const listed = (await (await page.request.get(`${url}?status=${status}&all=1`)).json()).data
    const index = listed.items.findIndex((item: { id: number }) => item.id === created.id)
    expect(index).toBeGreaterThanOrEqual(0)
    const tab = status === 'archived' ? 'archived-kcs' : 'course-kcs'
    await page.goto(`/teacher/courses/1/banks/?tab=${tab}&page=${Math.floor(index / 20) + 1}`)
  }
  await listPage('active')
  for (const [layout, width, height] of [['desktop', 1440, 900], ['mobile', 390, 844], ['zoom200-equivalent', 720, 450]] as const) {
    await page.setViewportSize({ width, height })
    const row = page.locator(layout === 'desktop' ? '.kt-qb-desktop-table tr' : '.kt-qb-mobile-list li').filter({ hasText: name })
    await expect(row.locator('[data-concept-archive]')).toBeVisible()
    await row.locator('[data-concept-archive]').click()
    await expect(row).toHaveCount(0)
    await page.locator('[data-archived-kcs]').click()
    await expect(page).toHaveURL(/tab=archived-kcs/)
    await page.reload()
    await expect(page.locator('h1')).toContainText('已归档课程 KC')
    await page.goBack()
    await expect(page.locator('[data-archived-kcs]')).toBeVisible()
    await listPage('archived')
    await expect(row.locator('[data-concept-restore]')).toBeVisible()
    await expect(row).toContainText('已归档')
    await page.screenshot({ path: testInfo.outputPath(`archived-${layout}.png`), fullPage: true })
    const restored = page.waitForResponse(r => r.request().method() === 'PATCH' && r.url().endsWith(`/${created.id}/`))
    await row.locator('[data-concept-restore]').click()
    expect((await restored).ok()).toBeTruthy()
    await expect(row).toHaveCount(0)
    await listPage('active')
    await expect(row.locator('[data-concept-archive]')).toBeVisible()
    await expect(row.locator('.kt-qb-badge')).toHaveText('有效')
    const listed = (await (await page.request.get(`${url}?all=1&status=all`)).json()).data
    expect(listed.items.find((item: { id: number }) => item.id === created.id)).toMatchObject({ kc_code: created.kc_code, name, status: 'active' })
    expect(listed.summary.total).toBe(before.summary.total + 1)
    expect(listed.summary.archived).toBe(before.summary.archived)
    await page.screenshot({ path: testInfo.outputPath(`restored-${layout}.png`), fullPage: true })
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width + 1)
  }
  const target = page.locator('.kt-qb-mobile-list li').filter({ hasText: name })
  await target.getByRole('button', { name: '删除', exact: true }).click()
  await page.getByRole('button', { name: '取消', exact: true }).click()
  await expect(target).toBeVisible()
  await target.getByRole('button', { name: '删除', exact: true }).click()
  await page.locator('[data-confirm-concept-delete]').click()
  await expect(page.getByRole('status').filter({ hasText: '已永久删除' })).toContainText(name)
  await expect(target).toHaveCount(0)
  const final = (await (await page.request.get(`${url}?all=1&status=all`)).json()).data
  expect(final.items.map((item: { id: number }) => item.id)).not.toContain(created.id)
})
