import { expect, test, type Page } from '@playwright/test'

const demoExperimentName = '演示交互数据采集'

function legacyDemoTask(page: Page) {
  return page.locator('.student-experiment').filter({
    has: page.getByRole('heading', { name: demoExperimentName, exact: true }),
  })
}

function vueDemoTask(page: Page) {
  return page.locator('.kt-student-task-card').filter({
    has: page.getByRole('heading', { name: demoExperimentName, exact: true }),
  })
}

async function loginStudent(page: Page, origin: string, account = 'S001') {
  await page.goto(`${origin}/login/`)
  await page.getByLabel('账号或学号').fill(account)
  await page.getByLabel('密码').fill('student123')
  await page.getByRole('button', { name: '登录' }).click()
  await expect(page).toHaveURL(new RegExp(`${origin}/student/`))
}

async function answerCurrentQuestion(page: Page) {
  await page.locator('#question-form, .kt-assessment-question').first().waitFor({ state: 'visible' })
  if (await page.locator('.kt-assessment-question').count()) {
    const question = page.locator('.kt-assessment-question')
    const answer = question.locator('[name="answer"]').first()
    const inputType = await answer.getAttribute('type')
    const inputMode = await answer.getAttribute('inputmode')
    const draftSaved = page.waitForResponse(
      (response) => response.url().includes('/draft/') && response.ok(),
    )
    if (inputType === 'radio' || inputType === 'checkbox') {
      await answer.check()
    } else {
      await answer.fill(inputMode === 'decimal' ? '3.14' : '知识追踪')
    }
    await draftSaved
    await page.getByRole('button', { name: '确认提交本题' }).click()
    const currentUrl = page.url()
    await Promise.all([
      page.waitForURL((url) => url.toString() !== currentUrl),
      page.getByRole('dialog').getByRole('button', { name: '提交并锁定' }).click(),
    ])
    return
  }
  const form = page.locator('#question-form')
  const type = await form.getAttribute('data-question-type')
  const draftSaved = page.waitForResponse(
    (response) => response.url().includes('/draft/') && response.ok(),
  )
  if (type === 'single_choice' || type === 'true_false') {
    await form.locator('input[name="answer"]').first().check()
  } else if (type === 'multiple_choice') {
    await form.locator('input[name="answer"]').first().check()
  } else {
    await form.locator('[name="answer"]').fill(type === 'numeric' ? '3.14' : '知识追踪')
  }
  await draftSaved
  await page.getByRole('button', { name: '确认提交本题' }).click()
  const currentUrl = page.url()
  await Promise.all([
    page.waitForURL((url) => url.toString() !== currentUrl),
    page.locator('#submit-confirm-dialog').getByRole('button', { name: '提交并锁定' }).click(),
  ])
}

test('student flag OFF keeps the legacy task template', async ({ page }) => {
  await loginStudent(page, 'http://127.0.0.1:8013')
  await page.goto('http://127.0.0.1:8013/student/courses/1/')
  await expect(page.locator('.student-experiment-list')).toBeVisible()
  await expect(legacyDemoTask(page).getByText(/^\d+ 题$/)).toBeVisible()
  await expect(page.locator('#kt-app')).toHaveCount(0)
})

test('student flag ON mounts courses, task cards, and learning cards on existing URLs', async ({ page }) => {
  test.setTimeout(300_000)
  await loginStudent(page, 'http://127.0.0.1:8014', 'S002')
  await expect(page.getByRole('heading', { name: '我的课程' })).toBeVisible({ timeout: 90_000 })
  await page.getByRole('link', { name: '进入课程' }).click()
  await expect(page).toHaveURL(/\/student\/courses\/1\/$/)
  await expect(page.getByRole('heading', { name: '实验任务' })).toBeVisible({ timeout: 90_000 })
  await expect(vueDemoTask(page)).toBeVisible()
  await expect(vueDemoTask(page).getByText(/^0 \/ \d+$/)).toBeVisible()
  await expect(page.getByText('正确', { exact: true })).toHaveCount(0)
  await expect(page.getByText('错误', { exact: true })).toHaveCount(0)

  await page.getByRole('link', { name: '我的学习' }).click()
  await expect(page.getByRole('heading', { name: '我的学习' })).toBeVisible({ timeout: 90_000 })
  await expect(page.locator('table')).toHaveCount(0)
  await expect(page.getByText('排名')).toHaveCount(0)
})

