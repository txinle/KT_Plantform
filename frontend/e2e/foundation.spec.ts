import { expect, test, type Page } from '@playwright/test'
import { openLoginPage } from './login-navigation'

async function login(page: Page, origin: string) {
  await openLoginPage(page, origin)
  const account = page.getByLabel('账号或学号')
  await expect(account).toBeVisible()
  await account.fill('teacher')
  await page.getByLabel('密码').fill('teacher123')
  await page.getByRole('button', { name: '登录' }).click()
  await expect(page).toHaveURL(new RegExp(`${origin}/teacher/courses/`))
}

async function selectFirstKnowledgeComponent(page: Page) {
  const picker = page
    .locator('.kt-kc-config-panel', { hasText: '知识点范围' })
    .locator('details.kt-kc-picker')
  await picker.locator('summary').click()
  const inventoryUpdated = page.waitForResponse((response) => {
    if (response.request().method() !== 'POST'
      || !response.url().endsWith('/experiments/sampling-inventory/')) return false
    const payload = response.request().postDataJSON() as { kc_codes?: string[] }
    return Boolean(payload.kc_codes?.length)
  })
  await page
    .locator('.kt-kc-config-panel', { hasText: '知识点范围' })
    .locator('.kt-kc-tree-selector input[type="checkbox"]')
    .first()
    .check()
  expect((await inventoryUpdated).status()).toBe(200)
  await picker.locator('summary').click()
  await expect(picker).not.toHaveAttribute('open', '')
}

const unifiedQuestionImportHeaders = [
  '序号', '题目', '题型', '选项A', '选项B', '选项C', '选项D', '选项E', '选项F',
  '答案', '可接受答案', '解析', '难度', '一级知识点', '二级知识点', '三级知识点',
]

