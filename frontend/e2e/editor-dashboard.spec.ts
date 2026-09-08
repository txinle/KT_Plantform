import { expect, test } from '@playwright/test'
import { mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { openLoginPage } from './login-navigation'

async function login(page: import('@playwright/test').Page) {
  await openLoginPage(page)
  await page.getByLabel('账号或学号').fill('teacher')
  await page.getByLabel('密码').fill('teacher123')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.locator('.kt-workspace')).toBeVisible()
}

test('compact option pairs preserve reading order at desktop and mobile widths', async ({ page }, info) => {
  await login(page)
  await page.goto('/teacher/courses/1/banks/1/questions/1/edit/')
  await expect(page.locator('#option-a')).toBeVisible()
  await page.screenshot({ path: info.outputPath('editor-default-1440.png'), fullPage: true })
  if (!await page.locator('#option-e').isVisible()) await page.getByText('显示更多选项（E、F）', { exact: true }).click()
  const fieldOrder = await page.locator('.kt-question-editor input, .kt-question-editor select, .kt-question-editor textarea').evaluateAll(nodes => nodes.map(n => n.id))
  expect(fieldOrder.indexOf('question-type')).toBeLessThan(fieldOrder.indexOf('question-text'))
  expect(fieldOrder.indexOf('option-f')).toBeLessThan(fieldOrder.indexOf('question-answer'))
  expect(fieldOrder.indexOf('question-answer')).toBeLessThan(fieldOrder.indexOf('question-difficulty'))
  for (const width of [1440, 1024, 768, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 900 })
    await page.evaluate(() => window.scrollTo(0, 0))
    await page.screenshot({ path: info.outputPath(`editor-${width}.png`), fullPage: true })
    const boxes = await page.locator('[id^="option-"]').evaluateAll(nodes => nodes.map(n => {
      const r = n.getBoundingClientRect()
      return { id: n.id, x: r.x, y: r.y, width: r.width }
    }))
    expect(boxes.map(b => b.id)).toEqual(['option-a', 'option-b', 'option-c', 'option-d', 'option-e', 'option-f'])
    for (const i of [0, 2, 4]) {
      if (width >= 768) {
        expect(Math.abs(boxes[i]!.y - boxes[i + 1]!.y)).toBeLessThan(2)
        expect(boxes[i + 1]!.x).toBeGreaterThan(boxes[i]!.x + boxes[i]!.width)
      } else expect(boxes[i + 1]!.y).toBeGreaterThan(boxes[i]!.y)
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
  }
})

test('class charts show empty, partial and completed distributions at supported widths', async ({ page }, info) => {
  await login(page)
  let scenario = 'original'
  await page.route('**/api/v1/teacher/courses/1/classes/1/center/**', async route => {
    const response = await route.fetch()
    const body = await response.json()
    if (scenario !== 'original') {
      const empty = scenario === 'empty'
      const completed = scenario === 'completed'
      const metric = (n: number, d: number) => ({ numerator: String(n), denominator: String(d), ratio: d ? String(n / d) : null, state: d ? 'available' : 'no_data', sample_size: d, percent_display: d ? `${(n / d * 100).toFixed(1)}%` : '—' })
      body.overview = { roster_count: empty ? 0 : 5, participated_count: empty ? 0 : completed ? 5 : 3, correct_rate: metric(completed ? 10 : 2, empty ? 0 : 10), completion_rate: metric(completed ? 10 : 4, empty ? 0 : 10) }
      body.participation = { completed: empty ? 0 : completed ? 5 : 2, in_progress: empty || completed ? 0 : 1, not_participated: empty || completed ? 0 : 2 }
      body.correct_rate_distribution = [{ key: 'lt_60', label: '<60%', count: empty || completed ? 0 : 2 }, { key: '90_100', label: '90–100%', count: completed ? 5 : 0 }, { key: 'no_data', label: '无数据', count: empty || completed ? 0 : 3 }]
      body.kc_rows = empty ? [] : [{ name: '包含长名称的知识点ABCDEFGHIJKLMNOPQRSTUVWXYZ'.repeat(3), correct_rate: metric(3, 4) }, { name: '待评分知识点', correct_rate: metric(0, 0) }]
    }
    await route.fulfill({ response, json: body })
  })
  for (scenario of ['original', 'partial', 'completed', 'empty']) {
    await page.goto('/teacher/courses/1/classes/1/')
    await expect(page.locator('.kt-class-dashboard')).toBeVisible()
    for (const width of [1672, 1440, 1024, 768, 390]) {
      await page.setViewportSize({ width, height: width === 390 ? 844 : 900 })
      await page.evaluate(() => window.scrollTo(0, 0))
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
      await page.screenshot({ path: info.outputPath(`class-${scenario}-${width}.png`), fullPage: true })
    }
    if (scenario === 'partial') {
      await expect(page.locator('[data-completion-units]')).toContainText('4 / 10')
      await expect(page.locator('[data-bin="no_data"]')).toContainText('60.0%')
    }
    if (scenario === 'empty') await expect(page.locator('[data-distribution-ring] [data-sector]')).toHaveCount(0)
  }
})

