import { expect, test } from '@playwright/test'
import { openLoginPage } from './login-navigation'

async function login(page: import('@playwright/test').Page) {
  await openLoginPage(page)
  await page.getByLabel('账号或学号').fill('teacher')
  await page.getByLabel('密码').fill('teacher123')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.locator('.kt-workspace')).toBeVisible()
}

test('question import uses mobile cards and keeps its desktop table inside a local scroller', async ({ page }, info) => {
  await login(page)
  await page.route('**/api/v1/teacher/courses/1/banks/1/imports/questions/preview/', route => route.fulfill({
    json: {
      meta: { request_id: 'layout-preview' },
      data: {
        preview_id: 'layout-preview',
        preview: {
          rows: [{
            row_id: 'layout-row',
            sequence: 1,
            source_label: 'CSV 第 2 行',
            text: '超长连续题干标识ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'.repeat(8),
            question_type: 'short_answer',
            question_type_label: '简答题',
            option_count: 0,
            teacher_difficulty: '0.75',
            options: [],
            answer: '用于布局验证的完整答案',
            accepted_answers: [],
            explanation: '用于验证完整内容和 KC 映射不会挤出页面。',
            kc_mapping: {
              root_kc_code: 'KC001',
              nodes: [{ kc_code: 'KC001', name: '超长连续知识点ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'.repeat(3), parent_kc_code: null, order: 1 }],
            },
            classification: 'ready',
            errors: [],
          }],
          summary: { ready: 1, duplicate: 0, invalid: 0 },
          confirmable: true,
        },
      },
    },
  }))
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/teacher/courses/1/banks/1/import/')
  await expect(page.getByRole('heading', { name: '导入题库', exact: true })).toBeVisible()
  await page.locator('input[type="file"]').setInputFiles({ name: 'layout.csv', mimeType: 'text/csv', buffer: Buffer.from('sequence,text\\n1,test') })
  const preview = page.locator('[data-import-preview]')
  await expect(preview).toBeVisible()
  await expect(page.locator('[data-import-mobile-row="layout-row"]')).toBeVisible()
  const mobileGeometry = await page.evaluate(() => ({
    pageOverflow: document.documentElement.scrollWidth > window.innerWidth + 1,
  }))
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.screenshot({ path: info.outputPath('question-import-preview-mobile-390.png'), fullPage: true })
  expect(mobileGeometry.pageOverflow, 'the mobile cards must not scroll the page horizontally').toBe(false)

  await page.locator('[data-import-mobile-row="layout-row"] button').click()
  const detail = page.locator('[data-question-detail]')
  await expect(detail.getByText('超长连续知识点ABCDEFGHIJKLMNOPQRSTUVWXYZ', { exact: false }).first()).toBeVisible()
  const expandedMobileGeometry = await page.evaluate(() => ({
    pageOverflow: document.documentElement.scrollWidth > window.innerWidth + 1,
  }))
  expect(expandedMobileGeometry.pageOverflow, 'the expanded KC mapping must not scroll the phone page horizontally').toBe(false)

  await page.setViewportSize({ width: 720, height: 450 })
  await expect(detail).toBeVisible()
  const zoomGeometry = await page.evaluate(() => ({
    pageOverflow: document.documentElement.scrollWidth > window.innerWidth + 1,
  }))
  await page.screenshot({ path: info.outputPath('question-import-preview-zoom-200-720.png'), fullPage: true })
  expect(zoomGeometry.pageOverflow, 'the 200 percent equivalent view must not scroll the page horizontally').toBe(false)

  await detail.getByRole('button', { name: '关闭题目详情' }).click()
  await page.setViewportSize({ width: 769, height: 844 })
  const table = preview.locator('.kt-table-scroll')
  await expect(table).toBeVisible()
  const desktopGeometry = await table.evaluate((scroller) => ({
    pageOverflow: document.documentElement.scrollWidth > window.innerWidth + 1,
    localOverflow: scroller.scrollWidth > scroller.clientWidth + 1,
  }))
  await page.screenshot({ path: info.outputPath('question-import-preview-table-769.png'), fullPage: true })
  expect(desktopGeometry.pageOverflow, 'the desktop preview must not scroll the page horizontally').toBe(false)
  expect(desktopGeometry.localOverflow, 'the desktop preview table must use its local scroller for wide data').toBe(true)
})

async function captureNoPageOverflow(page: import('@playwright/test').Page, info: import('@playwright/test').TestInfo, name: string) {
  const geometry = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    viewportWidth: window.innerWidth,
  }))
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.screenshot({ path: info.outputPath(`${name}.png`), fullPage: true })
  expect(geometry.scrollWidth, `${name} must not cause document-level horizontal overflow`).toBeLessThanOrEqual(geometry.viewportWidth + 1)
}

