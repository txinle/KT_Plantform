import { expect, test } from '@playwright/test'
import { spawnSync } from 'node:child_process'
import { requireServerPython, serverDir } from '../scripts/e2e-runtime.mjs'
import { requireIsolatedPostgres } from '../scripts/e2e-database.mjs'
import { openLoginPage } from './login-navigation'
import { acceptResearchConsentIfRequired } from './research-consent'

test('teacher business deep links render the independent entry at desktop and mobile widths', async ({ page }, info) => {
  test.setTimeout(240_000)
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await openLoginPage(page)
  await page.getByLabel('账号或学号').fill('teacher')
  await page.getByLabel('密码').fill('teacher123')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL(/\/teacher\/courses\/$/)
  await page.waitForLoadState('networkidle')
  const paths = [
    '/teacher/courses/', '/teacher/courses/1/', '/teacher/courses/1/settings/',
    '/teacher/accounts/classes/', '/teacher/accounts/students/', '/teacher/accounts/students/import/',
    '/teacher/courses/1/banks/', '/teacher/courses/1/banks/1/',
    '/teacher/courses/1/banks/1/edit/', '/teacher/courses/1/banks/1/import/',
    '/teacher/courses/1/banks/1/kc-import/', '/teacher/courses/1/banks/archived/',
    '/teacher/courses/1/banks/shared/', '/teacher/banks/', '/teacher/concepts/',
    '/teacher/courses/1/classes/', '/teacher/courses/1/classes/1/',
    '/teacher/courses/1/classes/1/?section=manage', '/teacher/courses/1/classes/1/edit/',
    '/teacher/courses/1/classes/1/students/add/manual/', '/teacher/courses/1/classes/1/students/copy/',
    '/teacher/courses/1/classes/1/students/import/', '/teacher/courses/1/classes/1/student-visibility/',
    '/teacher/courses/1/experiments/', '/teacher/courses/1/experiments/create/',
    '/teacher/courses/1/experiments/1/', '/teacher/courses/1/experiments/1/results/',
    '/teacher/experiments/', '/teacher/experiments/create/',
    '/teacher/courses/1/data/', '/teacher/courses/1/data/teaching/',
    '/teacher/courses/1/data/exports/', '/teacher/courses/1/data/quality/',
    '/teacher/courses/1/data/exports/history/', '/teacher/courses/1/data/advanced/',
  ]
  for (const [index, path] of paths.entries()) {
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: width === 390 ? 844 : 900 })
      await page.goto(path)
      await page.waitForLoadState('networkidle')
      const heading = page.locator('[data-vue-shell] h1').first()
      await expect(heading, path).toBeVisible()
      if (path === '/teacher/courses/1/data/advanced/') {
        const fontSize = Number.parseFloat(await heading.evaluate(element => getComputedStyle(element).fontSize))
        expect(fontSize, path).toBeGreaterThanOrEqual(28)
        expect(fontSize, path).toBeLessThanOrEqual(32)
      } else if (path === '/teacher/courses/1/experiments/create/' && width === 1440) {
        await expect(heading, path).toHaveCSS('font-size', '20px')
      } else {
        await expect(heading, path).toHaveCSS('font-size', width === 390 ? '24px' : /^(26|28)px$/)
      }
      await expect(page.locator('#kt-bootstrap')).toHaveCount(0)
      if (path === '/teacher/courses/1/classes/') {
        await expect(page.locator(width === 390 ? '.kt-desktop-only' : '.kt-mobile-only')).toBeHidden()
        await expect(page.locator(width === 390 ? '.kt-mobile-only' : '.kt-desktop-only')).toBeVisible()
      }
      await page.screenshot({ path: info.outputPath(`teacher-${index}-${width}.png`), fullPage: true })
      const overflow = await page.evaluate(() => [...document.querySelectorAll('main *')]
        .filter(node => node.getBoundingClientRect().right > innerWidth + 1)
        .map(node => `${node.tagName}.${node.className}`))
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `${path}: ${overflow.join(', ')}`).toBe(true)
    }
  }
  const missingPage = await page.goto('/teacher/courses/999999/classes/')
  expect(missingPage?.status()).toBe(200)
  await expect(page.getByRole('alert')).toBeVisible()
  const missingDomain = await page.request.get('/api/v1/teacher/courses/999999/classes/')
  expect(missingDomain.status()).toBe(404)
  const missingApi = await page.request.get('/api/v1/not-a-real-endpoint/')
  expect(missingApi.status()).toBe(404)
  expect(await missingApi.text()).not.toContain('id="kt-app"')
  expect(errors).toEqual([])
})

