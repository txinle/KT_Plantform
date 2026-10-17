import { submitWholePaper } from './whole-paper'
import { expect, test, type Page } from '@playwright/test'
import { spawnSync } from 'node:child_process'
import { randomUUID } from 'node:crypto'
import { connect, createServer, type Server, type Socket } from 'node:net'
import { requireServerPython, serverDir } from '../scripts/e2e-runtime.mjs'

import { requireIsolatedPostgres } from '../scripts/e2e-database.mjs'
import { acceptResearchConsentIfRequired } from './research-consent'

const root = serverDir
const python = requireServerPython()

interface CaptureFixture {
  course: { id: number }
  experiment_name: string
  student: { username: string; password: string }
}

interface AssignmentRow {
  assignment_id: number
  kind: string
}

class TcpProxy {
  private server?: Server
  private readonly sockets = new Set<Socket>()

  constructor(
    private readonly listenPort: number,
    private readonly upstreamHost: string,
    private readonly upstreamPort: number,
  ) {}

  async start() {
    if (this.server?.listening) return
    await new Promise<void>((resolve, reject) => {
      const server = createServer((client) => {
        const upstream = connect({ host: this.upstreamHost, port: this.upstreamPort })
        this.sockets.add(client)
        this.sockets.add(upstream)
        const close = (socket: Socket) => { this.sockets.delete(socket) }
        client.once('close', () => close(client))
        upstream.once('close', () => close(upstream))
        client.once('error', () => upstream.destroy())
        upstream.once('error', () => client.destroy())
        client.pipe(upstream).pipe(client)
      })
      server.once('error', reject)
      server.listen(this.listenPort, '127.0.0.1', () => {
        server.off('error', reject)
        this.server = server
        resolve()
      })
    })
  }

  async stop() {
    for (const socket of this.sockets) socket.destroy()
    this.sockets.clear()
    const server = this.server
    this.server = undefined
    if (!server) return
    await new Promise<void>((resolve) => server.close(() => resolve()))
  }
}

const directBaseURL = new URL(process.env.E2E_BASE_URL || 'http://127.0.0.1:8124')
const webkitProxyPort = Number(process.env.E2E_WEBKIT_PROXY_PORT || (process.env.E2E_BASE_URL ? '18081' : '8125'))
let webkitProxy: TcpProxy | undefined

test.beforeEach(async ({ browserName }) => {
  if (browserName !== 'webkit') return
  webkitProxy = new TcpProxy(webkitProxyPort, directBaseURL.hostname, Number(directBaseURL.port))
  await webkitProxy.start()
})

test.afterEach(async () => {
  await webkitProxy?.stop()
  webkitProxy = undefined
})

function django(args: string[]) {
  const result = spawnSync(python, ['manage.py', ...args], {
    cwd: root,
    encoding: 'utf8',
    env: { ...process.env, ...requireIsolatedPostgres(process.env), PYTHONUTF8: '1' },
  })
  if (result.error) throw result.error
  if (result.status !== 0) throw new Error(result.stderr || result.stdout)
  return JSON.parse(result.stdout.trim().split('\n').at(-1)!)
}

function eventCounts(sessionId: number, assignmentId: number) {
  return django(['shell', '-c', [
    'import json',
    'from apps.assessment.models import AssessmentEvent, QuestionVisitEvent',
    `events=AssessmentEvent.objects.filter(session_id=${sessionId},assignment_id=${assignmentId})`,
    `visits=QuestionVisitEvent.objects.filter(session_id=${sessionId},attempt__assignment_id=${assignmentId})`,
    "print(json.dumps({'item_entered':events.filter(event_type='item_entered').count(),'item_rendered':events.filter(event_type='item_rendered').count(),'visits':visits.count()}))",
  ].join('; ')])
}