test('student user allowlist mounts the Vue dashboard', async ({ page }) => {
  test.setTimeout(120_000)
  await loginStudent(page, 'http://127.0.0.1:8015')
  await expect(page.getByRole('heading', { name: '我的课程' })).toBeVisible({ timeout: 90_000 })
  await expect(page.locator('#kt-app')).toBeVisible()
})

test('student starts the exact task, completes it in the Vue runtime, and sees the trimmed result', async ({ browserName, page }, testInfo) => {
  test.setTimeout(420_000)
  test.skip(browserName !== 'chromium', 'One browser covers the destructive student submission journey.')
  await loginStudent(page, 'http://127.0.0.1:8014')
  await page.goto('http://127.0.0.1:8014/student/courses/1/')
  const progress = await vueDemoTask(page).getByText(/^0 \/ \d+$/).textContent()
  const questionCount = Number(progress?.split('/')[1].trim())
  expect(questionCount).toBeGreaterThan(0)
  await vueDemoTask(page).getByRole('button', { name: /^(开始实验|继续作答)$/ }).click()
  await expect(page).toHaveURL(/\/student\/sessions\/\d+\/questions\/\d+\/$/)
  await page.locator('.kt-assessment-question').waitFor({ state: 'visible' })

  for (let index = 0; index < questionCount; index += 1) {
    await answerCurrentQuestion(page)
    if (index < questionCount - 1) {
      await expect(page).toHaveURL(/\/student\/sessions\/\d+\/questions\/\d+\/$/)
    }
  }
  await expect(page).toHaveURL(/\/student\/sessions\/\d+\/complete\/$/)
  await expect(page.getByText('已完成', { exact: true })).toBeVisible()

  await page.goto('http://127.0.0.1:8014/student/courses/1/learning/')
  await page.getByRole('link', { name: '查看实验记录' }).click()
  await expect(page.getByRole('heading', { name: '演示交互数据采集' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '得分' })).toBeVisible()
  await expect(page.getByText('正确答案')).toHaveCount(0)
  for (const viewport of [
    { name: 'result-desktop-1440x900', width: 1440, height: 900 },
    { name: 'result-mobile-390x844', width: 390, height: 844 },
    { name: 'result-zoom-200-percent-equivalent', width: 720, height: 450 },
  ]) {
    await page.setViewportSize(viewport)
    expect(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)).toBe(false)
    await page.screenshot({ path: testInfo.outputPath(`${viewport.name}.png`), fullPage: true })
  }
})

test('assessment runtime retains an offline answer and saves it after reconnecting', async ({ browserName, context, page }) => {
  test.setTimeout(300_000)
  test.skip(browserName !== 'chromium', 'One browser covers the network transition contract.')
  await loginStudent(page, 'http://127.0.0.1:8014', 'S003')
  await page.goto('http://127.0.0.1:8014/student/courses/1/')
  await vueDemoTask(page).getByRole('button', { name: /^(开始实验|继续作答)$/ }).click()
  await page.locator('.kt-assessment-question').waitFor({ state: 'visible' })

  await context.setOffline(true)
  const answer = page.locator('.kt-assessment-question [name="answer"]').first()
  const inputType = await answer.getAttribute('type')
  const inputMode = await answer.getAttribute('inputmode')
  const checkable = inputType === 'radio' || inputType === 'checkbox'
  const offlineValue = inputMode === 'decimal' ? '3.14' : '离线草稿'
  if (checkable) await answer.check()
  else await answer.fill(offlineValue)
  await expect(page.locator('[data-connectivity-status]')).toContainText('当前离线')
  if (checkable) await expect(answer).toBeChecked()
  else await expect(answer).toHaveValue(offlineValue)

  const draftSaved = page.waitForResponse(
    (response) => response.url().includes('/draft/') && response.ok(),
  )
  await context.setOffline(false)
  await draftSaved

  await expect(page.locator('[data-save-status]')).toHaveText('草稿已保存')
  if (checkable) await expect(answer).toBeChecked()
  else await expect(answer).toHaveValue(offlineValue)
})

