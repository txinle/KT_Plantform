import { expect, test } from '@playwright/test'

async function login(page: import('@playwright/test').Page) {
  await page.goto('/login/')
  await page.getByLabel('账号或学号').fill('teacher')
  await page.getByLabel('密码').fill('teacher123')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.locator('.kt-workspace')).toBeVisible()
}

test('question import preview keeps an intentionally wide table inside its own scroller', async ({ page }, info) => {
  await login(page)
  await page.route('**/api/v1/teacher/courses/1/banks/1/imports/questions/preview/', route => route.fulfill({
    json: {
      preview_id: 'layout-preview',
      preview: {
        rows: [{
          sequence: 1,
          text: '超长连续题干标识ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'.repeat(8),
          question_type: 'short_answer',
          question_type_label: '简答题',
          option_count: 0,
          teacher_difficulty: '0.75',
        }],
      },
    },
  }))
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/teacher/courses/1/banks/1/import/')
  await expect(page.getByRole('heading', { name: '导入题目文件' })).toBeVisible()
  await page.locator('input[type="file"]').setInputFiles({ name: 'layout.csv', mimeType: 'text/csv', buffer: Buffer.from('sequence,text\\n1,test') })
  const preview = page.getByRole('dialog', { name: '题目导入预览' })
  await expect(preview).toBeVisible()
  const geometry = await preview.locator('.kt-table-scroll').evaluate((scroller) => ({
    pageOverflow: document.documentElement.scrollWidth > window.innerWidth + 1,
    localOverflow: scroller.scrollWidth > scroller.clientWidth + 1,
  }))
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.screenshot({ path: info.outputPath('question-import-preview-390-before.png'), fullPage: true })
  expect(geometry.pageOverflow, 'the page must not scroll horizontally').toBe(false)
  expect(geometry.localOverflow, 'the preview table must use its local scroller for wide data').toBe(true)
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
    json: { detail: '第 1 行的题目编号与当前题库中已冻结的题目快照发生冲突：请更换为未使用的稳定题目编号。'.repeat(5) },
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
  await page.goto('/teacher/courses/1/experiments/1/edit/')
  await expect(page.getByRole('heading', { name: '编辑实验定义' })).toBeVisible()
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
