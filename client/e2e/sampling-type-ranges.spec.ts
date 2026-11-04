import { expect, test } from '@playwright/test'

test('type candidate range survives save and supports responsive staged editing', async ({ page }, testInfo) => {
  test.setTimeout(90_000)
  await page.goto('/login/')
  await page.getByLabel('账号或学号').fill('teacher')
  await page.getByLabel('密码').fill('teacher123')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL(/\/teacher\/courses\//)
  await page.goto('/teacher/courses/1/experiments/create/')
  await page.getByLabel('实验名称', { exact: true }).fill(`题型候选范围验收 ${Date.now()}`)
  await page.locator('#experiment-bank').click()
  for (const checkbox of await page.locator('input[name="question_bank_choice"]').all()) await checkbox.uncheck()
  await page.locator('input[name="question_bank_choice"][value="1"]').check()
  await page.getByRole('button', { name: '应用题库', exact: true }).click()
  await page.getByLabel('每位学生总题数', { exact: true }).fill('1')
  await page.locator('[data-select-types]').click()
  await page.locator('input[name="question_type_choice"][value="single_choice"]').check()
  await page.getByRole('button', { name: '应用题型', exact: true }).click()
  await page.locator('[data-select-question-type="single_choice"]').focus()
  await page.keyboard.press('Enter')
  const dialog = page.getByRole('dialog', { name: '抽题范围 · 单选题' })
  await dialog.locator('[data-range-custom]').check()
  const choices = dialog.locator('[data-range-question]')
  await expect(choices.first()).toBeVisible()
  const ids: number[] = []
  for (let index = 0; index < 3; index++) {
    ids.push(Number(await choices.nth(index).getAttribute('data-range-question')))
    await choices.nth(index).check()
  }
  await dialog.locator('[data-range-selected-only]').check()
  await expect(choices).toHaveCount(3)
  await dialog.locator('details summary').first().click()
  await expect(dialog.locator('details').first()).toHaveAttribute('open', '')
  await expect(dialog.locator('.kt-experiment-inline-preview article')).toBeVisible()
  for (const [name, width, height] of [
    ['desktop', 1440, 900], ['mobile', 390, 844], ['zoom-equivalent', 720, 450],
  ] as const) {
    await page.setViewportSize({ width, height })
    await page.screenshot({ path: testInfo.outputPath(`range-picker-${name}.png`) })
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width + 1)
    expect(await dialog.evaluate(element => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1)
  }
  await dialog.locator('[data-apply-range]').click()
  await page.setViewportSize({ width: 1440, height: 900 })
  await expect(page.locator('[data-select-question-type="single_choice"]')).toContainText('已选 3 道候选')
  await page.locator('[data-select-question-type="single_choice"]').click()
  await dialog.locator('[data-range-all]').check()
  await dialog.locator('[data-cancel-range]').click()
  await expect(page.locator('[data-select-question-type="single_choice"]')).toContainText('已选 3 道候选')
  await page.locator('input[name="cross_experiment_item_policy"][value="unrestricted"]').check()
  const saved = page.waitForResponse(response => response.request().method() === 'POST'
    && /\/api\/v1\/teacher\/courses\/1\/experiments\/$/.test(new URL(response.url()).pathname))
  await page.getByRole('button', { name: '保存草稿', exact: true }).click()
  const response = await saved
  expect(response.status()).toBe(201)
  const definition = (await response.json()).data
  expect(definition.sampling.type_ranges).toEqual([{ question_type: 'single_choice', question_ids: ids }])
  await expect(page).toHaveURL(/\/teacher\/courses\/1\/experiments\/\d+\/$/)
  const inventoryResponse = page.waitForResponse(response => response.url().includes('/sampling-inventory/')
    && response.request().postDataJSON()?.type_ranges?.length > 0)
  await page.goto(`${page.url()}edit/`)
  const inventory = (await (await inventoryResponse).json()).data
  await expect(page.locator('[data-select-question-type="single_choice"]')).toContainText('已选 3 道候选')
  await expect(page.getByLabel('各题库候选库存')).toContainText(`${inventory.bank_counts[0].candidate_count} 题`)
  for (const [name, width, height] of [
    ['desktop', 1440, 900], ['mobile', 390, 844], ['zoom-equivalent', 720, 450],
  ] as const) {
    await page.setViewportSize({ width, height })
    await page.locator('[aria-labelledby="definition-selection"]').scrollIntoViewIfNeeded()
    await page.screenshot({ path: testInfo.outputPath(`range-form-${name}.png`) })
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width + 1)
  }
})