test('actual browser 200 percent zoom keeps the editor and class charts usable', async ({ playwright }, info) => {
  const extension = info.outputPath('zoom-extension')
  mkdirSync(extension, { recursive: true })
  writeFileSync(join(extension, 'manifest.json'), JSON.stringify({ manifest_version: 3, name: 'Isolated layout zoom check', version: '1.0', permissions: ['tabs'], background: { service_worker: 'worker.js' } }))
  writeFileSync(join(extension, 'worker.js'), 'chrome.runtime.onInstalled.addListener(() => {});')
  const context = await playwright.chromium.launchPersistentContext(info.outputPath('zoom-profile'), { channel: 'chromium', headless: true, viewport: null, args: [`--disable-extensions-except=${extension}`, `--load-extension=${extension}`, '--window-size=1440,900'] })
  try {
    const worker = context.serviceWorkers()[0] || await context.waitForEvent('serviceworker')
    const page = await context.newPage()
    const cdp = await context.newCDPSession(page)
    const screenshot = async (name: string) => {
      const capture = await cdp.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false })
      writeFileSync(info.outputPath(name), Buffer.from(capture.data, 'base64'))
    }
    await page.goto('http://127.0.0.1:8000/login/')
    await page.getByLabel('账号或学号').fill('teacher')
    await page.getByLabel('密码').fill('teacher123')
    await page.getByRole('button', { name: '登录', exact: true }).click()
    await expect(page.locator('.kt-workspace')).toBeVisible()
    const zoom = await worker.evaluate(`(async () => { const tabs = await chrome.tabs.query({}); const tab = tabs.find(t => t.url?.startsWith('http://127.0.0.1:8000/')); await chrome.tabs.setZoom(tab.id, 2); return await chrome.tabs.getZoom(tab.id); })()`)
    expect(zoom).toBe(2)
    for (const [label, path] of [['editor', '/teacher/courses/1/banks/1/questions/1/edit/'], ['class', '/teacher/courses/1/classes/1/']]) {
      await page.goto(`http://127.0.0.1:8000${path}`)
      await expect(page.locator(label === 'editor' ? '.kt-kc-editor' : '.kt-class-dashboard')).toBeVisible()
      expect(await page.evaluate(() => innerWidth)).toBeLessThan(800)
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
      await screenshot(`${label}-actual-200.png`)
      await page.locator(label === 'editor' ? '.kt-kc-editor' : '.kt-dashboard-analysis').scrollIntoViewIfNeeded()
      await screenshot(`${label}-actual-200-lower.png`)
    }
  } finally { await context.close() }
})