async function setAnswer(page: Page, kind: string) {
  const input = page.locator('[name=answer]').first()
  await expect(input).toBeEnabled()
  const draftResponse = page.waitForResponse((response) => response.url().endsWith('/draft/'))
  if (kind === 'single_choice' || kind === 'true_false') {
    const choices = page.getByRole('radio')
    expect(await choices.count()).toBeGreaterThanOrEqual(2)
    await choices.nth(0).check()
    await choices.nth(1).check()
    await expect(choices.nth(1)).toBeChecked()
  } else if (kind === 'multiple_choice') {
    const choices = page.getByRole('checkbox')
    expect(await choices.count()).toBeGreaterThanOrEqual(2)
    await choices.nth(0).check()
    await choices.nth(1).check()
    await choices.nth(0).uncheck()
    await expect(choices.nth(0)).not.toBeChecked()
    await expect(choices.nth(1)).toBeChecked()
  } else {
    const values = {
      numeric: { draft: '1.25', final: '12.5' },
      fill_blank: { draft: '硬件描述', final: '硬件描述语言' },
      short_answer: { draft: '离线前', final: '离线前的简答' },
    }[kind]
    expect(values, `unsupported question type ${kind}`).toBeDefined()
    await input.fill(values!.draft)
    await input.fill(values!.final)
  }
  const response = await draftResponse
  expect(response.ok(), await response.text()).toBe(true)
  await expect(page.locator('[data-save-status]')).toHaveText('草稿已保存')
}


async function login(page: Page, username: string, password: string) {
  const bootstrapSession = page.waitForResponse((response) => (
    new URL(response.url()).pathname === '/api/v1/auth/bootstrap/'
    && response.request().method() === 'GET'
  ))
  await page.goto('/login/')
  await bootstrapSession
  await page.getByLabel('账号或学号').fill(username)
  await page.getByLabel('密码').fill(password)
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL(/\/student\/$/)
  await acceptResearchConsentIfRequired(page)
}