test('assessment runtime reflows at desktop, mobile, and 200 percent zoom equivalent', async ({ browserName, page }, testInfo) => {
  test.setTimeout(300_000)
  test.skip(browserName !== 'chromium', 'One rendering engine provides the required viewport evidence.')
  await loginStudent(page, 'http://127.0.0.1:8014', 'S002')
  await page.goto('http://127.0.0.1:8014/student/courses/1/')
  await vueDemoTask(page).getByRole('button', { name: /^(开始实验|继续作答)$/ }).click()
  await page.locator('.kt-assessment-question').waitFor({ state: 'visible' })

  for (const viewport of [
    { name: 'assessment-desktop-1440x900', width: 1440, height: 900 },
    { name: 'assessment-mobile-390x844', width: 390, height: 844 },
    { name: 'assessment-zoom-200-percent-equivalent', width: 720, height: 450 },
  ]) {
    await page.setViewportSize(viewport)
    expect(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)).toBe(false)
    if (viewport.width <= 720) {
      await expect(page.locator('[data-mobile-assessment-header]')).toBeVisible()
    } else {
      await expect(page.locator('.kt-assessment-nav')).toBeVisible()
    }
    await page.screenshot({ path: testInfo.outputPath(`${viewport.name}.png`), fullPage: true })
  }

  await page.setViewportSize({ width: 390, height: 844 })
  await page.locator('[data-mobile-navigation-trigger]').click()
  await expect(page.getByRole('dialog', { name: '题目导航' })).toBeVisible()
  await page.screenshot({ path: testInfo.outputPath('assessment-mobile-drawer-390x844.png'), fullPage: true })
})

test('student pages reflow without horizontal page overflow', async ({ browserName, page }, testInfo) => {
  test.setTimeout(600_000)
  test.skip(browserName !== 'chromium', 'One rendering engine is sufficient for viewport evidence.')
  await loginStudent(page, 'http://127.0.0.1:8014', 'S002')

  for (const viewport of [
    { name: 'student-desktop-1440x900', width: 1440, height: 900 },
    { name: 'student-mobile-390x844', width: 390, height: 844 },
    { name: 'student-zoom-200-percent-equivalent', width: 720, height: 450 },
  ]) {
    await page.setViewportSize(viewport)
    await page.goto('http://127.0.0.1:8014/student/courses/1/')
    await expect(page.getByRole('heading', { name: '实验任务' })).toBeInViewport()
    await expect(page.getByText('演示交互数据采集')).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)).toBe(false)
    await page.screenshot({ path: testInfo.outputPath(`task-${viewport.name}.png`), fullPage: true })

    await page.goto('http://127.0.0.1:8014/student/courses/1/learning/')
    await expect(page.getByRole('heading', { name: '我的学习' })).toBeInViewport()
    await expect(page.getByRole('heading', { name: '实验完成' })).toBeVisible()
    await expect(page.getByText('演示交互数据采集')).toBeVisible()
    await expect(page.getByText('正在加载学习记录…')).toHaveCount(0)
    expect(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)).toBe(false)
    await page.screenshot({ path: testInfo.outputPath(`learning-${viewport.name}.png`), fullPage: true })
  }
})
