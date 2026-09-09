import { expect, test } from '@playwright/test'
import { writeFileSync } from 'node:fs'

test('business labels, controls and long text fit their actual layout', async ({ page }, info) => {
  test.setTimeout(600_000)
  await page.goto('/login/')
  await page.getByLabel('账号或学号').fill('teacher')
  await page.getByLabel('密码').fill('teacher123')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL(/\/teacher\/courses\/$/)
  const paths = [
    '/teacher/courses/', '/teacher/courses/1/', '/teacher/courses/1/settings/',
    '/teacher/accounts/classes/', '/teacher/accounts/students/', '/teacher/accounts/students/import/',
    '/teacher/courses/1/banks/', '/teacher/courses/1/banks/1/',
    '/teacher/courses/1/banks/1/edit/', '/teacher/courses/1/banks/1/import/',
    '/teacher/courses/1/banks/archived/',
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
    '/teacher/courses/1/banks/?tab=course-kcs',
  ]
  const observations: Array<{ path: string; width: number; issues: string[] }> = []
  for (const [index, path] of paths.entries()) {
    for (const width of [1440, 1280, 1024, 768, 390]) {
      await page.setViewportSize({ width, height: width === 390 ? 844 : 900 })
      await page.goto(path)
      await page.waitForLoadState('networkidle')
      await expect(page.locator('main h1').first(), path).toBeVisible()
      const issues = await page.evaluate(() => {
        const result: string[] = []
        const shown = (el: HTMLElement) => el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden'
        if (document.documentElement.scrollWidth > innerWidth + 1) result.push(`page overflow ${document.documentElement.scrollWidth}/${innerWidth}`)
        for (const table of document.querySelectorAll<HTMLElement>('.kt-desktop-only')) {
          const mobile = table.parentElement?.querySelector<HTMLElement>('.kt-mobile-only')
          if (mobile && !shown(table) && !shown(mobile)) result.push('both desktop and mobile records hidden')
        }
        for (const el of document.querySelectorAll<HTMLElement>('.kt-status, .kt-qb-badge, .kt-option-badge')) {
          if (!shown(el)) continue
          const label = el.textContent?.trim() || ''
          const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT)
          const tops: number[] = []
          while (walker.nextNode()) {
            const range = document.createRange(); range.selectNodeContents(walker.currentNode)
            tops.push(...[...range.getClientRects()].filter(r => r.width > 0 && r.height > 0).map(r => Math.round(r.top)))
          }
          const rows = new Set(tops)
          if (label.length <= 10 && rows.size > 1) result.push(`split badge: ${label} .${el.className}`)
          if (el.scrollWidth > el.clientWidth + 1) result.push(`clipped badge: ${label}`)
        }
        for (const el of document.querySelectorAll<HTMLElement>('main th, main button, main summary, main label')) {
          if (!shown(el) || el.querySelector('input,select,textarea,svg')) continue
          const label = el.textContent?.trim() || ''
          if (!/^[\u4e00-\u9fff]{2,8}$/.test(label)) continue
          const range = document.createRange(); range.selectNodeContents(el)
          const rects = [...range.getClientRects()].filter(r => r.width > 0 && r.height > 0)
          const rows = new Set(rects.map(r => Math.round(r.top)))
          const size = Number.parseFloat(getComputedStyle(el).fontSize)
          if (rows.size > 1 && rects.every(r => r.width < size * 1.7)) result.push(`vertical short label: ${label} .${el.className}`)
        }
        for (const el of document.querySelectorAll<HTMLElement>('main input:not([type=hidden]):not([type=radio]):not([type=checkbox]):not([type=file]), main select')) {
          if (!shown(el)) continue
          const style = getComputedStyle(el)
          const rect = el.getBoundingClientRect()
          const minimumHeight = Number.parseFloat(style.fontSize) + Number.parseFloat(style.paddingTop) + Number.parseFloat(style.paddingBottom)
          if (rect.width < 44 || rect.height + 1 < minimumHeight) result.push(`undersized control: ${el.id || el.tagName} ${Math.round(rect.width)}×${Math.round(rect.height)}`)
        }
        return result
      })
      observations.push({ path, width, issues })
      if (issues.length || [0, 6, 15, 23, 29].includes(index)) {
        await page.screenshot({ path: info.outputPath(`layout-${index}-${width}.png`), fullPage: true })
      }
    }
  }
  writeFileSync(info.outputPath('layout-findings.json'), JSON.stringify(observations, null, 2))
  const failures = observations.filter(row => row.issues.length)
  expect(failures, `${failures.length} page/viewport states have text or control layout issues`).toEqual([])
})