test('student entry, drawer and feedback navigation stay usable at compact viewports', async ({ page }, info) => {
  const scripts: string[] = []
  page.on('request', request => { if (request.resourceType() === 'script') scripts.push(request.url()) })
  await openLoginPage(page)
  await page.getByLabel('账号或学号').fill('S002')
  await page.getByLabel('密码').fill('student123')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL(/\/student\/$/)
  await acceptResearchConsentIfRequired(page)
  for (const size of [{ width: 1440, height: 900 }, { width: 390, height: 844 }, { width: 768, height: 1024 }, { width: 720, height: 450 }]) {
    await page.setViewportSize(size)
    await page.goto('/student/courses/2/')
    await expect(page.getByRole('heading', { name: '作业', exact: true })).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
    if (size.width < 1024) {
      const menu = page.locator('[data-workspace-menu]')
      await menu.click()
      await expect(page.locator('[data-workspace-close]')).toBeFocused()
      await page.keyboard.press('Escape')
      await expect(menu).toBeFocused()
    }
    await page.screenshot({ path: info.outputPath(`student-${size.width}.png`), fullPage: true })
  }
  expect(scripts.filter(url => /\/Teacher[^/]+\.js/.test(url))).toEqual([])
})


test('teacher without courses keeps independent bank and experiment workflows', async ({ page }, info) => {
  const root = serverDir
  const python = requireServerPython()
  const fixture = spawnSync(python, ['manage.py', 'shell', '-c', `
import json
from apps.accounts.models import User
from apps.question_bank.models import QuestionBank, Question
u, _ = User.objects.get_or_create(username='e2e-standalone-ui', defaults={'role': 'teacher', 'display_name': '独立教师'})
u.set_password('standalone123'); u.save()
b, _ = QuestionBank.objects.get_or_create(owner=u, name='独立 UI 题库')
q, _ = Question.objects.get_or_create(bank=b, sequence=1, defaults={'question_type': Question.Type.SHORT, 'text': '简述 EDA', 'correct_answer': '电子设计自动化'})
print(json.dumps({'bank': b.pk, 'question': q.pk}))
  `], { cwd: root, encoding: 'utf8', env: { ...process.env, ...requireIsolatedPostgres(process.env), PYTHONUTF8: '1' } })
  if (fixture.error) throw fixture.error
  expect(fixture.status, fixture.stderr).toBe(0)
  const data = JSON.parse(fixture.stdout.trim().split('\n').at(-1)!) as { bank: number; question: number }
  await openLoginPage(page)
  await page.getByLabel('账号或学号').fill('e2e-standalone-ui')
  await page.getByLabel('密码').fill('standalone123')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL(/\/teacher\/courses\/$/)
  const paths = ['/teacher/banks/', `/teacher/banks/${data.bank}/`, `/teacher/banks/${data.bank}/import/`, `/teacher/banks/${data.bank}/kc-import/`, `/teacher/banks/${data.bank}/questions/${data.question}/edit/`, '/teacher/concepts/', '/teacher/experiments/', '/teacher/experiments/create/']
  for (const [index, path] of paths.entries()) {
    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto(path)
    await page.waitForLoadState('networkidle')
    await expect(page.locator('h1').first()).toBeVisible()
    await expect(page).toHaveURL(new RegExp(`${path}$`))
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), path).toBe(true)
    await page.screenshot({ path: info.outputPath(`independent-${index}-390.png`), fullPage: true })
  }
  await page.getByLabel('实验名称', { exact: true }).fill('独立 UI 草稿')
  await page.getByLabel('题库', { exact: true }).selectOption(String(data.bank))
  await page.getByRole('button', { name: '创建并载入题库题目', exact: true }).click()
  await expect(page).toHaveURL(/\/teacher\/experiments\/\d+\/$/)
  await expect(page.getByRole('heading', { name: '独立 UI 草稿', exact: true })).toBeVisible()
  const detail = page.url()
  await page.goto(`${detail}edit/`)
  await expect(page.getByLabel('实验名称', { exact: true })).toHaveValue('独立 UI 草稿')
  await page.getByLabel('实验说明（可选）', { exact: true }).fill('保留独立入口的已有编辑流程')
  await page.getByRole('button', { name: '保存实验配置', exact: true }).click()
  await expect(page).toHaveURL(detail)
})


test('class creation preserves validation in Vue and follows the existing detail URL', async ({ page }) => {
  await openLoginPage(page)
  await page.getByLabel('账号或学号').fill('teacher')
  await page.getByLabel('密码').fill('teacher123')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL(/\/teacher\/courses\/$/)
  await page.goto('/teacher/courses/1/classes/')
  await page.locator('#new-class-name').fill('   ')
  const rejected = page.waitForResponse(response => response.url().endsWith('/api/v1/teacher/courses/1/classes/') && response.request().method() === 'POST')
  await page.getByRole('button', { name: '创建班级', exact: true }).click()
  expect((await rejected).status()).toBe(400)
  await expect(page.locator('[role="alert"]').first()).toBeVisible()
  await expect(page).toHaveURL(/\/teacher\/courses\/1\/classes\/$/)
  await page.locator('#new-class-name').fill(`E2E JSON 班级 ${Date.now()}`)
  await page.locator('#new-class-year').fill('2026')
  const created = page.waitForResponse(response => response.url().endsWith('/api/v1/teacher/courses/1/classes/') && response.request().method() === 'POST')
  await page.getByRole('button', { name: '创建班级', exact: true }).click()
  expect((await created).status()).toBe(201)
  await expect(page).toHaveURL(/\/teacher\/courses\/1\/classes\/\d+\/$/)
  await expect(page.locator('#kt-bootstrap')).toHaveCount(0)
})
