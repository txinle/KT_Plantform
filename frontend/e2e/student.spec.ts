import { expect, test, type Page } from '@playwright/test'

const demoExperimentName = '演示交互数据采集'

function vueDemoTask(page: Page) {
  return page.locator('.kt-student-task-row').filter({
    has: page.getByRole('heading', { name: demoExperimentName, exact: true }),
  })
}

function vueFullFeedbackTask(page: Page) {
  return page.locator('.kt-student-task-row').filter({
    has: page.getByRole('heading', { name: '完整反馈视觉验证', exact: true }),
  })
}

async function loginStudent(page: Page, origin: string, account = 'S001') {
  await page.goto(`${origin}/login/`, { waitUntil: 'commit' })
  const accountField = page.getByLabel('账号或学号')
  await expect(accountField).toBeVisible()
  await accountField.fill(account)
  await page.getByLabel('密码').fill('student123')
  await page.getByRole('button', { name: '登录' }).click()
  await expect(page).toHaveURL(new RegExp(`${origin}/student/`))
}

async function answerCurrentQuestion(page: Page) {
  const question = page.locator('.kt-assessment-question')
  await question.waitFor({ state: 'visible' })
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
}

test('student courses, task cards, and learning cards mount on their business URLs', async ({ page }) => {
  test.setTimeout(300_000)
  await loginStudent(page, 'http://127.0.0.1:8000', 'S002')
  await expect(page.getByRole('heading', { name: '我的课程' })).toBeVisible({ timeout: 90_000 })
  await page.locator('a.kt-button[href="/student/courses/1/"]').click()
  await expect(page).toHaveURL(/\/student\/courses\/1\/$/)
  await expect(page.getByRole('heading', { name: '作业', exact: true })).toBeVisible({ timeout: 90_000 })
  await expect(vueDemoTask(page)).toBeVisible()
  await expect(vueDemoTask(page).getByText(/^0 \/ \d+$/)).toBeVisible()
  await expect(page.getByText('正确', { exact: true })).toHaveCount(0)
  await expect(page.getByText('错误', { exact: true })).toHaveCount(0)

  await page.getByRole('link', { name: '班级中心' }).click()
  await expect(page.getByRole('heading', { name: '班级中心', exact: true })).toBeVisible({ timeout: 90_000 })
  await expect(page.locator('table')).toHaveCount(0)
  await expect(page.getByText('排名')).toHaveCount(0)
})

