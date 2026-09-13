import { expect, test, type Page, type TestInfo } from '@playwright/test'

async function login(page: Page, user: string, password: string) {
  await page.goto('/login/')
  await page.getByLabel('账号或学号').fill(user)
  await page.getByLabel('密码').fill(password)
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.locator('.kt-workspace')).toBeVisible()
  await page.waitForLoadState('networkidle')
}

async function inspect(page: Page, info: TestInfo, name: string) {
  await expect(page.locator('.kt-workspace-main')).toBeVisible()
  await page.waitForLoadState('networkidle')
  for (const size of [{ width: 2534, height: 1088 }, { width: 1440, height: 900 }, { width: 390, height: 844 }, { width: 720, height: 450 }]) {
    await page.setViewportSize(size)
    await page.screenshot({ path: info.outputPath(`${name}-${size.width}.png`), fullPage: true })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `${name} ${size.width} overflow`).toBe(true)
    if (size.width >= 1024) {
      const sidebar = await page.locator('.kt-workspace-sidebar').boundingBox()
      const main = await page.locator('.kt-workspace-main').boundingBox()
      const expectedSidebarWidth = await page.locator('.kt-workspace').evaluate(
        element => element.closest('.kt-teacher-experiments')
          ? 194
          : (element.querySelector('.kt-workspace-navigation--teacher-course') ? 236 : 224),
      )
      expect(sidebar?.width).toBe(expectedSidebarWidth)
      expect(main!.x).toBeGreaterThanOrEqual(sidebar!.x + sidebar!.width)
    }
  }
  const trigger = page.locator('[data-workspace-menu]')
  await trigger.click()
  await expect(page.locator('.kt-workspace-sidebar')).toHaveAttribute('aria-modal', 'true')
  await page.keyboard.press('Escape')
  await expect(trigger).toBeFocused()
  await page.setViewportSize({ width: 1440, height: 900 })
}

test('teacher course and module pages share a left sidebar without losing existing actions', async ({ page }, info) => {
  test.setTimeout(180_000)
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await login(page, 'teacher', 'teacher123')
  for (const [name, path] of [
    ['teacher-courses', '/teacher/courses/'],
    ['teacher-overview', '/teacher/courses/1/'],
    ['teacher-create', '/teacher/courses/create/'],
    ['teacher-settings', '/teacher/courses/1/settings/'],
    ['teacher-banks', '/teacher/courses/1/banks/'],
    ['teacher-classes', '/teacher/courses/1/classes/'],
    ['teacher-experiments', '/teacher/courses/1/experiments/'],
    ['teacher-data', '/teacher/courses/1/data/'],
  ]) {
    await page.goto(path!)
    await inspect(page, info, name!)
  }
  await expect(page.locator('#workspace-secondary-nav')).toContainText('数据质量')
  await page.locator('#workspace-secondary-nav').getByRole('link', { name: '数据质量', exact: true }).click()
  await expect(page).toHaveURL(/\/data\/quality\//)
  await expect(page.locator('.kt-workspace-main')).toBeVisible()
  expect(errors).toEqual([])
})

test('experiment workspace switches cleanly at the 1024px desktop boundary', async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 900 })
  await login(page, 'teacher', 'teacher123')
  await page.goto('/teacher/courses/1/experiments/')
  await expect(page.getByRole('heading', { name: '实验', exact: true })).toBeVisible()

  const sidebar = await page.locator('.kt-workspace-sidebar').boundingBox()
  expect(sidebar?.width).toBe(194)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
})

test('student course cards, assignment list and class center reflow with their original links', async ({ browserName, page }, info) => {
  test.setTimeout(90_000)
  test.skip(browserName !== 'chromium', 'Chromium provides the responsive visual evidence for this multi-page flow.')
  await login(page, 'S002', 'student123')
  await inspect(page, info, 'student-courses')
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
    const saved = page.waitForResponse(response => response.url().includes('/draft/') && response.ok())
    await answer.check()
    await saved
    await page.getByRole('button', { name: '确认提交本题', exact: true }).click()
    await Promise.all([
      page.waitForURL(/\/student\/sessions\/\d+\/complete\/$/),
      page.getByRole('dialog').getByRole('button', { name: '提交并锁定', exact: true }).click(),
    ])
    await page.goto('/student/courses/1/')
  }
  await inspect(page, info, 'student-tasks')
  await page.locator('.kt-workspace-navigation').getByRole('link', { name: '班级中心', exact: true }).click()
  await expect(page).toHaveURL(/\/student\/courses\/1\/learning\//)
  await expect(page.getByRole('region', { name: '个人学习概览' })).toBeVisible()
  await inspect(page, info, 'student-center')
  await page.getByRole('link', { name: '查看作答详情', exact: true }).first().click()
  await expect(page.getByRole('complementary', { name: '题目目录' })).toBeVisible()
  await inspect(page, info, 'student-result')
  await page.getByRole('complementary', { name: '题目目录' }).getByRole('link').first().click()
  await expect(page.getByRole('button', { name: '展开正确答案', exact: true })).toBeVisible()
  await expect(page.getByRole('complementary', { name: '题目目录' })).toBeVisible()
  await expect(page.getByRole('alert')).toHaveCount(0)
  await inspect(page, info, 'student-feedback-closed')
  const directoryLinks = page.getByRole('complementary', { name: '题目目录' }).getByRole('link')
  for (const link of await directoryLinks.all()) expect((await link.boundingBox())!.height).toBeGreaterThanOrEqual(44)
})

