import { expect, test, type Page } from '@playwright/test'

const origin = 'http://127.0.0.1:8000'
const experimentName = '人工简答题批改验收'

async function login(page: Page, account: string, password: string) {
  if (!page.url().startsWith(`${origin}/login/`)) {
    await page.goto(`${origin}/login/`, { waitUntil: 'commit' })
  }
  await expect(page.getByLabel('账号或学号')).toBeVisible()
  await page.getByLabel('账号或学号').fill(account)
  await page.getByLabel('密码').fill(password)
  await page.getByRole('button', { name: '登录' }).click()
  await expect(page).toHaveURL(
    new RegExp(`${origin}/${account === 'teacher' ? 'teacher/courses' : 'student'}/`),
  )
}

test('student submits a short answer and teacher completes and revises manual grading', async ({ browserName, page }, testInfo) => {
  test.setTimeout(300_000)
  test.skip(browserName !== 'chromium', 'One browser covers the state-changing grading journey.')

  await login(page, 'S003', 'student123')
  await expect(page).toHaveURL(new RegExp(`${origin}/student/`))
  await page.goto(`${origin}/student/courses/1/`)
  const task = page.locator('.kt-student-task-row').filter({
    has: page.getByRole('heading', { name: experimentName, exact: true }),
  })
  await task.getByRole('button', { name: '开始作业' }).click()
  await expect(page).toHaveURL(/\/student\/sessions\/\d+\/questions\/\d+\/$/)
  const draftSaved = page.waitForResponse(response => response.url().includes('/draft/') && response.ok())
  await page.getByRole('textbox').fill('模型需要估计学生当前的知识掌握状态。')
  await draftSaved
  await page.getByRole('button', { name: '确认提交本题' }).click()
  await page.getByRole('dialog').getByRole('button', { name: '提交并锁定' }).click()
  await expect(page).toHaveURL(/\/student\/sessions\/\d+\/complete\/$/)

  await page.goto(`${origin}/student/courses/1/learning/`)
  const learningCard = page.locator('.kt-learning-experiment-row').filter({
    has: page.getByRole('heading', { name: experimentName, exact: true }),
  })
  await learningCard.getByRole('link', { name: '查看作答详情' }).click()
  await expect(page.getByText('待教师批改', { exact: true })).toBeVisible()
  await expect(page.locator('.kt-feedback-table tbody tr').getByText('—', { exact: true })).toBeVisible()

  await page.getByRole('button', { name: '退出' }).click()
  await expect(page).toHaveURL(new RegExp(`${origin}/login/`))
  await login(page, 'teacher', 'teacher123')
  await expect(page).toHaveURL(new RegExp(`${origin}/teacher/courses/`))
  await page.goto(`${origin}/teacher/courses/1/experiments/`)
  await page.getByRole('link', { name: experimentName, exact: true }).click()
  await page.getByRole('link', { name: /批改简答题 · 待批改 1/ }).click()
  await expect(page.getByRole('heading', { name: '简答题批改', exact: true })).toBeVisible()
  await expect(page.getByText('S003', { exact: true })).toBeVisible()
  await expect(page.getByText('模型需要估计学生当前的知识掌握状态。', { exact: true })).toBeVisible()

  for (const viewport of [
    { name: 'manual-grading-1440x900', width: 1440, height: 900 },
    { name: 'manual-grading-390x844', width: 390, height: 844 },
    { name: 'manual-grading-200-percent-equivalent', width: 720, height: 450 },
  ]) {
    await page.setViewportSize(viewport)
    expect(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)).toBe(false)
    await page.screenshot({ path: testInfo.outputPath(`${viewport.name}.png`), fullPage: true })
  }

  await page.setViewportSize({ width: 1440, height: 900 })
  await page.getByRole('button', { name: '判为正确' }).click()
  await expect(page.getByText('判定已保存。')).toBeVisible()
  await page.getByRole('button', { name: '已批改', exact: true }).click()
  await expect(page.getByText('当前判定：正确')).toBeVisible()
  await page.getByLabel('修改原因').fill('复核学生表述后调整')
  await page.getByRole('button', { name: '判为错误' }).click()
  await expect(page.getByText('判定已保存。')).toBeVisible()
  await page.getByRole('button', { name: '已批改', exact: true }).click()
  await expect(page.getByText('当前判定：错误')).toBeVisible()

  await page.getByRole('button', { name: '退出' }).click()
  await expect(page).toHaveURL(new RegExp(`${origin}/login/`))
  await login(page, 'S003', 'student123')
  await page.goto(`${origin}/student/courses/1/learning/`)
  await page.locator('.kt-learning-experiment-row').filter({
    has: page.getByRole('heading', { name: experimentName, exact: true }),
  }).getByRole('link', { name: '查看作答详情' }).click()
  await expect(page.getByText('错误', { exact: true })).toBeVisible()
  await expect(page.getByText('0.00 / 1.00', { exact: true })).toBeVisible()
})
