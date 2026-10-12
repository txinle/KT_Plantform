import { errors, expect, test, type Page } from '@playwright/test'
import { openLoginPage } from './login-navigation'

function injectCompletedNavigationTimeout(page: Page, times = 1) {
  const goto = page.goto.bind(page)
  const failure = new errors.TimeoutError('Simulated Firefox navigation bookkeeping stall')
  let calls = 0
  page.goto = async (url, options) => {
    calls += 1
    const response = await goto(url, { ...options, waitUntil: 'load' })
    await expect(page.getByLabel('账号或学号')).toBeVisible()
    await page.waitForLoadState('networkidle')
    if (calls <= times) throw failure
    return response
  }
  return { failure, calls: () => calls, restore: () => { page.goto = goto } }
}

test.beforeEach(({ browserName }) => {
  test.skip(browserName !== 'firefox', 'Regression for Firefox navigation bookkeeping.')
})

for (const origin of ['http://127.0.0.1:8000']) {
  test(`recovers a fully loaded login whose navigation promise times out: ${origin}`, async ({ page }, info) => {
    const injected = injectCompletedNavigationTimeout(page)
    try {
      await openLoginPage(page, origin)
      expect(injected.calls()).toBe(2)
      await page.getByLabel('账号或学号').fill('teacher')
      expect(info.annotations.some(row => row.type === 'firefox-navigation-recovery')).toBe(true)
    } finally { injected.restore() }
  })
}

test('does not recover a failed HTTP response even when it renders a login form', async ({ page }) => {
  await page.route('http://127.0.0.1:8000/login/', async route => {
    const response = await route.fetch()
    await route.fulfill({ response, status: 500 })
  })
  const injected = injectCompletedNavigationTimeout(page)
  try {
    await expect(openLoginPage(page)).rejects.toBe(injected.failure)
    expect(injected.calls()).toBe(1)
  } finally { injected.restore() }
})

test('a second navigation timeout remains a failure', async ({ page }) => {
  const injected = injectCompletedNavigationTimeout(page, 2)
  try {
    await expect(openLoginPage(page)).rejects.toBe(injected.failure)
    expect(injected.calls()).toBe(2)
  } finally { injected.restore() }
})

test('does not recover a document with a script error', async ({ page }) => {
  await page.addInitScript(() => {
    window.addEventListener('DOMContentLoaded', () => { throw new Error('Injected login script failure') })
  })
  const injected = injectCompletedNavigationTimeout(page)
  try {
    await expect(openLoginPage(page)).rejects.toBe(injected.failure)
    expect(injected.calls()).toBe(1)
  } finally { injected.restore() }
})
