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
    const tab = status === 'archived' ? 'archived-kcs' : 'course-kcs'
    await page.goto(`/teacher/courses/1/banks/?tab=${tab}&concept_id=${created.id}&q=${encodeURIComponent(name)}`)
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

test('adapts the KC page size and finds a later-page KC by name or code', async ({ page }, testInfo) => {
  await page.goto('/login/')
  await page.getByLabel('账号或学号', { exact: true }).fill('teacher')
  await page.getByLabel('密码', { exact: true }).fill('teacher123')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL(/\/teacher\/courses\/$/)
  const url = '/api/v1/teacher/courses/1/concepts/'
  const before = (await (await page.request.get(`${url}?all=1&status=all`)).json()).data
  const highest = Math.max(0, ...before.items.map((item: { kc_code: string }) => Number(item.kc_code.slice(2))))
  const headers = { 'X-CSRFToken': (await (await page.request.get('/api/v1/auth/csrf/')).json()).data.csrf_token }
  const created: Array<{ id: number; name: string; kc_code: string }> = []
  const prefix = `课堂听力材料中的关键信息识别与推断${Date.now() % 10000}`
  try {
    const required = before.summary.active <= 10 ? 20 - before.summary.active : 11
    for (let n = 1; n <= required; n++) {
      const response = await page.request.post(url, { headers, data: { sequence: highest + n, name: `${prefix}-${n}`, description: '' } })
      expect(response.status()).toBe(201)
      created.push((await response.json()).data)
    }
    const target = created.at(-1)!
    const pageCount = Math.ceil((before.summary.active + required) / 5)
    await page.setViewportSize({ width: 1366, height: 768 })
    await page.goto('/teacher/courses/1/banks/?tab=course-kcs')
    const rows = page.locator('.kt-qb-kc-list tbody tr')
    const pager = page.getByRole('navigation', { name: '分页' })
    await expect(rows).toHaveCount(5)
    await expect(pager).toContainText(`第 1/${pageCount} 页`)
    for (const [width, height, size] of [[1366, 768, 5], [1440, 900, 7], [1920, 1080, 11]]) {
      await page.setViewportSize({ width, height })
      const pages = Math.ceil((before.summary.active + required) / size)
      await expect(pager).toContainText(`第 1/${pages} 页`)
      const fullPageWithNewKCs = Math.max(1, Math.floor((before.summary.active + required) / size))
      for (let n = 1; n < fullPageWithNewKCs; n++) {
        await pager.getByRole('button', { name: '下一页' }).click()
        await expect(pager).toContainText(`第 ${n + 1}/${pages} 页`)
      }
      await expect(rows).toHaveCount(size)
      const form = page.locator('[data-create-concept]')
      const sequenceBox = await form.locator('[name="sequence"]').boundingBox()
      const codeBox = await form.locator('input[readonly]').boundingBox()
      expect(codeBox!.width).toBeCloseTo(sequenceBox!.width, 0)
      await expect(rows.filter({ hasText: prefix }).first()).toBeVisible()
      const bounds = await page.evaluate(() => {
        const bottom = (selector: string) => Math.round(document.querySelector(selector)!.getBoundingClientRect().bottom)
        return { rowsBottom: bottom('.kt-qb-kc-list tbody tr:last-child'), paginationBottom: bottom('.kt-qb-kc-list .kt-paginator'),
          listBottom: bottom('.kt-qb-kc-list'), sidebarBottom: bottom('#workspace-sidebar'),
          importBottom: bottom('[data-course-kc-import]'), documentHeight: document.documentElement.scrollHeight,
          documentWidth: document.documentElement.scrollWidth }
      })
      await page.screenshot({ path: testInfo.outputPath(`kc-list-${width}.png`) })
      console.log('KC layout', width, height, size, JSON.stringify(bounds))
      expect(bounds.documentWidth).toBeLessThanOrEqual(width + 1)
      expect(bounds.documentHeight).toBeLessThanOrEqual(height + 1)
      expect(Math.max(bounds.rowsBottom, bounds.paginationBottom, bounds.importBottom)).toBeLessThanOrEqual(height)
      expect(bounds.sidebarBottom - bounds.listBottom).toBe(24)
      expect(bounds.importBottom).toBe(bounds.listBottom)
    }
    await page.setViewportSize({ width: 1366, height: 768 })
    await expect(pager).toContainText(`第 1/${pageCount} 页`)
    await pager.getByRole('button', { name: '下一页' }).click()
    await expect(pager).toContainText(`第 2/${pageCount} 页`)
    await expect(rows).toHaveCount(5)
    const search = page.getByRole('combobox', { name: '按 KC 名称或编码搜索' })
    for (const value of [target.name, target.kc_code.toLowerCase()]) {
      await search.fill(value)
      const option = page.getByRole('option').filter({ hasText: target.name })
      await expect(option).toBeVisible()
      await search.press('ArrowDown')
      await search.press('Enter')
      await expect(rows).toHaveCount(1)
      await expect(rows.first()).toContainText(target.name)
      await page.getByRole('button', { name: '清除 KC 搜索' }).click()
      await expect(rows).toHaveCount(5)
      await expect(pager).toContainText(`第 1/${pageCount} 页`)
    }
    await page.locator('[data-archived-kcs]').click()
    await search.fill(target.name)
    await expect(page.getByRole('status').filter({ hasText: '没有匹配的课程 KC' })).toBeVisible()
  } finally {
    for (const item of created) {
      expect((await page.request.delete(`${url}${item.id}/`, { headers })).status()).toBe(204)
    }
  }
})