function unifiedQuestionImportCsv(rows: Array<{ sequence: number; text: string; root: string }>) {
  const escape = (value: string) => /[",\r\n]/.test(value) ? `"${value.replace(/"/g, '""')}"` : value
  return [
    unifiedQuestionImportHeaders.join(','),
    ...rows.map(({ sequence, text, root }) => [
      String(sequence), text, '简答题', '', '', '', '', '', '', '知识追踪', '', '浏览器验收解析。', '0.500', root, 'KC2', 'KC2>KC3',
    ].map(escape).join(',')),
  ].join('\n')
}

test('independent frontend mounts on the business URL and survives refresh', async ({ page }) => {
  await login(page, 'http://127.0.0.1:8000')
  await page.goto('http://127.0.0.1:8000/teacher/courses/1/experiments/')
  await expect(page.locator('#kt-app')).toBeVisible()
  await expect(page.getByRole('heading', { name: '实验', exact: true })).toBeVisible()
  await page.reload()
  await expect(page.getByRole('heading', { name: '实验', exact: true })).toBeVisible()

  await page.getByRole('link', { name: '题库' }).click()
  await expect(page).toHaveURL(/\/teacher\/courses\/1\/banks\/$/)
  await expect(page.locator('#kt-app')).toBeVisible()
  await expect(page.getByRole('heading', { name: '题库', exact: true })).toBeVisible()
})

test('teacher creates an experiment and saves a resumable publish draft', async ({ browserName, page }) => {
  await login(page, 'http://127.0.0.1:8000')
  await page.goto('http://127.0.0.1:8000/teacher/courses/1/experiments/')
  await page.getByRole('link', { name: '创建实验' }).click()
  await expect(page).toHaveURL(/\/teacher\/courses\/1\/experiments\/create\/$/)

  await page.getByLabel('实验名称').fill(`渐进式发布验证-${browserName}`)
  await page.getByLabel('题库').selectOption({ label: '演示题库' })
  await selectFirstKnowledgeComponent(page)
  await page.getByRole('button', { name: '保存草稿', exact: true }).click()
  await expect(page).toHaveURL(/\/teacher\/courses\/1\/experiments\/\d+\/$/)
  await expect(page.getByRole('heading', { name: `渐进式发布验证-${browserName}` })).toBeVisible()

  await page.getByRole('button', { name: '发布到班级' }).click()
  await expect(page).toHaveURL(/\/publish-plan\/\d+\/step\/1\/$/)
  await page.getByRole('checkbox', { name: '选择 认知追踪实验班', exact: true }).check()
  await page.getByRole('button', { name: '保存草稿' }).click()
  await expect(page.getByText('草稿已保存。你可以退出后继续。')).toBeVisible()
  await expect(page.getByText('设置完整，可以执行发布检查。')).toBeVisible()
})

test('teacher previews and atomically publishes a class task', async ({ browserName, page }) => {
  test.skip(browserName !== 'chromium', 'One browser covers the destructive publish journey.')
  await login(page, 'http://127.0.0.1:8000')
  await page.goto('http://127.0.0.1:8000/teacher/courses/1/experiments/create/')
  await page.getByLabel('实验名称').fill('正式发布浏览器验收')
  await page.getByLabel('题库').selectOption({ label: '演示题库' })
  await selectFirstKnowledgeComponent(page)
  await page.locator('input[name="cross_experiment_item_policy"][value="unrestricted"]').check()
  await page.getByRole('button', { name: '保存草稿', exact: true }).click()
  await page.getByRole('button', { name: '发布到班级' }).click()

  await page.locator('[id^="publish-class-"]').first().check()
  const previewed = page.waitForResponse(response => (
    response.request().method() === 'POST'
    && /\/api\/v1\/teacher\/publish-plans\/\d+\/preview\/$/.test(response.url())
  ))
  await page.getByRole('button', { name: '运行发布前检查' }).click()
  const previewResponse = await previewed
  expect(previewResponse.status(), await previewResponse.text()).toBe(200)

  await expect(page).toHaveURL(/\/publish-plan\/\d+\/step\/2\/$/, { timeout: 30_000 })
  await expect(page.getByRole('heading', { name: '发布前检查', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '进入发布确认' }).click()
  await page.getByRole('button', { name: '确认发布' }).click()
  await expect(page).toHaveURL(/\/teacher\/courses\/1\/experiments\/\d+\/$/)
  await expect(page.getByRole('heading', { name: '班级任务' })).toBeVisible()
  await expect(page.getByText('未开始', { exact: true })).toBeVisible()
})

test('login proceeds when the form is interactive before optional resources finish', async ({ page }) => {
  let releaseOptionalResource = () => {}
  let optionalResourceRequested = false
  const optionalResourceGate = new Promise<void>((resolve) => {
    releaseOptionalResource = resolve
  })
  await page.addInitScript(() => {
    window.addEventListener('DOMContentLoaded', () => {
      const script = document.createElement('script')
      script.async = true
      script.src = '/__e2e_slow_resource__'
      document.body.append(script)
    }, { once: true })
  })
  await page.route('http://127.0.0.1:8000/__e2e_slow_resource__', async (route) => {
    optionalResourceRequested = true
    await optionalResourceGate
    await route.fulfill({ body: '', contentType: 'application/javascript' })
  })

  try {
    await login(page, 'http://127.0.0.1:8000')
    expect(optionalResourceRequested).toBe(true)
  } finally {
    releaseOptionalResource()
  }
})

test('Session and CSRF contracts remain same-origin', async ({ page }) => {
  await login(page, 'http://127.0.0.1:8000')
  const session = await page.request.get('http://127.0.0.1:8000/api/v1/auth/session/')
  expect(session.status()).toBe(200)

  const missingCsrf = await page.request.post(
    'http://127.0.0.1:8000/api/v1/auth/session/',
    { data: {} },
  )
  expect(missingCsrf.status()).toBe(403)
  expect((await missingCsrf.json()).error.code).toBe('CSRF_FAILED')

  const csrf = await page.request.get('http://127.0.0.1:8000/api/v1/auth/csrf/')
  const csrfToken = (await csrf.json()).csrf_token
  const acceptedCsrf = await page.request.post(
    'http://127.0.0.1:8000/api/v1/auth/session/',
    { data: {}, headers: { 'X-CSRFToken': csrfToken } },
  )
  expect(acceptedCsrf.status()).toBe(405)
})

test('unknown global URLs remain Django 404s', async ({ page }) => {
  const response = await page.goto('http://127.0.0.1:8000/not-a-vue-route/')
  expect(response?.status()).toBe(404)
})

test('Vue shell reflows without horizontal overflow', async ({ browserName, page }, testInfo) => {
  test.skip(browserName !== 'chromium', 'One rendering engine is sufficient for viewport evidence.')
  await login(page, 'http://127.0.0.1:8000')
  await page.goto('http://127.0.0.1:8000/teacher/courses/1/experiments/')

  for (const viewport of [
    { name: 'desktop-1440x900', width: 1440, height: 900 },
    { name: 'mobile-390x844', width: 390, height: 844 },
    { name: 'zoom-200-percent-equivalent', width: 720, height: 450 },
  ]) {
    await page.setViewportSize(viewport)
    await expect(page.getByRole('heading', { name: '实验', exact: true })).toBeInViewport()
    const hasHorizontalOverflow = await page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
    )
    expect(hasHorizontalOverflow).toBe(false)
    await page.screenshot({ path: testInfo.outputPath(`${viewport.name}.png`), fullPage: true })
  }
})

test('approved shell prevents button wrapping and keeps reduced-motion focus visible', async ({ browserName, page }) => {
  test.skip(browserName !== 'chromium', 'One rendering engine is sufficient for visual-system gates.')
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await login(page, 'http://127.0.0.1:8000')
  await page.goto('http://127.0.0.1:8000/teacher/courses/1/experiments/')

  const wrappedButtons = await page.locator('button, .kt-button, .btn').evaluateAll((elements) =>
    elements
      .filter(element => element.scrollWidth > element.clientWidth + 1)
      .map(element => ({ text: element.textContent?.trim(), className: element.className })),
  )
  expect(wrappedButtons).toEqual([])

  const focusTarget = page.locator('.kt-skip-link')
  await focusTarget.focus()
  await expect(focusTarget).toBeFocused()
  await expect(focusTarget).toBeInViewport()
  const focusStyle = await focusTarget.evaluate((element) => {
    const style = getComputedStyle(element)
    return { outlineStyle: style.outlineStyle, outlineWidth: style.outlineWidth }
  })
  expect(focusStyle.outlineStyle).not.toBe('none')
  expect(Number.parseFloat(focusStyle.outlineWidth)).toBeGreaterThan(0)
})

test('teacher class center keeps analytics and management on the same frontend route', async ({ page }) => {
  await login(page, 'http://127.0.0.1:8000')
  await page.goto('http://127.0.0.1:8000/teacher/courses/1/classes/')
  await expect(page.locator('#kt-app')).toBeVisible()
  await expect(page.getByRole('heading', { name: '班级', exact: true })).toBeVisible()

  await page.locator('a[href="/teacher/courses/1/classes/1/"]').filter({ hasText: '进入班级中心' }).click()
  await expect(page).toHaveURL(/\/teacher\/courses\/1\/classes\/\d+\/$/)
  await expect(page.getByRole('button', { name: '概览', exact: true })).toBeVisible()
  await expect(page.getByText('总体正确率', { exact: true })).toBeVisible()

  await page.getByRole('button', { name: '学生表现', exact: true }).click()
  await expect(page.getByRole('heading', { name: '学生表现', exact: true })).toBeVisible()
  await expect(page.getByText(/S00[1-3]/).first()).toBeVisible()

  await page.getByRole('link', { name: '班级管理', exact: true }).click()
  await expect(page).toHaveURL(/\?section=manage$/)
  await expect(page.locator('#kt-app')).toBeVisible()
  await expect(page.getByText('班级设置', { exact: true })).toBeVisible()
})

test('teacher class center reflows at required viewports', async ({ browserName, page }, testInfo) => {
  test.skip(browserName !== 'chromium', 'One rendering engine is sufficient for viewport evidence.')
  await login(page, 'http://127.0.0.1:8000')
  await page.goto('http://127.0.0.1:8000/teacher/courses/1/classes/')
  await expect(page.getByRole('heading', { name: '课程班级', exact: true })).toBeVisible()

  for (const viewport of [
    { name: 'class-desktop-1440x900', width: 1440, height: 900 },
    { name: 'class-mobile-390x844', width: 390, height: 844 },
    { name: 'class-zoom-200-percent-equivalent', width: 720, height: 450 },
  ]) {
    await page.setViewportSize(viewport)
    await expect(page.getByRole('heading', { name: '班级', exact: true })).toBeInViewport()
    const overflow = await page.evaluate(() => ({
      clientWidth: document.documentElement.clientWidth,
      scrollWidth: document.documentElement.scrollWidth,
      offenders: Array.from(document.querySelectorAll<HTMLElement>('body *'))
        .filter(element => element.getBoundingClientRect().right > document.documentElement.clientWidth + 1)
        .slice(0, 8)
        .map(element => ({
          tag: element.tagName,
          className: element.className,
          right: Math.round(element.getBoundingClientRect().right),
          scrollWidth: element.scrollWidth,
        })),
    }))
    expect(overflow, viewport.name).toMatchObject({
      clientWidth: overflow.scrollWidth,
      offenders: [],
    })
    await page.screenshot({ path: testInfo.outputPath(`${viewport.name}.png`), fullPage: true })
  }

  await page.setViewportSize({ width: 1440, height: 900 })
  await page.getByRole('link', { name: '进入班级中心' }).first().click()
  await expect(page.getByText('总体正确率', { exact: true })).toBeVisible()
  for (const viewport of [
    { name: 'class-center-desktop-1440x900', width: 1440, height: 900 },
    { name: 'class-center-mobile-390x844', width: 390, height: 844 },
    { name: 'class-center-zoom-200-percent-equivalent', width: 720, height: 450 },
  ]) {
    await page.setViewportSize(viewport)
    await expect(page.getByText('总体正确率', { exact: true })).toBeVisible()
    const overflow = await page.evaluate(() => ({
      clientWidth: document.documentElement.clientWidth,
      scrollWidth: document.documentElement.scrollWidth,
      offenders: Array.from(document.querySelectorAll<HTMLElement>('body *'))
        .filter(element => element.getBoundingClientRect().right > document.documentElement.clientWidth + 1)
        .slice(0, 8)
        .map(element => ({
          tag: element.tagName,
          className: element.className,
          right: Math.round(element.getBoundingClientRect().right),
          scrollWidth: element.scrollWidth,
        })),
    }))
    expect(overflow, viewport.name).toMatchObject({
      clientWidth: overflow.scrollWidth,
      offenders: [],
    })
    await page.screenshot({ path: testInfo.outputPath(`${viewport.name}.png`), fullPage: true })
  }
})

test('question-bank list and detail mount on their business URLs', async ({ page }) => {
  await login(page, 'http://127.0.0.1:8000')
  await page.goto('http://127.0.0.1:8000/teacher/courses/1/banks/')
  await expect(page.locator('#kt-app')).toBeVisible()
  await expect(page.getByRole('heading', { name: '题库', exact: true })).toBeVisible()
  await expect(page.getByRole('tab', { name: /课程 KC 总库 \d+/ })).toBeVisible()
  await page.getByRole('link', { name: '演示题库', exact: true }).first().click()
  await expect(page).toHaveURL(/\/teacher\/courses\/1\/banks\/\d+\/$/)
  await expect(page.getByRole('heading', { name: '演示题库', exact: true })).toBeVisible()
  await expect(page.getByText('2 + 2 等于多少？').first()).toBeVisible()
  await page.reload()
  await expect(page.getByRole('heading', { name: '演示题库', exact: true })).toBeVisible()
})

test('question-bank Vue imports CSV, Excel and Word templates, then retries corrected rows', async ({ browserName, page }, testInfo) => {
  test.skip(browserName !== 'chromium', 'One browser covers the uploaded-file and download workflow.')
  test.setTimeout(120_000)
  const runId = `${browserName}-${Date.now()}`
  const bankName = `浏览器统一导入验收-${runId}`
  await login(page, 'http://127.0.0.1:8000')
  await page.goto('http://127.0.0.1:8000/teacher/courses/1/banks/')

  const createForm = page.locator('form[data-create-bank]')
  await createForm.getByLabel('名称').fill(bankName)
  await createForm.getByLabel('说明').fill('验证统一导入的文件预览和确认写入')
  await createForm.getByLabel('可见性').selectOption('private')
  await createForm.getByRole('button', { name: '创建题库' }).click()

  await page.getByRole('link', { name: bankName, exact: true }).click()
  await expect(page).toHaveURL(/\/teacher\/courses\/1\/banks\/\d+\/$/)
  await expect(page.getByRole('heading', { name: bankName, exact: true })).toBeVisible()
  await page.locator('[data-question-import]').click()
  await expect(page).toHaveURL(/\/import\/$/)
  await expect(page.locator('#kt-app')).toBeVisible()
  await expect(page.getByRole('heading', { name: '导入题库', exact: true })).toBeVisible()

  const fileInput = page.locator('input[type="file"]')
  const preview = page.locator('[data-import-preview]')
  const summary = page.getByLabel('预览状态汇总')
  const uploadDownloadedTemplate = async (format: 'csv' | 'xlsx' | 'docx') => {
    const templateLink = page.locator(`[data-question-template="${format}"]`)
    if (!await templateLink.isVisible()) {
      await page.locator('summary[aria-label="文件格式与模板"]').click()
    }
    const downloadEvent = page.waitForEvent('download')
    await templateLink.click()
    const download = await downloadEvent
    const filename = download.suggestedFilename()
    expect(filename).toMatch(new RegExp(`\\.${format}$`))
    const downloadPath = testInfo.outputPath(`template-${format}-${filename}`)
    await download.saveAs(downloadPath)

    const response = page.waitForResponse((candidate) => (
      candidate.request().method() === 'POST'
      && candidate.url().includes('/imports/questions/preview/')
    ))
    await fileInput.setInputFiles(downloadPath!)
    expect((await response).status()).toBe(200)
    await expect(preview).toBeVisible()
    await expect(summary).toContainText('导入后可用 6')
    return downloadPath!
  }

  const csvTemplate = await uploadDownloadedTemplate('csv')
  await uploadDownloadedTemplate('xlsx')
  await page.locator('summary[aria-label="文件格式与模板"]').click()
  await page.getByLabel('Word .docx').check()
  await expect(fileInput).toHaveAttribute('accept', '.docx')
  await uploadDownloadedTemplate('docx')
  await page.locator('summary[aria-label="文件格式与模板"]').click()
  await page.getByLabel('Excel / CSV').check()
  await expect(fileInput).toHaveAttribute('accept', '.csv,.xlsx')

  const captureImportViewport = async (name: string, width: number, height: number) => {
    await page.setViewportSize({ width, height })
    await expect(page.getByRole('heading', { name: '导入题库', exact: true })).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth), name).toBe(false)
    await page.evaluate(() => window.scrollTo(0, 0))
    await page.screenshot({ path: testInfo.outputPath(`${name}.png`), fullPage: true })
  }
  await captureImportViewport('question-import-preview-1440x900', 1440, 900)
  await page.setViewportSize({ width: 390, height: 844 })
  let response = page.waitForResponse((candidate) => (
    candidate.request().method() === 'POST'
    && candidate.url().includes('/imports/questions/preview/')
  ))
  await fileInput.setInputFiles(csvTemplate)
  expect((await response).status()).toBe(200)
  await expect(preview.locator('[data-import-mobile-row]').first()).toBeVisible()
  await captureImportViewport('question-import-preview-390x844', 390, 844)
  await captureImportViewport('question-import-preview-zoom-200', 720, 450)
  await page.setViewportSize({ width: 390, height: 844 })
  await expect(page.locator('[data-confirm-import]')).toBeEnabled()
  response = page.waitForResponse((candidate) => (
    candidate.request().method() === 'POST'
    && candidate.url().includes('/imports/questions/confirm/')
  ))
  await page.locator('[data-confirm-import]').click()
  expect((await response).status()).toBe(201)
  const result = page.getByRole('status').filter({ hasText: '本次导入已完成。' })
  await expect(result).toContainText('新增 6 道，其中可用 6 道、草稿 0 道；跳过 0 道，未完成 0 道。')
  await page.setViewportSize({ width: 1440, height: 900 })
  await expect(page.locator('[data-confirm-import]')).toBeDisabled()

  const createdText = `E2E-${runId}-首次导入题目`
  const repairedText = `E2E-${runId}-修正后题目`
  response = page.waitForResponse((candidate) => (
    candidate.request().method() === 'POST'
    && candidate.url().includes('/imports/questions/preview/')
  ))
  await fileInput.setInputFiles({
    name: `partial-${runId}.csv`, mimeType: 'text/csv',
    buffer: Buffer.from(unifiedQuestionImportCsv([
      { sequence: 1, text: createdText, root: 'KC1' },
      { sequence: 2, text: repairedText, root: 'UNKNOWN_KC' },
      { sequence: 3, text: createdText, root: 'KC1' },
    ]), 'utf8'),
  })
  expect((await response).status()).toBe(200)
  await expect(result).toHaveCount(0)
  await expect(summary).toContainText('导入后可用 1')
  await expect(summary).toContainText('保存为草稿 1')
  await expect(summary).toContainText('重复 1')
  await expect(summary).toContainText('需修正 0')
  response = page.waitForResponse((candidate) => (
    candidate.request().method() === 'POST'
    && candidate.url().includes('/imports/questions/confirm/')
  ))
  await page.locator('[data-confirm-import]').click()
  expect((await response).status()).toBe(201)
  await expect(result).toContainText('新增 2 道，其中可用 1 道、草稿 1 道；跳过 1 道，未完成 0 道。')
  await expect(preview.locator('.kt-question-import__table .kt-question-import__errors').filter({ hasText: 'UNKNOWN_KC' })).toBeVisible()

  response = page.waitForResponse((candidate) => (
    candidate.request().method() === 'POST'
    && candidate.url().includes('/imports/questions/preview/')
  ))
  await fileInput.setInputFiles({
    name: `repaired-complete-${runId}.csv`, mimeType: 'text/csv',
    buffer: Buffer.from(unifiedQuestionImportCsv([
      { sequence: 1, text: createdText, root: 'KC1' },
      { sequence: 2, text: repairedText, root: 'KC1' },
    ]), 'utf8'),
  })
  expect((await response).status()).toBe(200)
  await expect(result).toHaveCount(0)
  await expect(summary).toContainText('导入后可用 1')
  await expect(summary).toContainText('保存为草稿 0')
  await expect(summary).toContainText('重复 1')
  await expect(summary).toContainText('需修正 0')
  await expect(preview).not.toContainText('UNKNOWN_KC')
  response = page.waitForResponse((candidate) => (
    candidate.request().method() === 'POST'
    && candidate.url().includes('/imports/questions/confirm/')
  ))
  await page.locator('[data-confirm-import]').click()
  expect((await response).status()).toBe(201)
  await expect(result).toContainText('新增 0 道，修复 1 道，其中可用 1 道、草稿 0 道；跳过 1 道，未完成 0 道。')
  await page.locator('[data-return-detail]').click()
  await expect(page.getByRole('heading', { name: bankName, exact: true })).toBeVisible()
  await expect(page.locator('.kt-bank-question-section .kt-desktop-only').getByText(repairedText, { exact: true })).toHaveCount(1)

  const retiredKcImport = new URL(page.url())
  retiredKcImport.pathname = `${retiredKcImport.pathname}kc-import/`
  await page.goto(retiredKcImport.toString())
  await expect(page).toHaveURL(/\/teacher\/courses\/1\/banks\/\d+\/import\/$/)
  await expect(page.getByRole('heading', { name: '导入题库', exact: true })).toBeVisible()
})

test('question-bank pages reflow without horizontal page overflow', async ({ browserName, page }, testInfo) => {
  test.skip(browserName !== 'chromium', 'One rendering engine provides the required viewport evidence.')
  await login(page, 'http://127.0.0.1:8000')

  for (const viewport of [
    { name: 'bank-list-desktop-1440x900', width: 1440, height: 900 },
    { name: 'bank-list-mobile-390x844', width: 390, height: 844 },
    { name: 'bank-list-zoom-200-percent-equivalent', width: 720, height: 450 },
  ]) {
    await page.setViewportSize(viewport)
    await page.goto('http://127.0.0.1:8000/teacher/courses/1/banks/')
    await expect(page.getByRole('heading', { name: '题库', exact: true })).toBeInViewport()
    await expect(page.getByText('正在加载题库…')).toHaveCount(0)
    await expect(page.getByRole('link', { name: '演示题库', exact: true }).first()).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)).toBe(false)
    await page.screenshot({ path: testInfo.outputPath(`${viewport.name}.png`), fullPage: true })
  }

  for (const viewport of [
    { name: 'bank-detail-desktop-1440x900', width: 1440, height: 900 },
    { name: 'bank-detail-mobile-390x844', width: 390, height: 844 },
    { name: 'bank-detail-zoom-200-percent-equivalent', width: 720, height: 450 },
  ]) {
    await page.setViewportSize(viewport)
    await page.goto('http://127.0.0.1:8000/teacher/courses/1/banks/1/')
    await expect(page.getByRole('heading', { name: '演示题库', exact: true })).toBeInViewport()
    expect(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)).toBe(false)
    await page.screenshot({ path: testInfo.outputPath(`${viewport.name}.png`), fullPage: true })
  }
})

test('data-center mounts every read route on its business URL', async ({ page }) => {
  test.setTimeout(120_000)
  await login(page, 'http://127.0.0.1:8000')
  const routes = [
    ['', '数据中心'],
    ['export/', '导出研究数据'],
    ['exports/', '导出研究数据'],
    ['quality/', '数据质量'],
    ['exports/history/', '导出记录'],
    ['advanced/', '研究数据预览'],
  ] as const

  for (const [suffix, heading] of routes) {
    await page.goto(`http://127.0.0.1:8000/teacher/courses/1/data/${suffix}`)
    await expect(page.locator('#kt-app')).toBeVisible()
    await expect(page.getByRole('heading', { name: heading, exact: true })).toBeVisible()
    await expect(page.getByRole('region', { name: '当前数据范围' })).toBeVisible()
  }

  await page.reload()
  await expect(page.getByRole('heading', { name: '研究数据预览', exact: true })).toBeVisible()
})

test('data-center saves its authoritative scope through the JSON API', async ({ browserName, page }) => {
  test.skip(browserName !== 'chromium', 'One browser covers the scope mutation boundary.')
  await login(page, 'http://127.0.0.1:8000')
  await page.goto('http://127.0.0.1:8000/teacher/courses/1/data/')

  const scope = page.getByRole('region', { name: '当前数据范围' })
  const experiment = scope.locator('label').filter({ hasText: '演示交互数据采集' })
  await experiment.click()
  await expect(experiment.getByRole('checkbox')).toBeChecked()
  await scope.locator('select[name="class_id"]').selectOption({ label: '2026 认知追踪实验班' })
  const saved = page.waitForResponse((response) => (
    response.request().method() === 'POST'
    && response.url().endsWith('/api/v1/teacher/courses/1/data/scope/')
  ))
  await scope.getByRole('button', { name: '应用' }).click()
  expect((await saved).status()).toBe(200)
  await expect(scope).toContainText('班级：2026 认知追踪实验班')
  await expect(scope).toContainText('已选择实验：演示交互数据采集（1 个实验）')
})

test('data-center overview and advanced pages reflow at required viewports', async ({ browserName, page }, testInfo) => {
  test.skip(browserName !== 'chromium', 'One rendering engine provides the required viewport evidence.')
  await login(page, 'http://127.0.0.1:8000')

  for (const route of [
    { suffix: '', heading: '数据中心', name: 'data-overview' },
    { suffix: 'advanced/', heading: '研究数据预览', name: 'data-advanced' },
  ]) {
    for (const viewport of [
      { name: 'desktop-1440x900', width: 1440, height: 900 },
      { name: 'mobile-390x844', width: 390, height: 844 },
      { name: 'zoom-200-percent-equivalent', width: 720, height: 450 },
    ]) {
      await page.setViewportSize(viewport)
      await page.goto(`http://127.0.0.1:8000/teacher/courses/1/data/${route.suffix}`)
      await expect(page.getByRole('status')).toHaveCount(0)
      const heading = page.getByRole('heading', { name: route.heading, exact: true })
      await expect(heading).toBeVisible()
      await heading.scrollIntoViewIfNeeded()
      await expect(heading).toBeInViewport()
      expect(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)).toBe(false)
      await page.screenshot({ path: testInfo.outputPath(`${route.name}-${viewport.name}.png`), fullPage: true })
    }
  }
})
