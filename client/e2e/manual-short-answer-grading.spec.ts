import { submitWholePaper } from './whole-paper'
import { expect, test, type Page } from '@playwright/test'
import { acceptResearchConsentIfRequired } from './research-consent'

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
  if (account !== 'teacher') await acceptResearchConsentIfRequired(page)
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
  await submitWholePaper(page)
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

test('grading columns contain long lists and keep compact question navigation usable', async ({ browserName, page }, testInfo) => {
  test.skip(browserName !== 'chromium', 'Layout regression is covered in Chromium.')
  const questions = Array.from({ length: 140 }, (_, i) => ({
    question_snapshot_id: i + 1, code: `Q${String(i + 1).padStart(4, '0')}`,
    text_summary: '不应在题目列表中重复显示的题干摘要',
    submitted_count: 50, pending_count: 50, graded_count: 0,
  }))
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname
    let data: unknown
    if (path === '/api/v1/auth/bootstrap/') {
      data = { csrf_token: 'layout-test', user: {
        id: 1, role: 'teacher', display_name: '验收教师', username: 'teacher', student_number: null,
        home_url: '/teacher/courses/', uses_initial_password: false,
        initial_password_reminder_pending: false, research_consent_required: false,
        research_consent_version: '',
      } }
    } else if (path === '/api/v1/courses/1/context/') {
      data = { id: 1, name: 'EDA技术', code: 'KT01', term: '2026年秋' }
    } else if (path === '/api/v1/teacher/class-tasks/1/grading/') {
      data = {
        task: { id: 1, experiment_id: 1, experiment_name: '简答题批改布局验收', class_id: 1, class_name: '验收班级' },
        totals: { enabled: true, short_answer_questions: 140, submitted: 7000, pending: 7000, graded: 0 },
        questions,
      }
    } else if (/\/grading\/questions\/\d+\/$/.test(path)) {
      const id = Number(path.split('/').at(-2))
      data = {
        question: { question_snapshot_id: id, code: questions[id - 1]!.code,
          text: '两个时钟过程分别给同一个寄存器赋值。指出风险并给出推荐重构方案。',
          correct_answer: '多个过程不能通过源码顺序确定优先级，应合并到同一时钟过程。',
          accepted_answers: [], explanation: '使用条件分支明确优先级，并验证所有输入条件。'.repeat(80) },
        submissions: {
          items: Array.from({ length: 50 }, (_, i) => ({
            submission_id: i + 1, student_id: i + 1, student_number: `S${i + 1}`, display_name: `学生${i + 1}`,
            final_answer: '两个 always 块会并发执行，应合并到一个时钟过程，用 if/else 明确优先级。',
            submitted_at: '2026-10-21T04:40:08Z', grading_status: 'pending', latest_grade_version: 1,
            current_is_correct: null, current_comment: '', graded_at: null, grader_name: '',
          })),
          page: { number: 1, size: 50, total_items: 50, total_pages: 1, start: 1, end: 50 },
        },
      }
    } else if (/\/grading\/submissions\/\d+\/$/.test(path)) {
      data = { task_progress: { enabled: true, short_answer_questions: 140, submitted: 7000, pending: 6999, graded: 1 } }
    } else {
      throw new Error(`Unexpected API call in layout test: ${path}`)
    }
    await route.fulfill({ json: { data, meta: { request_id: 'grading-layout-test' } } })
  })
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/teacher/courses/1/experiments/1/class-tasks/1/grading/')
  await expect(page.locator('[data-grading-row]')).toHaveCount(50)
  const navigation = page.getByRole('complementary', { name: '简答题列表' })
  await expect(navigation.getByRole('button').first()).toHaveText('Q0001待批 50')
  await expect(navigation.getByText('不应在题目列表中重复显示的题干摘要')).toHaveCount(0)
  await expect(navigation.getByText('已批改', { exact: false })).toHaveCount(0)
  const columns = await page.locator('.kt-grading-layout').evaluate(element => {
    const boxes = [...element.children].map(child => child.getBoundingClientRect())
    return boxes.map(box => ({ x: box.x, y: box.y, width: box.width, bottom: box.bottom }))
  })
  expect(columns[0]!.width).toBe(160)
  expect(columns[1]!.x).toBeGreaterThan(columns[0]!.x + columns[0]!.width)
  expect(columns[2]!.x).toBeGreaterThan(columns[1]!.x + columns[1]!.width)
  expect(Math.abs(columns[0]!.y - columns[2]!.y)).toBeLessThan(1)
  expect(Math.max(...columns.map(column => column.bottom))).toBeLessThanOrEqual(900)
  for (const selector of ['.kt-grading-question-list', '.kt-grading-reference', '.kt-grading-submissions']) {
    const state = await page.locator(selector).evaluate(element => {
      element.scrollTop = 100
      return { scrollTop: element.scrollTop, windowY: window.scrollY }
    })
    expect(state.scrollTop, selector).toBe(100)
    expect(state.windowY, selector).toBe(0)
    await page.locator(selector).evaluate(element => { element.scrollTop = 0 })
  }
  await page.locator('.kt-grading-reference, .kt-grading-submissions').evaluateAll(elements => {
    elements.forEach(element => { element.scrollTop = 100 })
  })
  await navigation.getByRole('button').first().focus()
  await page.keyboard.press('Tab')
  await page.keyboard.press('Enter')
  await expect(navigation.getByRole('button').nth(1)).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByRole('region', { name: '题目与参考' }).getByText('Q0002').first()).toBeVisible()
  await expect.poll(() => page.locator('.kt-grading-reference, .kt-grading-submissions')
    .evaluateAll(elements => elements.map(element => element.scrollTop))).toEqual([0, 0])
  await navigation.getByRole('button').nth(99).click()
  await page.locator('[data-grade="correct"]').first().click()
  await expect(page.getByText('判定已保存。', { exact: true })).toBeVisible()
  await expect(navigation.getByRole('button').nth(99)).toHaveAttribute('aria-pressed', 'true')
  await expect(navigation.getByRole('button').nth(99)).toBeInViewport()
  for (const viewport of [
    { name: 'three-column-desktop', width: 1440, height: 900 },
    { name: 'three-column-mobile', width: 390, height: 844 },
    { name: 'three-column-200-percent-equivalent', width: 720, height: 450 },
  ]) {
    await page.setViewportSize(viewport)
    expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false)
    await page.locator('[data-grade="correct"]').first().scrollIntoViewIfNeeded()
    await expect(page.locator('[data-grade="correct"]').first()).toBeInViewport()
    await page.evaluate(() => window.scrollTo(0, 0))
    await page.screenshot({ path: testInfo.outputPath(`${viewport.name}.png`), fullPage: false })
  }
})
