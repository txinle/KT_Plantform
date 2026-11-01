import { spawnSync } from 'node:child_process'
import { readFileSync, writeFileSync } from 'node:fs'
import { expect, test, type Page } from '@playwright/test'
import { requireIsolatedPostgres } from '../scripts/e2e-database.mjs'
import { requireServerPython, serverDir } from '../scripts/e2e-runtime.mjs'
import { openLoginPage } from './login-navigation'
import { acceptResearchConsentIfRequired } from './research-consent'

interface RouteCase { name: string; pattern: string; path: string; persona: string }
interface Finding { name: string; path: string; actualPath: string; viewport: string; screenshot: string; issues: string[] }

async function login(page: Page, username: string, password: string) {
  await openLoginPage(page, test.info().project.use.baseURL)
  await page.getByLabel('账号或学号').fill(username)
  await page.getByLabel('密码').fill(password)
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).not.toHaveURL(/\/login\//)
  if (!['teacher', 'r3-english-teacher', 'admin'].includes(username)) await acceptResearchConsentIfRequired(page)
}

test('all registered R3 business pages have desktop, phone and 200 percent equivalent evidence', async ({ browser }, info) => {
  test.setTimeout(600_000)
  const source = readFileSync(new URL('./fixtures/registered-routes.py', import.meta.url), 'utf8')
  const fixture = spawnSync(requireServerPython(), ['manage.py', 'shell', '-c', source], {
    cwd: serverDir, encoding: 'utf8', timeout: 30_000,
    env: { ...process.env, ...requireIsolatedPostgres(process.env), PYTHONUTF8: '1' },
  })
  expect(fixture.status, fixture.stderr).toBe(0)
  const data = JSON.parse(fixture.stdout.trim().split(/\r?\n/).at(-1)!) as { routes: RouteCase[]; group_result: string }
  const registry = JSON.parse(readFileSync(new URL('../../contracts/frontend-routes.json', import.meta.url), 'utf8')) as { routes: Array<{ name: string }> }
  expect(data.routes.map(route => route.name)).toEqual(registry.routes.map(route => route.name))
  const identities: Record<string, [string, string] | null> = {
    anonymous: null, admin: ['admin', 'admin123'], teacher: ['teacher', 'teacher123'], student: ['S002', 'student123'],
    runtime: ['S001', 'student123'],
    groupstudent: ['site-v2-types-student', 'SiteV2Student123!'],
  }
  const pages = new Map<string, Page>()
  const contexts = []
  const findings: Finding[] = []
  try {
    for (const [persona, credentials] of Object.entries(identities)) {
      const context = await browser.newContext({ baseURL: info.project.use.baseURL, viewport: { width: 1440, height: 900 } })
      contexts.push(context)
      const page = await context.newPage()
      pages.set(persona, page)
      if (credentials) await login(page, ...credentials)
    }
    // The existing seed session is entered through its assigned task before inspecting its page.
    const runtime = pages.get('runtime')!
    await runtime.goto('/student/courses/1/')
    const task = runtime.locator('.kt-student-task-row').filter({ has: runtime.getByRole('heading', { name: '演示交互数据采集', exact: true }) })
    await task.getByRole('button', { name: /^(开始作业|继续作答)$/ }).click()
    await expect(runtime.locator('[data-init-state="ready"]')).toBeVisible()
    const runtimeRoute = data.routes.find(route => route.name === 'assessment:question')!
    runtimeRoute.path = new URL(runtime.url()).pathname
    for (const [index, route] of data.routes.entries()) {
      const page = pages.get(route.persona)!
      await page.goto(route.path)
      await page.waitForLoadState('networkidle')
      if (route.persona === 'admin') expect(new URL(page.url()).pathname, route.path).toMatch(/^\/manage\//)
      for (const size of [
        { width: 1440, height: 900, label: 'desktop' },
        { width: 390, height: 844, label: 'mobile' },
        { width: 720, height: 450, label: 'zoom-equivalent' },
      ]) {
        await page.setViewportSize(size)
        await page.evaluate(() => scrollTo(0, 0))
        const issues = await page.evaluate(() => {
          const result: string[] = []
          if (document.documentElement.scrollWidth > innerWidth + 1) result.push(`horizontal overflow ${document.documentElement.scrollWidth}/${innerWidth}`)
          const main = document.querySelector('main')
          if (!main) result.push('missing main landmark')
          for (const node of document.querySelectorAll<HTMLElement>('main button, main label, main input, main textarea, main small, main th, main td')) {
            if (!node.getClientRects().length || node.closest('svg,[aria-hidden="true"],.kt-visually-hidden,details:not([open])')) continue
            if (!node.textContent?.trim() && !node.matches('input,textarea')) continue
            // The approved management/collaboration Site uses 11px compact metadata and actions.
            const minimum = node.closest('.management-workspace') ? 11
              : location.pathname.startsWith('/teacher/') && node.matches('button,label,small,th') ? 13 : 14
            if (Number.parseFloat(getComputedStyle(node).fontSize) < minimum) result.push(`text below${minimum}px: ${node.className || node.tagName}`)
          }
          for (const node of document.querySelectorAll<HTMLElement>('main [role="alert"]')) {
            if (node.getClientRects().length && /加载失败|不存在|无权访问|请求失败|Unable|not found/i.test(node.textContent || '')) result.push(`page error: ${node.textContent?.slice(0, 100)}`)
          }
          return [...new Set(result)]
        })
        const screenshot = `${String(index + 1).padStart(2, '0')}-${route.name.replaceAll(':', '-')}-${size.label}.png`
        await page.screenshot({ path: info.outputPath(screenshot), fullPage: true })
        findings.push({ name: route.name, path: route.path, actualPath: new URL(page.url()).pathname, viewport: size.label, screenshot, issues })
      }
      if (route.persona === 'runtime') {
        await page.locator('[data-assessment-return]').click()
        await expect(page).toHaveURL(/\/student\/$/)
      }
    }
    const grouped = pages.get('groupstudent')!
    await grouped.goto(data.group_result)
    await expect(grouped.getByRole('region', { name: '题目结果', exact: true })).toBeVisible()
    for (const size of [{ width: 1366, height: 768 }, { width: 1920, height: 1080 }, { width: 2560, height: 1229 }]) {
      await grouped.setViewportSize(size)
      expect(await grouped.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
      await grouped.screenshot({ path: info.outputPath(`group-results-${size.width}x${size.height}.png`), fullPage: true })
    }
  } finally {
    writeFileSync(info.outputPath('registered-route-findings.json'), JSON.stringify(findings, null, 2))
    await Promise.all(contexts.map(context => context.close()))
  }
  expect(findings.filter(finding => finding.issues.length)).toEqual([])
})
