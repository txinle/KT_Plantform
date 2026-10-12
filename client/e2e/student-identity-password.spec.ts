import { expect, test, type Page, type TestInfo } from '@playwright/test'
import { openLoginPage } from './login-navigation'
import { acceptResearchConsentIfRequired } from './research-consent'

async function login(page: Page, username: string, password: string) {
  await openLoginPage(page)
  await page.getByLabel('账号或学号', { exact: true }).fill(username)
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: '登录', exact: true }).click()
}

async function captureResponsive(page: Page, testInfo: TestInfo, prefix: string) {
  for (const [name, width, height] of [['desktop', 1440, 900], ['mobile', 390, 844]] as const) {
    await page.setViewportSize({ width, height })
    await page.screenshot({ path: testInfo.outputPath(`${prefix}-${name}.png`), fullPage: true })
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width + 1)
  }
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.evaluate(() => { document.documentElement.style.zoom = '2' })
  await page.screenshot({ path: testInfo.outputPath(`${prefix}-200-percent.png`), fullPage: true })
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(1441)
  await page.evaluate(() => { document.documentElement.style.zoom = '' })
}

test('corrects a removed student, preserves identity and password, and allows optional password changes', async ({ page, browser }, testInfo) => {
  test.setTimeout(120_000)
  const number = `ID${Date.now()}`
  const correctedNumber = `${number}N`
  const base = '/api/v1/teacher/accounts/courses/1/classes/1/'
  await login(page, 'teacher', 'teacher123')
  await expect(page).toHaveURL(/\/teacher\/courses\/$/)
  await page.goto('/teacher/courses/1/classes/1/edit/', { waitUntil: 'commit' })
  await page.getByRole('button', { name: '手动添加', exact: true }).click()
  await page.locator('[data-manual-enrollment] input[name="student_number"]').fill(number)
  await page.locator('[data-manual-enrollment] input[name="display_name"]').fill('录入错误姓名')
  const createdResponse = page.waitForResponse((response) => response.url().endsWith(`${base}students/manual/`) && response.status() === 201)
  await page.getByRole('button', { name: '添加到当前班级', exact: true }).click()
  const originalId = (await (await createdResponse).json()).data.student.id
  await page.getByRole('button', { name: '班级与成员', exact: true }).click()
  let row = page.getByRole('row').filter({ hasText: number })
  await row.getByRole('button', { name: '移出课程', exact: true }).click()
  await page.getByRole('button', { name: '确认移出课程', exact: true }).click()
  await expect(row).toHaveCount(0)
  await page.getByRole('button', { name: '手动添加', exact: true }).click()
  await page.locator('[data-manual-enrollment] input[name="student_number"]').fill(number)
  await page.locator('[data-manual-enrollment] input[name="display_name"]').fill('更正后姓名')
  await page.getByRole('button', { name: '添加到当前班级', exact: true }).click()
  const conflict = page.getByRole('dialog', { name: '确认更新已有学生姓名' })
  await expect(conflict).toContainText('录入错误姓名')
  await expect(conflict).toContainText('更正后姓名')
  await captureResponsive(page, testInfo, 'name-conflict')
  await conflict.getByRole('button', { name: '取消', exact: true }).click()
  let management = (await (await page.request.get(`${base}manage/`)).json()).data
  expect(management.enrollments.some((item: { student: { id: number } }) => item.student.id === originalId)).toBe(false)
  await page.getByRole('button', { name: '添加到当前班级', exact: true }).click()
  const confirmedResponse = page.waitForResponse((response) => response.url().endsWith(`${base}students/manual/`) && response.status() === 201)
  await page.locator('[data-confirm-student-name]').click()
  expect((await (await confirmedResponse).json()).data.student.id).toBe(originalId)
  await page.getByRole('button', { name: '班级与成员', exact: true }).click()
  row = page.getByRole('row').filter({ hasText: number })
  await row.locator('[data-edit-student-identity]').click()
  const editor = page.locator('[data-student-identity-form]')
  await editor.scrollIntoViewIfNeeded()
  await captureResponsive(page, testInfo, 'identity-edit')
  await editor.locator('input[name="student_number"]').fill(correctedNumber)
  await editor.locator('input[name="display_name"]').fill('更正后姓名')
  await editor.getByRole('button', { name: '保存学生信息', exact: true }).click()
  row = page.getByRole('row').filter({ hasText: correctedNumber })
  await expect(row).toContainText('更正后姓名')
  management = (await (await page.request.get(`${base}manage/`)).json()).data
  expect(management.enrollments.find((item: { student: { id: number } }) => item.student.id === originalId).student.student_number).toBe(correctedNumber)

  const studentContext = await browser.newContext({ baseURL: testInfo.project.use.baseURL, viewport: { width: 1440, height: 900 } })
  try {
    const studentPage = await studentContext.newPage()
    // Editing the number must preserve even a not-yet-changed initial password.
    await login(studentPage, correctedNumber, `S${number}`)
    await acceptResearchConsentIfRequired(studentPage)
    await expect(studentPage.getByRole('heading', { name: '建议修改初始密码' })).toBeVisible()
    await studentPage.getByRole('button', { name: '暂不修改', exact: true }).click()
    expect((await studentPage.request.get('/api/v1/student/courses/')).status()).toBe(200)
    await studentPage.goto('/student/', { waitUntil: 'commit' })
    await expect(studentPage).toHaveURL(/\/student\/$/)
    await studentPage.goto('/password/change/', { waitUntil: 'commit' })
    for (const [name, width, height] of [['desktop', 1440, 900], ['mobile', 390, 844]] as const) {
      await studentPage.setViewportSize({ width, height })
      await expect(studentPage.getByRole('heading', { name: '修改密码' })).toBeVisible()
      await studentPage.screenshot({ path: testInfo.outputPath(`password-change-${name}.png`), fullPage: true })
      expect(await studentPage.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width + 1)
    }
    await studentPage.setViewportSize({ width: 1440, height: 900 })
    await studentPage.evaluate(() => { document.documentElement.style.zoom = '2' })
    await studentPage.screenshot({ path: testInfo.outputPath('password-change-200-percent.png'), fullPage: true })
    expect(await studentPage.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(1441)
    await studentPage.evaluate(() => { document.documentElement.style.zoom = '' })
    await studentPage.getByLabel('当前密码', { exact: true }).fill(`S${number}`)
    await studentPage.getByLabel('新密码', { exact: true }).fill('Student-personal-password-1')
    await studentPage.getByLabel('再次输入新密码', { exact: true }).fill('Student-personal-password-1')
    await studentPage.getByRole('button', { name: '保存新密码', exact: true }).click()
    await expect(studentPage).toHaveURL(/\/student\/$/)
    expect((await (await studentPage.request.get('/api/v1/auth/session/')).json()).data.user.uses_initial_password).toBe(false)

    await row.locator('[data-course-reset-password]').click()
    await expect(page.locator('[aria-labelledby="reset-password-title"]')).toContainText(`S${correctedNumber}`)
    await page.locator('[data-course-reset-password-confirm]').click()
    await expect(page.getByText('的全局登录密码已重置。', { exact: false })).toBeVisible()
    expect((await studentPage.request.get('/api/v1/auth/session/')).status()).toBe(401)
    await login(studentPage, correctedNumber, `S${correctedNumber}`)
    await acceptResearchConsentIfRequired(studentPage)
    await expect(studentPage.getByRole('heading', { name: '建议修改初始密码' })).toBeVisible()
  } finally {
    await studentContext.close()
  }
})
