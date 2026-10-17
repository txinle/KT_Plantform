import { expect, type Page } from '@playwright/test'

export async function requestWholePaperSubmission(page: Page) {
  const desktop = page.locator('[data-finish-request]')
  if (await desktop.isVisible()) await desktop.click()
  else {
    await page.locator('[data-mobile-navigation-trigger]').click()
    await page.getByRole('dialog', { name: '题目导航', exact: true }).getByRole('button', { name: '提交试卷', exact: true }).click()
  }
  const dialog = page.getByRole('dialog', { name: '确认提交试卷', exact: true })
  await expect(dialog).toBeVisible()
  return dialog
}

export async function submitWholePaper(page: Page) {
  const dialog = await requestWholePaperSubmission(page)
  const accepted = page.waitForResponse(response => response.request().method() === 'POST'
    && /\/student\/sessions\/\d+\/finish\/$/.test(new URL(response.url()).pathname))
  void accepted.catch(() => undefined)
  await dialog.locator('[data-paper-submit-confirm]').click()
  const response = await accepted
  expect(response.status(), await response.text()).toBe(202)
  const receipt = (await response.json()).data
  expect(receipt.submission_id).toBeTruthy()
  expect(['queued', 'running', 'completed']).toContain(receipt.status)
  await expect(page).toHaveURL(/\/student\/sessions\/\d+\/complete\/$/, { timeout: 60_000 })
  return receipt
}
