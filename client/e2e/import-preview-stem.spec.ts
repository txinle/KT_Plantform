import { expect, test } from '@playwright/test'

test('import preview displays distinct stems despite identical material titles', async ({ page }, info) => {
  await page.goto('/login/')
  await page.getByLabel('账号或学号', { exact: true }).fill('teacher')
  await page.getByLabel('密码', { exact: true }).fill('teacher123')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL(/\/teacher\/courses\/$/)
  const csrf = (await (await page.request.get('/api/v1/auth/csrf/')).json()).data.csrf_token
  const response = await page.request.post('/api/v1/teacher/courses/1/banks/', {
    headers: { 'X-CSRFToken': csrf },
    data: { name: `UAT-IMPORT-STEM-${Date.now()}`, visibility: 'private' },
  })
  expect(response.status()).toBe(201)
  const bank = (await response.json()).data
  await page.goto(`/teacher/courses/1/banks/${bank.id}/import/`)
  await expect(page.getByLabel('目标题库')).toHaveValue(bank.name)
  const stem = 'EDA 的中文全称是什么？'
  const title = 'EDA技术的基本概念与作用'
  const template = await page.request.get('/api/v1/teacher/import-templates/question-groups.csv')
  const columns = (await template.text()).replace(/^\ufeff/, '').split(/\r?\n/)[0]!.split(',')
  const rows: Record<string, string>[] = [
    { 序号: '1', 题目: stem, 题型: '单选题', 选项A: '电子设计自动化', 选项B: '电子数据分析', 答案: 'A', 一级知识点: 'KC1', 材料标题: title },
    { 序号: '2', 题目: 'EDA 工具可以用于逻辑综合吗？', 题型: '判断题', 答案: '正确', 一级知识点: 'KC1', 材料标题: title },
  ]
  const csv = [columns.join(','), ...rows.map(row => columns.map(column => row[column] || '').join(','))].join('\n')
  await page.locator('input[type="file"]').setInputFiles({ name: 'eda.csv', mimeType: 'text/csv', buffer: Buffer.from('\ufeff' + csv) })
  await expect(page.locator('[data-import-row]').first()).toContainText(stem)
  await expect(page.locator('[data-import-row]').first().locator('.qimp-question-text')).not.toContainText(title)
  await expect(page.locator('[data-import-row]').first().locator('[data-question-title]')).toHaveText(title)
  for (const [name, width, height] of [['desktop', 1440, 900], ['mobile', 390, 844], ['zoom', 720, 450]] as const) {
    await page.setViewportSize({ width, height })
    const text = page.locator(width === 1440 ? '.qimp-question-text' : '[data-import-mobile-row] h3').first()
    await expect(text).toBeVisible()
    await expect(text).toHaveText(stem)
    await expect(page.locator(width === 1440 ? '[data-import-row] [data-question-title]' : '[data-import-mobile-row] [data-question-title]').first()).toContainText(title)
    expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false)
    await page.screenshot({ path: info.outputPath(`${name}.png`), fullPage: true })
  }
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.locator('[data-import-row]').first().getByRole('button', { name: '查看题目 1' }).click()
  await expect(page.locator('[data-question-detail]')).toContainText(title)
  await expect(page.locator('[data-question-detail]')).toContainText(stem)
})