test('capture-v2 initializes once, recovers safely, and preserves all six response types', async ({ browser, browserName, context, page }, testInfo) => {
  test.setTimeout(300_000)
  const runKey = `${browserName.slice(0, 3)}-${process.pid}-${randomUUID().slice(0, 8)}`
  const fixture: CaptureFixture = django(['seed_e2e_capture_initialization', '--run-key', runKey])
  let questionApiRequests = 0
  let collectorRequests = 0
  let draftRequests = 0
  let submitRequests = 0
  page.on('request', (request) => {
    const path = new URL(request.url()).pathname
    if (/^\/api\/v1\/student\/sessions\/\d+\/questions\/\d+\/$/.test(path)) questionApiRequests += 1
    if (path.includes('process-event-collector')) collectorRequests += 1
    if (path.endsWith('/draft/')) draftRequests += 1
    if (path.endsWith('/finish/')) submitRequests += 1
  })

  await login(page, fixture.student.username, fixture.student.password)

  const loginPageResponse = await page.request.get('/login/', { maxRedirects: 0 })
  expect(loginPageResponse.headers()['x-capture-contract']).toBeUndefined()
  const normalPage = await page.request.get('/student/')
  expect(normalPage.headers()['x-capture-contract']).toBeUndefined()
  const privateEntry = await page.request.get('/__frontend/capture-v2.html')
  expect([403, 404]).toContain(privateEntry.status())

  let injectedFailure = false
  let workerPreparationRequests = 0
  if (browserName === 'chromium') {
    await context.route(/\/static\/spa-assets\/capture-shell-[a-f0-9]+\.html$/, async (route) => {
      if (route.request().serviceWorker()) {
        workerPreparationRequests += 1
        if (!injectedFailure) {
          injectedFailure = true
          await route.fulfill({ status: 200, contentType: 'text/html', body: '<!doctype html><title>header fault</title>' })
          return
        }
      }
      await route.continue()
    })
  }

  await page.goto(`/student/courses/${fixture.course.id}/`)
  const captureDocument = page.waitForResponse((response) => (
    response.request().isNavigationRequest()
    && /^\/student\/sessions\/\d+\/questions\/\d+\/$/.test(new URL(response.url()).pathname)
  ))
  await page.getByRole('article').filter({ has: page.getByRole('heading', { name: fixture.experiment_name, exact: true }) })
    .getByRole('button', { name: '开始作业', exact: true }).click()
  const documentResponse = await captureDocument
  expect(documentResponse.status()).toBe(200)
  expect(await documentResponse.text()).toContain('id="kt-app"')
  await expect(page).toHaveURL(/\/student\/sessions\/\d+\/questions\/\d+\/$/)
  const sessionMatch = page.url().match(/sessions\/(\d+)\/questions\/(\d+)/)
  expect(sessionMatch).not.toBeNull()
  const sessionId = Number(sessionMatch![1])
  const firstAssignmentId = Number(sessionMatch![2])
  const questionPath = new URL(page.url()).pathname
  const origin = new URL(page.url()).origin
  await expect.poll(() => page.locator('main').getAttribute('data-init-state')).toMatch(/^(failed|ready)$/)
  const failed = await page.locator('main[data-init-state="failed"]').isVisible()
  if (browserName === 'chromium') expect(failed).toBe(true)
  if (failed) {
    await expect(page.getByRole('heading', { name: '安全作答环境准备失败', exact: true })).toBeVisible()
    await expect(page.getByRole('alert')).toContainText('capture_shell_version_mismatch')
    expect(injectedFailure).toBe(true)
    expect(workerPreparationRequests).toBe(1)
    await expect(page.locator('[name=answer]').first()).toBeDisabled()
    await expect(page.locator('[data-next-question]')).toBeDisabled()
    await expect(page.locator('[data-finish-request]')).toBeDisabled()
    await expect(page.locator('[data-nav-assignment]').first()).toHaveAttribute('aria-disabled', 'true')
    await expect(page.locator('[data-nav-assignment]').first()).not.toHaveAttribute('href', /.+/)
    await expect(page.locator('[data-init-return]')).toHaveAttribute('href', '/student/')
    expect(questionApiRequests).toBe(0)
    expect(collectorRequests).toBe(0)
    expect(draftRequests).toBe(0)
    expect(submitRequests).toBe(0)
    expect(await page.locator('script[data-kt-collector]').count()).toBe(0)
    expect(eventCounts(sessionId, firstAssignmentId)).toEqual({ item_entered: 1, item_rendered: 0, visits: 1 })

    const anonymousContext = await browser.newContext({ baseURL: origin })
    try {
      const anonymous = await anonymousContext.request.get(questionPath, { maxRedirects: 0 })
      expect(anonymous.status()).toBe(200)
      expect(anonymous.headers()['x-capture-contract']).toBeUndefined()
      const denied = await anonymousContext.request.get(`/api/v1${questionPath}`)
      expect([401, 403]).toContain(denied.status())
      expect((await denied.json()).data).toBeUndefined()
    } finally {
      await anonymousContext.close()
    }

    const outsiderFixture: CaptureFixture = django([
      'seed_e2e_capture_initialization', '--run-key', `out-${process.pid}-${randomUUID().slice(0, 8)}`,
    ])
    const outsiderContext = await browser.newContext({ baseURL: origin })
    try {
      const outsiderPage = await outsiderContext.newPage()
      await login(outsiderPage, outsiderFixture.student.username, outsiderFixture.student.password)
      const outsider = await outsiderContext.request.get(`/api/v1${questionPath}`, { maxRedirects: 0 })
      expect(outsider.status()).toBe(404)
      expect(outsider.headers()['x-capture-contract']).toBeUndefined()
    } finally {
      await outsiderContext.close()
    }

    const forcePasswordChange = (enabled: boolean) => django(['shell', '-c', [
      'import json',
      'from apps.accounts.models import User',
      `updated=User.objects.filter(username=${JSON.stringify(fixture.student.username)}).update(must_change_password=${enabled ? 'True' : 'False'})`,
      "print(json.dumps({'updated':updated}))",
    ].join('; ')])
    forcePasswordChange(true)
    try {
      const forced = await page.request.get(questionPath, { maxRedirects: 0 })
      expect(forced.status()).toBe(200)
      expect(forced.headers()['x-capture-contract']).toBeUndefined()
      expect((await page.request.get(`/api/v1${questionPath}`)).status()).toBe(200)
    } finally {
      forcePasswordChange(false)
    }
    await page.locator('[data-init-retry]').click()
  }
  await expect(page.locator('main[data-init-state="ready"]')).toBeVisible()
  await expect(page.locator('[name=answer]').first()).toBeEnabled()
  expect(questionApiRequests).toBe(0)
  expect(collectorRequests).toBe(0)
  expect(await page.locator('script[data-kt-collector]').count()).toBe(0)

  const rows: AssignmentRow[] = django(['shell', '-c', [
    'import json',
    'from apps.assessment.models import Attempt',
    `rows=Attempt.objects.filter(session_id=${sessionId}).select_related('assignment__snapshot').order_by('assignment__assigned_sequence_index')`,
    "print(json.dumps([{'assignment_id':row.assignment_id,'kind':row.assignment.snapshot.question_type} for row in rows]))",
  ].join('; ')])
  expect(rows.map((row) => row.kind).sort()).toEqual([
    'fill_blank', 'multiple_choice', 'numeric', 'short_answer', 'single_choice', 'true_false',
  ])

  for (const [index, row] of rows.entries()) {
    await expect(page).toHaveURL((url) => url.pathname.endsWith(`/questions/${row.assignment_id}/`))
    await expect(page.locator('main[data-init-state="ready"]')).toBeVisible()
    await setAnswer(page, row.kind)
    if (index === 0) {
      expect(questionApiRequests).toBe(0)
      expect(eventCounts(sessionId, firstAssignmentId)).toEqual({ item_entered: 1, item_rendered: 1, visits: 1 })
    }
    if (row.kind === 'short_answer') {
      const offlinePreparation = await page.evaluate(async ({ sessionId, assignmentId }) => {
        const identities = await caches.open('kt-capture-v2-runtime-3-identity')
        const identityResponse = await identities.match('/assessment/process/capture-v2/offline-identity')
        if (!identityResponse) throw new Error('Authorized offline identity is missing')
        const identity = await identityResponse.json() as { userId: number; sessionId: number; buildVersion: string }
        if (identity.sessionId !== sessionId) throw new Error('Offline identity belongs to another session')
        const cache = await caches.open(`kt-capture-v2-runtime-3-question-${identity.userId}-${sessionId}-${encodeURIComponent(identity.buildVersion)}`)
        const pageResponse = await cache.match(`/assessment/process/capture-v2/offline-envelope${location.pathname}`)
        const apiResponse = await cache.match(`/api/v1/student/sessions/${sessionId}/questions/${assignmentId}/`)
        const prepared = pageResponse ? await pageResponse.json() as { bootstrap: Record<string, unknown>; shell: string } : undefined
        const assets = await caches.open(`kt-capture-v2-runtime-3-assets-${encodeURIComponent(identity.buildVersion)}`)
        const shellResponse = prepared ? await assets.match(prepared.shell) : undefined
        const envelope = prepared?.bootstrap
        const rawEnvelope = Boolean(envelope && shellResponse)
        const user = envelope?.user as Record<string, unknown> | undefined
        return {
          controlled: Boolean(navigator.serviceWorker.controller),
          pageCached: Boolean(pageResponse),
          bootstrapCached: Boolean(rawEnvelope),
          apiCached: Boolean(apiResponse),
          envelopeValid: Boolean(
            envelope?.version === 1
            && envelope.page_path === location.pathname
            && envelope.assessment_session_id === sessionId
            && envelope.assignment_id === assignmentId
            && typeof envelope.csrf_token === 'string'
            && envelope.csrf_token.length > 0
            && typeof user?.id === 'number'
            && user.role === 'student'
            && typeof user.display_name === 'string'
            && typeof user.username === 'string'
            && (typeof user.student_number === 'string' || user.student_number === null)
            && typeof user.home_url === 'string'
            && typeof user.uses_initial_password === 'boolean'
            && typeof user.initial_password_reminder_pending === 'boolean'
          ),
        }
      }, { sessionId, assignmentId: row.assignment_id })
      expect(offlinePreparation).toEqual({
        controlled: true,
        pageCached: true,
        bootstrapCached: true,
        apiCached: true,
        envelopeValid: true,
      })
      if (browserName === 'webkit') {
        // Web Inspector routing/offline/reload hooks can change the Service Worker path.
        // Cut TCP and use a page-initiated reload instead (microsoft/playwright#42273).
        await page.evaluate(() => globalThis.dispatchEvent(new Event('offline')))
        await webkitProxy!.stop()
        const offlineFetch = await page.evaluate(async () => {
          const response = await fetch(location.href, { cache: 'reload' })
          const html = await response.text()
          return {
            ok: response.ok,
            contract: response.headers.get('X-Capture-Contract'),
            bootstrap: html.includes('id="kt-offline-bootstrap"'),
          }
        })
        expect(offlineFetch).toEqual({ ok: true, contract: 'capture-v2', bootstrap: true })
        const networkFailure = await page.evaluate(async () => {
          try {
            await fetch(`/api/v1/auth/csrf/?offline-probe=${crypto.randomUUID()}`, { cache: 'no-store' })
            return { rejected: false }
          } catch (caught) {
            const error = caught as Error
            return {
              rejected: true,
              name: error.name,
              constructorName: error.constructor?.name,
              message: error.message,
              isTypeError: error instanceof TypeError,
            }
          }
        })
        await testInfo.attach('webkit-network-failure', {
          body: JSON.stringify(networkFailure),
          contentType: 'application/json',
        })
        expect(networkFailure).toMatchObject({ rejected: true })
      } else {
        await context.setOffline(true)
      }
      const input = page.locator('[name=answer]').first()
      await input.fill('离线简答甲')
      await input.fill('离线简答甲乙')
      await page.waitForTimeout(900)
      if (browserName === 'webkit') {
        const previousDocument = await page.evaluate(() => performance.timeOrigin)
        await page.evaluate(() => { globalThis.setTimeout(() => location.reload(), 0) })
        await expect.poll(
          () => page.evaluate(() => performance.timeOrigin).catch(() => previousDocument),
          { timeout: 15_000 },
        ).not.toBe(previousDocument)
        await expect(page.locator('#kt-offline-bootstrap')).toHaveCount(1)
      } else {
        await page.reload({ waitUntil: 'commit' })
      }
      await expect(page.locator('main[data-init-state="ready"]')).toBeVisible({ timeout: 20_000 })
      await expect(input).toHaveValue('离线简答甲乙')
      const reconnectedDraft = page.waitForResponse(
        (response) => response.url().endsWith('/draft/'),
        { timeout: 45_000 },
      )
      if (browserName === 'webkit') {
        await webkitProxy!.start()
        await page.evaluate(() => globalThis.dispatchEvent(new Event('online')))
      } else {
        await context.setOffline(false)
      }
      const response = await reconnectedDraft
      expect(response.ok(), await response.text()).toBe(true)
      await expect(page.locator('[data-save-status]')).toHaveText('草稿已保存')
    }
    if (index < rows.length - 1) {
      const destination = `/student/sessions/${sessionId}/questions/${rows[index + 1]!.assignment_id}/`
      await page.locator('[data-next-question]').click()
      await expect(page).toHaveURL(url => url.pathname === destination)
    } else await submitWholePaper(page)
  }

  expect(draftRequests).toBeGreaterThanOrEqual(7)
  expect(submitRequests).toBe(1)
  expect(await page.locator('script[data-kt-collector]').count()).toBeLessThanOrEqual(1)
})