test('student completes the isolated full-feedback task and sees item and question feedback', async ({ browserName, page }, testInfo) => {
  test.setTimeout(420_000)
  test.skip(browserName !== 'chromium', 'One browser covers the destructive student submission journey.')
  await loginStudent(page, 'http://127.0.0.1:8000')
  await page.goto('http://127.0.0.1:8000/student/courses/1/')
  const progress = await vueFullFeedbackTask(page).getByText(/^0 \/ \d+$/).textContent()
  const questionCount = Number(progress?.split('/')[1].trim())
  expect(questionCount).toBe(1)
  await vueFullFeedbackTask(page).getByRole('button', { name: /^(开始作业|继续作答)$/ }).click()
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

  await page.goto('http://127.0.0.1:8000/student/courses/1/learning/')
  const fullFeedbackCard = page.locator('.kt-learning-experiment-row').filter({
    has: page.getByRole('heading', { name: '完整反馈视觉验证', exact: true }),
  })
  await fullFeedbackCard.getByRole('link', { name: '查看作答详情' }).click()
  await expect(page.getByRole('heading', { name: '完整反馈视觉验证' })).toBeVisible()
  await expect(page.locator('.kt-feedback-metric')).toHaveCount(3)
  await expect(page.locator('.kt-feedback-table tbody tr')).toHaveCount(1)
  await expect(page.locator('.kt-course-bar')).toHaveCount(0)
  const resultRow = page.locator('.kt-feedback-table tbody tr')
  await page.getByRole('link', { name: '返回班级中心' }).focus()
  await page.keyboard.press('Tab')
  await expect(resultRow).toBeFocused()
  await expect(resultRow).toHaveCSS('outline-style', 'solid')
  for (const viewport of [
    { name: 'result-reference-1448x1086', width: 1448, height: 1086 },
    { name: 'result-desktop-1440x900', width: 1440, height: 900 },
    { name: 'result-mobile-390x844', width: 390, height: 844 },
    { name: 'result-zoom-200-percent-equivalent', width: 720, height: 450 },
  ]) {
    await page.setViewportSize(viewport)
    expect(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)).toBe(false)
    await page.screenshot({ path: testInfo.outputPath(`${viewport.name}.png`), fullPage: true })
  }

  await page.setViewportSize({ width: 1448, height: 1086 })
  await page.getByRole('link', { name: '查看题目反馈' }).click()
  await expect(page.getByRole('heading', { name: /^Q\d+$/ })).toBeVisible()
  await expect(page.locator('.kt-feedback-option')).toHaveCount(4)
  await expect(page.getByText('我的作答', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '展开解析' })).toBeDisabled()
  await page.getByRole('button', { name: '展开正确答案' }).click()
  await expect(page.getByText('正确答案', { exact: true })).toHaveCount(2)
  await page.getByRole('button', { name: '展开解析' }).click()
  await expect(page.getByText('答案解析', { exact: true })).toBeVisible()
  await expect(page.locator('.kt-feedback-answer-panel--analysis p')).toBeVisible()
  for (const viewport of [
    { name: 'question-reference-1448x1086', width: 1448, height: 1086 },
    { name: 'question-desktop-1440x900', width: 1440, height: 900 },
    { name: 'question-mobile-390x844', width: 390, height: 844 },
    { name: 'question-zoom-200-percent-equivalent', width: 720, height: 450 },
  ]) {
    await page.setViewportSize(viewport)
    expect(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)).toBe(false)
    await page.screenshot({ path: testInfo.outputPath(`${viewport.name}.png`), fullPage: true })
  }
})

test('assessment runtime retains an offline answer and saves it after reconnecting', async ({ browserName, context, page }) => {
  test.setTimeout(300_000)
  test.skip(browserName !== 'chromium', 'One browser covers the network transition contract.')
  await loginStudent(page, 'http://127.0.0.1:8000', 'S003')
  await page.goto('http://127.0.0.1:8000/student/courses/1/')
  await vueDemoTask(page).getByRole('button', { name: /^(开始作业|继续作答)$/ }).click()
  await page.locator('.kt-assessment-question').waitFor({ state: 'visible' })

  const answer = page.locator('.kt-assessment-question [name="answer"]').first()
  await expect(answer).toBeEnabled({ timeout: 30_000 })
  await context.setOffline(true)
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
  await loginStudent(page, 'http://127.0.0.1:8000', 'S002')
  await page.goto('http://127.0.0.1:8000/student/courses/1/')
  await vueDemoTask(page).getByRole('button', { name: /^(开始作业|继续作答)$/ }).click()
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
  await loginStudent(page, 'http://127.0.0.1:8000', 'S002')

  for (const viewport of [
    { name: 'student-desktop-1440x900', width: 1440, height: 900 },
    { name: 'student-mobile-390x844', width: 390, height: 844 },
    { name: 'student-zoom-200-percent-equivalent', width: 720, height: 450 },
  ]) {
    await page.setViewportSize(viewport)
    await page.goto('http://127.0.0.1:8000/student/courses/1/')
    await expect(page.getByRole('heading', { name: '作业', exact: true })).toBeInViewport()
    await expect(page.getByText('演示交互数据采集')).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)).toBe(false)
    await page.screenshot({ path: testInfo.outputPath(`task-${viewport.name}.png`), fullPage: true })

    await page.goto('http://127.0.0.1:8000/student/courses/1/learning/')
    await expect(page.getByRole('heading', { name: '班级中心', exact: true })).toBeInViewport()
    await expect(page.getByText('实验完成', { exact: true })).toBeVisible()
    await expect(page.getByText('演示交互数据采集')).toBeVisible()
    await expect(page.getByText('正在加载学习记录…')).toHaveCount(0)
    expect(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)).toBe(false)
    await page.screenshot({ path: testInfo.outputPath(`learning-${viewport.name}.png`), fullPage: true })
  }
})