test('long editable question content and validation errors stay within desktop, tablet, and phone layouts', async ({ page }, info) => {
  test.setTimeout(120_000)
  await login(page)
  await page.goto('/teacher/courses/1/banks/1/questions/1/edit/')
  await expect(page.locator('#question-text')).toBeVisible()
  await page.locator('#question-type').selectOption('single_choice')
  const longText = '连续英文标识ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'.repeat(10)
  await page.locator('#question-text').fill(longText)
  await page.locator('[data-answer-toggle]').click()
  await page.locator('#question-answer').fill(longText)
  if (!await page.locator('#option-e').isVisible()) await page.getByText('显示更多选项（E、F）', { exact: true }).click()
  for (const input of await page.locator('[id^="option-"]').all()) await input.fill(longText)
  for (const size of [{ width: 1440, height: 900 }, { width: 1024, height: 900 }, { width: 768, height: 900 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(size)
    await captureNoPageOverflow(page, info, `question-editor-long-content-${size.width}`)
  }
  await page.getByRole('button', { name: '查看结构图', exact: true }).click()
  await expect(page.getByRole('dialog', { name: '结构图预览' })).toBeVisible()
  expect(await page.locator('.kt-kc-editor').evaluate(node => node.scrollWidth <= node.clientWidth + 1)).toBe(true)

  await page.route('**/api/v1/teacher/courses/1/banks/1/imports/questions/preview/', route => route.fulfill({
    status: 400,
    json: { detail: '第 1 行的 KC“未知知识点”尚未在课程知识点总库中创建，请先修正完整文件后重新上传。'.repeat(5) },
  }))
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/teacher/courses/1/banks/1/import/')
  await page.locator('input[type="file"]').setInputFiles({ name: 'invalid.csv', mimeType: 'text/csv', buffer: Buffer.from('sequence,text\\n1,test') })
  await expect(page.getByRole('alert')).toBeVisible()
  await captureNoPageOverflow(page, info, 'question-import-validation-error-390')
})

test('nested question-bank navigation and detail tables preserve local scrolling at responsive boundaries', async ({ page }, info) => {
  test.setTimeout(120_000)
  await login(page)
  await page.goto('/teacher/courses/1/banks/')
  await expect(page.getByRole('tab', { name: /题库管理/ })).toBeVisible()
  await page.evaluate(() => {
    const tab = document.querySelector<HTMLElement>('#question-bank-tab-course-kcs')
    if (tab) tab.textContent = '课程知识点总库名称ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'.repeat(4)
  })
  await page.setViewportSize({ width: 1440, height: 900 })
  await captureNoPageOverflow(page, info, 'question-bank-nested-tabs-1440')
  await page.setViewportSize({ width: 390, height: 844 })
  await page.locator('[data-workspace-menu]').click()
  await expect(page.locator('.kt-workspace-sidebar')).toHaveAttribute('aria-modal', 'true')
  await captureNoPageOverflow(page, info, 'question-bank-nested-tabs-390')
  await page.keyboard.press('Escape')

  await page.setViewportSize({ width: 1024, height: 900 })
  await page.goto('/teacher/courses/1/banks/1/')
  const table = page.locator('.kt-bank-question-section .kt-table-scroll').first()
  await expect(table).toBeVisible()
  const tableGeometry = await table.evaluate((scroller) => ({
    pageOverflow: document.documentElement.scrollWidth > window.innerWidth + 1,
    localOverflow: scroller.scrollWidth > scroller.clientWidth + 1,
  }))
  await captureNoPageOverflow(page, info, 'question-bank-detail-tablet-table')
  expect(tableGeometry.pageOverflow).toBe(false)
  expect(tableGeometry.localOverflow).toBe(true)

  await page.setViewportSize({ width: 390, height: 844 })
  await expect(page.locator('.kt-mobile-question-card').first()).toBeVisible()
  await captureNoPageOverflow(page, info, 'question-bank-detail-phone-cards')
})

test('experiment selection and expanded research tools retain all inputs within the workspace', async ({ page }, info) => {
  test.setTimeout(120_000)
  await login(page)
  await page.goto('/teacher/courses/1/experiments/create/')
  await expect(page.getByRole('heading', { name: '创建实验' })).toBeVisible()
  await page.locator('[data-type-picker]').click()
  await expect(page.locator('.kt-type-tree-selector')).toBeVisible()
  await page.getByText('可选研究工具', { exact: true }).click()
  await page.getByRole('button', { name: '添加固定提示', exact: true }).click()
  await page.getByRole('button', { name: '添加出现规则', exact: true }).click()
  const longCode = 'QUESTION-CODE-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'.repeat(6)
  await page.getByLabel('题目编号', { exact: true }).first().fill(longCode)
  await page.getByLabel('提示正文', { exact: true }).fill(longCode)
  for (const size of [{ width: 1440, height: 900 }, { width: 1024, height: 900 }, { width: 768, height: 900 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(size)
    await captureNoPageOverflow(page, info, `experiment-expanded-tools-${size.width}`)
  }
})
