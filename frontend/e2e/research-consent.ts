import { expect, type Page } from '@playwright/test'

export async function acceptResearchConsentIfRequired(page: Page) {
  let session: { user?: { role?: string; research_consent_required?: boolean } } | undefined
  await expect.poll(async () => {
    const response = await page.request.get('/api/v1/auth/session/')
    if (!response.ok()) return false
    session = await response.json()
    return session?.user?.role === 'student'
  }, { timeout: 15_000 }).toBe(true)
  if (!session?.user?.research_consent_required) return

  const dialog = page.getByRole('dialog', { name: '研究参与说明与知情同意' })
  await expect(dialog).toBeVisible()
  await dialog.getByRole('checkbox').check()
  const accepted = page.waitForResponse((response) => (
    new URL(response.url()).pathname === '/api/v1/auth/research-consent/'
    && response.request().method() === 'POST'
  ))
  await dialog.getByRole('button', { name: '同意并继续', exact: true }).click()
  expect((await accepted).ok()).toBe(true)
  await expect(dialog).toHaveCount(0)
}