test('course list keeps empty, failed and long-title states readable', async ({ page }, info) => {
  test.setTimeout(90_000)
  await page.route('**/api/v1/student/courses/', route => route.fulfill({ json: { items: [] } }))
  await login(page, 'S002', 'student123')
  await expect(page.getByRole('heading', { name: '暂无可进入课程' })).toBeVisible()
  await inspect(page, info, 'student-empty')
  await page.unroute('**/api/v1/student/courses/')
  await page.route('**/api/v1/student/courses/', route => route.fulfill({ status: 503, json: { detail: '课程暂时无法加载，请稍后重试。' } }))
  await page.reload()
  await expect(page.getByRole('alert')).toBeVisible()
  await inspect(page, info, 'student-error')
  await page.unroute('**/api/v1/student/courses/')
  await page.route('**/api/v1/student/courses/', route => route.fulfill({ json: { items: [{
    course_id: 2, course_name: '电子设计自动化技术概述与可编程逻辑器件基础：长课程标题界面验收样例',
    course_code: 'EDA-LONG-TITLE', course_term: '2026 秋季学期', course_status: 'active', class_name: '电子信息工程专业实验教学班',
  }] } }))
  await page.reload()
  await expect(page.getByRole('heading', { name: /电子设计自动化技术概述与可编程/ })).toBeVisible()
  await inspect(page, info, 'student-long-title')
})

test('Vue course forms create, save and reject stale edits through the existing server rules', async ({ page, context }, info) => {
  test.setTimeout(90_000)
  await login(page, 'teacher', 'teacher123')
  await page.goto('/teacher/courses/create/')
  const code = `UI-${Date.now()}`
  await page.getByLabel('课程名称', { exact: true }).fill('课程管理浏览器验收')
  await page.getByLabel('课程代码', { exact: true }).fill(code)
  await page.getByLabel('学期', { exact: true }).fill('UI-E2E')
  await page.getByRole('button', { name: '创建课程', exact: true }).click()
  await expect(page.getByRole('heading', { name: '课程管理浏览器验收', exact: true })).toBeVisible()
  const coursePath = new URL(page.url()).pathname
  await page.goto(`${coursePath}settings/`)
  await expect(page.getByLabel('课程名称', { exact: true })).toHaveValue('课程管理浏览器验收')
  const stalePage = await context.newPage()
  await stalePage.goto(`${coursePath}settings/`)
  await expect(stalePage.getByLabel('课程名称', { exact: true })).toHaveValue('课程管理浏览器验收')
  await page.getByLabel('课程名称', { exact: true }).fill('课程管理浏览器验收已保存')
  await page.getByRole('button', { name: '保存设置', exact: true }).click()
  await expect(page.getByRole('status')).toContainText('课程设置已保存')
  await stalePage.getByLabel('课程名称', { exact: true }).fill('不应覆盖较新设置')
  await stalePage.getByRole('button', { name: '保存设置', exact: true }).click()
  await expect(stalePage.getByRole('alert')).toContainText('课程设置已被其他管理者更新')
  await stalePage.close()
  await page.reload()
  await expect(page.getByLabel('课程名称', { exact: true })).toHaveValue('课程管理浏览器验收已保存')
  const archived = page.waitForResponse(response => (
    response.request().method() === 'POST'
    && response.url().endsWith(`${coursePath}archive/`)
  ))
  await page.getByRole('button', { name: '归档课程', exact: true }).click()
  expect((await archived).status()).toBe(200)
  await expect(page.getByRole('status')).toContainText('课程已归档并切换为只读')
  await page.goto(`${coursePath}settings/`)
  await expect(page.getByRole('button', { name: '保存设置', exact: true })).toBeDisabled()
  await expect(page.getByLabel('课程名称', { exact: true })).toBeDisabled()
  await expect(page.getByRole('button', { name: '归档课程', exact: true })).toHaveCount(0)
  await inspect(page, info, 'teacher-readonly')
})