test('retired course KC import URL redirects to the unified import view', async ({ page }) => {
  await page.goto('/login/')
  await page.getByLabel('账号或学号').fill('teacher')
  await page.getByLabel('密码').fill('teacher123')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL(/\/teacher\/courses\/$/)
  await page.goto('/teacher/courses/1/banks/1/kc-import/')
  await expect(page).toHaveURL(/\/teacher\/courses\/1\/banks\/1\/import\/$/)
  await expect(page.getByRole('heading', { name: '导入题库', exact: true })).toBeVisible()
})


test('long names and mobile question-bank actions remain usable', async ({ page }, info) => {
  await page.goto('/login/', { waitUntil: 'commit' })
  const accountField = page.getByLabel('账号或学号')
  await expect(accountField).toBeVisible()
  await accountField.fill('teacher')
  await page.getByLabel('密码').fill('teacher123')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL(/\/teacher\/courses\/$/)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/teacher/courses/1/banks/')
  await expect(page.locator('.kt-qb-mobile-list [data-bank-archive]').first()).toBeVisible()
  await page.evaluate(() => {
    const title = document.querySelector('main h1')
    const user = document.querySelector('.kt-user-name strong')
    const bank = document.querySelector('.kt-qb-mobile-list li strong')
    if (title) title.textContent = '中文长标题与连续英文编号_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'.repeat(3)
    if (user) user.textContent = '非常长的教师显示姓名与研究团队名称'.repeat(3)
    if (bank) bank.textContent = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'.repeat(6)
  })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
  await page.screenshot({ path: info.outputPath('long-bank-name-390.png'), fullPage: true })
  await page.goto('/teacher/courses/1/banks/?tab=course-kcs')
  await expect(page.locator('.kt-qb-mobile-list [data-concept-archive]').first()).toBeVisible()
  await page.locator('.kt-qb-mobile-list [data-concept-rename]').first().click()
  const editor = page.locator('.kt-qb-mobile-list [data-concept-rename-form]').first()
  await expect(editor.getByLabel('知识点名称')).toBeVisible()
  await editor.getByLabel('知识点名称').fill('用于布局检查的长知识点名称'.repeat(8))
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
  await page.screenshot({ path: info.outputPath('kc-mobile-editor-390.png'), fullPage: true })
})

test('student records and feedback keep readable controls across responsive boundaries', async ({ page }, info) => {
  test.setTimeout(180_000)
  await page.goto('/login/', { waitUntil: 'commit' })
  await page.getByLabel('账号或学号').fill('S002')
  await page.getByLabel('密码').fill('student123')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL(/\/student\/$/)
  await page.goto('/student/courses/1/')
  const feedbackTask = page.locator('.kt-student-task-row').filter({
    has: page.getByRole('heading', { name: '完整反馈视觉验证', exact: true }),
  })
  await expect(feedbackTask).toBeVisible()
  const startAction = feedbackTask.getByRole('button', { name: /^(开始作业|继续作答)$/ })
  if (await startAction.isVisible()) {
    await startAction.click()
    const answer = page.locator('.kt-assessment-question [name="answer"]').first()
    await expect(answer).toBeEnabled()
    const draftSaved = page.waitForResponse(response => response.url().includes('/draft/') && response.ok())
    await answer.check()
    await draftSaved
    await page.getByRole('button', { name: '确认提交本题', exact: true }).click()
    await Promise.all([
      page.waitForURL(/\/student\/sessions\/\d+\/complete\/$/),
      page.getByRole('dialog').getByRole('button', { name: '提交并锁定', exact: true }).click(),
    ])
  }
  await page.goto('/student/courses/1/learning/')
  const result = page.getByRole('link', { name: '查看作答详情', exact: true }).first()
  await expect(result).toBeVisible()
  const resultPath = (await result.getAttribute('href'))!
  await result.click()
  const question = page.getByRole('link', { name: '查看题目反馈', exact: true }).first()
  await expect(question).toBeVisible()
  const questionPath = (await question.getAttribute('href'))!
  const paths = ['/student/', '/student/courses/1/', '/student/courses/1/learning/', resultPath, questionPath]
  for (const [index, path] of paths.entries()) {
    for (const width of [1440, 1024, 768, 720, 390]) {
      await page.setViewportSize({ width, height: width === 720 ? 450 : 900 })
      await page.goto(path)
      await page.waitForLoadState('networkidle')
      await expect(page.locator('main h1').first()).toBeVisible()
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `${path} @ ${width}`).toBe(true)
      if (path === questionPath) {
        await expect(page.getByRole('button', { name: '展开正确答案', exact: true })).toBeVisible()
        await expect(page.getByRole('button', { name: '展开解析', exact: true })).toBeVisible()
      }
      await page.screenshot({ path: info.outputPath(`student-${index}-${width}.png`), fullPage: true })
    }
  }
})
