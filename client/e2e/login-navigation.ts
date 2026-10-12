import { errors, expect, test, type Page, type Request, type Response } from '@playwright/test'

export async function openLoginPage(page: Page, origin = 'http://127.0.0.1:8000') {
  const url = new URL('/login/', origin).href
  const previousDocument = await page.evaluate(() => performance.timeOrigin)
  const pending = new Set<Request>()
  let documentStatus: number | undefined
  let requestFailed = false
  let scriptFailed = false
  const onRequest = (request: Request) => { pending.add(request) }
  const onFinished = (request: Request) => { pending.delete(request) }
  const onFailed = (request: Request) => { pending.delete(request); requestFailed = true }
  const onPageError = () => { scriptFailed = true }
  const onResponse = (response: Response) => {
    if (response.request().isNavigationRequest() && response.frame() === page.mainFrame() && response.url() === url) {
      documentStatus = response.status()
    }
  }
  page.on('request', onRequest)
  page.on('requestfinished', onFinished)
  page.on('requestfailed', onFailed)
  page.on('pageerror', onPageError)
  page.on('response', onResponse)
  const navigate = () => page.goto(url, { waitUntil: 'commit', timeout: 10_000 })
  try {
    let response: Response | null
    try {
      response = await navigate()
    } catch (error) {
      if (!(error instanceof errors.TimeoutError) || page.context().browser()?.browserType().name() !== 'firefox') throw error
      // Firefox can leave a completed navigation pending: microsoft/playwright#42183.
      const state = await page.evaluate(() => {
        const usable = (selector: string) => {
          const element = document.querySelector<HTMLInputElement | HTMLButtonElement>(selector)
          return Boolean(element && !element.disabled && element.getClientRects().length && getComputedStyle(element).visibility !== 'hidden')
        }
        return {
          url: location.href, readyState: document.readyState, timeOrigin: performance.timeOrigin,
          formReady: usable('input[name="username"]') && usable('input[name="password"]') && usable('button[type="submit"]'),
        }
      })
      if (documentStatus !== 200 || pending.size || requestFailed || scriptFailed || state.url !== url
        || state.readyState !== 'complete' || state.timeOrigin === previousDocument || !state.formReady) throw error
      test.info().annotations.push({ type: 'firefox-navigation-recovery', description: `Completed login navigation stalled: ${url}` })
      await test.info().attach('firefox-navigation-recovery', { body: JSON.stringify({ ...state, documentStatus, pendingRequests: pending.size }), contentType: 'application/json' })
      response = await navigate()
    }
    expect(response?.status(), `Login document: ${url}`).toBe(200)
    await expect(page.getByLabel('账号或学号')).toBeVisible()
    await expect(page.getByLabel('密码')).toBeEnabled()
  } finally {
    page.off('request', onRequest)
    page.off('requestfinished', onFinished)
    page.off('requestfailed', onFailed)
    page.off('pageerror', onPageError)
    page.off('response', onResponse)
  }
}