test('KC tree preserves parent links through editing and server save', async ({ page }, info) => {
  await login(page)
  await page.goto('/teacher/courses/1/banks/1/questions/1/edit/')
  await page.locator('[data-kc-toggle]').click()
  await page.locator('[data-clear]').click()
  await page.locator('[data-edit-mapping]').click()
  await page.locator('#kc-root').selectOption('KC1')
  await page.locator('[data-code="KC1"] [data-add]').click()
  await page.locator('[data-level="2"] [data-add]').click()
  await expect(page.locator('[data-level="3"]')).toBeVisible()
  for (const width of [1704, 1440, 1024, 768, 390]) {
    await page.setViewportSize({ width, height: 900 })
    const root = await page.locator('[data-code="KC1"]').boundingBox()
    const child = await page.locator('[data-level="2"]').boundingBox()
    if (width >= 1024) expect(child!.x).toBeGreaterThan(root!.x + root!.width)
    else expect(child!.y).toBeGreaterThan(root!.y + root!.height)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
    await page.locator('.kt-kc-dialog').screenshot({ path: info.outputPath(`kc-tree-${width}.png`) })
  }
  await page.setViewportSize({ width: 1440, height: 900 })
  const incoming = page.locator('.kt-kc-connectors path').first()
  const initialPath = await incoming.getAttribute('d')
  await page.locator('[data-level="2"] summary').click()
  await expect(incoming).not.toHaveAttribute('d', initialPath!)
  await page.locator('[data-level="2"] summary').click()
  await page.locator('[data-code="KC1"] [data-add]').click()
  await expect(page.locator('[data-level="2"]')).toHaveCount(2)
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 900 })
    await page.locator('.kt-kc-dialog').screenshot({ path: info.outputPath(`kc-multiple-${width}.png`) })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
  }
  await page.locator('[data-code="KC1"] [data-add]').click()
  await page.locator('[data-code="KC1"] [data-add]').click()
  await expect(page.locator('.kt-kc-editor')).toHaveClass(/is-dense/)
  await expect(page.locator('.kt-kc-connectors path')).toHaveCount(5)
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 900 })
    await page.locator('.kt-kc-dialog').screenshot({ path: info.outputPath(`kc-dense-${width}.png`) })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
  }
  await page.locator('[data-modal-close]').click()
  const saved = page.waitForResponse(r => r.request().method() === 'PUT' && r.url().includes('/questions/1/editor/'))
  await page.getByRole('button', { name: '保存题目', exact: true }).click()
  expect((await saved).ok()).toBe(true)
  await expect(page).toHaveURL(/\/banks\/1\/$/)
  await page.goto('/teacher/courses/1/banks/1/questions/1/edit/')
  await page.locator('[data-preview]').click()
  await expect(page.locator('[data-level="3"]')).toBeVisible()
})



test('collapsed summaries and modal preview preserve focus and form values', async ({ page }, info) => {
  await login(page)
  await page.goto('/teacher/courses/1/banks/1/questions/1/edit/')
  await expect(page.locator('[data-answer-toggle]')).toHaveAttribute('aria-expanded', 'false')
  await expect(page.locator('#question-answer')).toBeHidden()
  await expect(page.locator('[data-kc-toggle]')).toHaveAttribute('aria-expanded', 'false')
  await page.screenshot({ path: info.outputPath('collapsed-1440.png'), fullPage: true })
  await page.locator('[data-preview]').click()
  const dialog = page.getByRole('dialog', { name: '结构图预览' })
  await expect(dialog).toBeVisible()
  await expect(dialog.locator('select')).toHaveCount(0)
  await page.screenshot({ path: info.outputPath('preview-1440.png') })
  await page.keyboard.press('Escape')
  await expect(dialog).toBeHidden()
  await expect(page.locator('[data-preview]')).toBeFocused()
  await page.locator('[data-answer-toggle]').click()
  await page.locator('#question-answer').fill('C')
  await page.locator('[data-answer-toggle]').click()
  await expect(page.locator('[data-answer-toggle]')).toContainText('正确答案 C')
  await page.setViewportSize({ width: 390, height: 844 })
  await page.screenshot({ path: info.outputPath('collapsed-390.png'), fullPage: true })
  await page.locator('[data-preview]').click()
  await expect(dialog).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
  await page.screenshot({ path: info.outputPath('preview-390.png') })
  await page.getByRole('button', { name: '关闭', exact: true }).click()
  await expect(page.locator('[data-preview]')).toBeFocused()
})
