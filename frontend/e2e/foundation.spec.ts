import { expect, test, type Page } from '@playwright/test'

async function login(page: Page, origin: string) {
  await page.goto(`${origin}/login/`)
  await page.getByLabel('账号或学号').fill('teacher')
  await page.getByLabel('密码').fill('teacher123')
  await page.getByRole('button', { name: '登录' }).click()
  await expect(page).toHaveURL(new RegExp(`${origin}/teacher/courses/`))
}

async function selectFirstKnowledgeComponent(page: Page) {
  await page.getByText('选择知识点', { exact: true }).click()
  await page
    .locator('.kt-kc-config-panel', { hasText: '知识点范围' })
    .locator('.kt-kc-tree-selector input[type="checkbox"]')
    .first()
    .check()
}

test('Flag OFF keeps the legacy template', async ({ page }) => {
  await login(page, 'http://127.0.0.1:8010')
  await page.goto('http://127.0.0.1:8010/teacher/courses/1/experiments/')
  await expect(page.locator('.table-shell')).toBeVisible()
  await expect(page.locator('#kt-app')).toHaveCount(0)
})

test('Flag ON mounts Vue on the same URL and survives refresh', async ({ page }) => {
  await login(page, 'http://127.0.0.1:8011')
  await page.goto('http://127.0.0.1:8011/teacher/courses/1/experiments/')
  await expect(page.locator('#kt-app')).toBeVisible()
  await expect(page.getByRole('heading', { name: '实验', exact: true })).toBeVisible()
  await page.reload()
  await expect(page.getByRole('heading', { name: '实验', exact: true })).toBeVisible()

  await page.getByRole('link', { name: '题库' }).click()
  await expect(page).toHaveURL(/\/teacher\/courses\/1\/banks\/$/)
  await expect(page.locator('#kt-app')).toHaveCount(0)
})

test('User allowlist enables the internal teacher', async ({ page }) => {
  await login(page, 'http://127.0.0.1:8012')
  await page.goto('http://127.0.0.1:8012/teacher/courses/1/experiments/')
  await expect(page.getByRole('heading', { name: '实验', exact: true })).toBeVisible()
})

test('teacher creates an experiment and saves a resumable publish draft', async ({ browserName, page }) => {
  await login(page, 'http://127.0.0.1:8011')
  await page.goto('http://127.0.0.1:8011/teacher/courses/1/experiments/')
  await page.getByRole('link', { name: '创建实验' }).click()
  await expect(page).toHaveURL(/\/teacher\/courses\/1\/experiments\/create\/$/)

  await page.getByLabel('实验名称').fill(`渐进式发布验证-${browserName}`)
  await page.getByLabel('题库').selectOption({ label: '演示题库' })
  await selectFirstKnowledgeComponent(page)
  await page.getByRole('button', { name: '保存实验' }).click()
  await expect(page).toHaveURL(/\/teacher\/courses\/1\/experiments\/\d+\/$/)
  await expect(page.getByRole('heading', { name: `渐进式发布验证-${browserName}` })).toBeVisible()

  await page.getByRole('button', { name: '发布到班级' }).click()
  await expect(page).toHaveURL(/\/publish-plan\/\d+\/step\/1\/$/)
  await page.locator('[id^="publish-class-"]').first().check()
  await page.getByRole('button', { name: '保存草稿' }).click()
  await expect(page.getByText('草稿已保存。你可以退出后继续。')).toBeVisible()
  await expect(page.getByText('设置完整，可以执行发布检查。')).toBeVisible()
})

test('teacher previews and atomically publishes a class task', async ({ browserName, page }) => {
  test.skip(browserName !== 'chromium', 'One browser covers the destructive publish journey.')
  await login(page, 'http://127.0.0.1:8011')
  await page.goto('http://127.0.0.1:8011/teacher/courses/1/experiments/create/')
  await page.getByLabel('实验名称').fill('正式发布浏览器验收')
  await page.getByLabel('题库').selectOption({ label: '演示题库' })
  await selectFirstKnowledgeComponent(page)
  await page.getByRole('button', { name: '保存实验' }).click()
  await page.getByRole('button', { name: '发布到班级' }).click()

  await page.locator('[id^="publish-class-"]').first().check()
  await page.getByRole('button', { name: '运行发布前检查' }).click()

  await expect(page).toHaveURL(/\/publish-plan\/\d+\/step\/2\/$/)
  await expect(page.getByRole('heading', { name: '发布前检查', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '进入发布确认' }).click()
  await page.getByRole('button', { name: '确认发布' }).click()
  await expect(page).toHaveURL(/\/teacher\/courses\/1\/experiments\/\d+\/$/)
  await expect(page.getByRole('heading', { name: '班级任务' })).toBeVisible()
  await expect(page.getByText('未开始', { exact: true })).toBeVisible()
})

test('Session and CSRF contracts remain same-origin', async ({ page }) => {
  await login(page, 'http://127.0.0.1:8011')
  const session = await page.request.get('http://127.0.0.1:8011/api/v1/auth/session/')
  expect(session.status()).toBe(200)

  const missingCsrf = await page.request.post(
    'http://127.0.0.1:8011/api/v1/auth/session/',
    { data: {} },
  )
  expect(missingCsrf.status()).toBe(403)
  expect((await missingCsrf.json()).error.code).toBe('CSRF_FAILED')

  const csrf = await page.request.get('http://127.0.0.1:8011/api/v1/auth/csrf/')
  const csrfToken = (await csrf.json()).csrf_token
  const acceptedCsrf = await page.request.post(
    'http://127.0.0.1:8011/api/v1/auth/session/',
    { data: {}, headers: { 'X-CSRFToken': csrfToken } },
  )
  expect(acceptedCsrf.status()).toBe(405)
})

test('unknown global URLs remain Django 404s', async ({ page }) => {
  const response = await page.goto('http://127.0.0.1:8011/not-a-vue-route/')
  expect(response?.status()).toBe(404)
})

test('Vue shell reflows without horizontal overflow', async ({ browserName, page }, testInfo) => {
  test.skip(browserName !== 'chromium', 'One rendering engine is sufficient for viewport evidence.')
  await login(page, 'http://127.0.0.1:8011')
  await page.goto('http://127.0.0.1:8011/teacher/courses/1/experiments/')

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
  await login(page, 'http://127.0.0.1:8011')
  await page.goto('http://127.0.0.1:8011/teacher/courses/1/experiments/')

  const wrappedButtons = await page.locator('button, .kt-button, .btn').evaluateAll((elements) =>
    elements
      .filter(element => element.scrollWidth > element.clientWidth + 1)
      .map(element => ({ text: element.textContent?.trim(), className: element.className })),
  )
  expect(wrappedButtons).toEqual([])

  await page.keyboard.press('Tab')
  const focusStyle = await page.locator(':focus-visible').first().evaluate((element) => {
    const style = getComputedStyle(element)
    return { outlineStyle: style.outlineStyle, outlineWidth: style.outlineWidth }
  })
  expect(focusStyle.outlineStyle).not.toBe('none')
  expect(Number.parseFloat(focusStyle.outlineWidth)).toBeGreaterThan(0)
})

test('teacher class center keeps analytics in Vue and management in Django', async ({ page }) => {
  await login(page, 'http://127.0.0.1:8016')
  await page.goto('http://127.0.0.1:8016/teacher/courses/1/classes/')
  await expect(page.locator('#kt-app')).toBeVisible()
  await expect(page.getByRole('heading', { name: '班级', exact: true })).toBeVisible()

  await page.getByRole('link', { name: '进入班级中心' }).first().click()
  await expect(page).toHaveURL(/\/teacher\/courses\/1\/classes\/\d+\/$/)
  await expect(page.getByRole('button', { name: '概览', exact: true })).toBeVisible()
  await expect(page.getByText('总体正确率', { exact: true })).toBeVisible()

  await page.getByRole('button', { name: '学生表现', exact: true }).click()
  await expect(page.getByRole('heading', { name: '学生表现', exact: true })).toBeVisible()
  await expect(page.getByText(/S00[1-3]/).first()).toBeVisible()

  await page.getByRole('link', { name: '班级管理', exact: true }).click()
  await expect(page).toHaveURL(/\?section=manage$/)
  await expect(page.locator('#kt-app')).toHaveCount(0)
  await expect(page.getByText('班级设置', { exact: true })).toBeVisible()
})

test('teacher class center reflows at required viewports', async ({ browserName, page }, testInfo) => {
  test.skip(browserName !== 'chromium', 'One rendering engine is sufficient for viewport evidence.')
  await login(page, 'http://127.0.0.1:8016')
  await page.goto('http://127.0.0.1:8016/teacher/courses/1/classes/')
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

test('question-bank flag OFF keeps the legacy list and detail templates', async ({ page }) => {
  await login(page, 'http://127.0.0.1:8010')
  await page.goto('http://127.0.0.1:8010/teacher/courses/1/banks/')
  await expect(page.locator('.table-shell').first()).toBeVisible()
  await expect(page.locator('#kt-app')).toHaveCount(0)
  await page.getByLabel('课程题库').getByRole('link', { name: '演示题库', exact: true }).click()
  await expect(page.locator('.table-shell').first()).toBeVisible()
  await expect(page.locator('#kt-app')).toHaveCount(0)
})

test('question-bank flag ON mounts list and detail on the existing URLs', async ({ page }) => {
  await login(page, 'http://127.0.0.1:8017')
  await page.goto('http://127.0.0.1:8017/teacher/courses/1/banks/')
  await expect(page.locator('#kt-app')).toBeVisible()
  await expect(page.getByRole('heading', { name: '题库', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: /课程 KC 总库 \d+/ })).toBeVisible()
  await page.getByRole('link', { name: '演示题库', exact: true }).first().click()
  await expect(page).toHaveURL(/\/teacher\/courses\/1\/banks\/\d+\/$/)
  await expect(page.getByRole('heading', { name: '演示题库', exact: true })).toBeVisible()
  await expect(page.getByText('2 + 2 等于多少？').first()).toBeVisible()
  await page.reload()
  await expect(page.getByRole('heading', { name: '演示题库', exact: true })).toBeVisible()
})

test('question-bank Vue keeps create and import operations in Django document flows', async ({ browserName, page }) => {
  test.skip(browserName !== 'chromium', 'One browser covers the document POST and import handoff.')
  await login(page, 'http://127.0.0.1:8017')
  await page.goto('http://127.0.0.1:8017/teacher/courses/1/banks/')

  const createForm = page.locator('form[data-create-bank]')
  await createForm.getByLabel('名称').fill('浏览器验收题库')
  await createForm.getByLabel('说明').fill('验证同 URL 创建和旧写入事务')
  await createForm.getByLabel('可见性').selectOption('private')
  await createForm.getByRole('button', { name: '创建题库' }).click()

  await page.getByRole('link', { name: '浏览器验收题库', exact: true }).click()
  await expect(page).toHaveURL(/\/teacher\/courses\/1\/banks\/\d+\/$/)
  await expect(page.getByRole('heading', { name: '浏览器验收题库', exact: true })).toBeVisible()
  await page.getByRole('link', { name: '导入题目文件' }).click()
  await expect(page).toHaveURL(/\/import\/$/)
  await expect(page.locator('#kt-app')).toHaveCount(0)
  await expect(page.getByRole('heading', { name: '导入题目文件' })).toBeVisible()
})

test('question-bank pages reflow without horizontal page overflow', async ({ browserName, page }, testInfo) => {
  test.skip(browserName !== 'chromium', 'One rendering engine provides the required viewport evidence.')
  await login(page, 'http://127.0.0.1:8017')

  for (const viewport of [
    { name: 'bank-list-desktop-1440x900', width: 1440, height: 900 },
    { name: 'bank-list-mobile-390x844', width: 390, height: 844 },
    { name: 'bank-list-zoom-200-percent-equivalent', width: 720, height: 450 },
  ]) {
    await page.setViewportSize(viewport)
    await page.goto('http://127.0.0.1:8017/teacher/courses/1/banks/')
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
    await page.goto('http://127.0.0.1:8017/teacher/courses/1/banks/1/')
    await expect(page.getByRole('heading', { name: '演示题库', exact: true })).toBeInViewport()
    expect(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)).toBe(false)
    await page.screenshot({ path: testInfo.outputPath(`${viewport.name}.png`), fullPage: true })
  }
})

test('data-center flag OFF keeps the legacy data templates', async ({ page }) => {
  await login(page, 'http://127.0.0.1:8010')
  await page.goto('http://127.0.0.1:8010/teacher/courses/1/data/')
  await expect(page.locator('#kt-app')).toHaveCount(0)
  await expect(page.getByRole('heading', { name: '数据中心', exact: true })).toBeVisible()
  await page.goto('http://127.0.0.1:8010/teacher/courses/1/data/advanced/')
  await expect(page.locator('#kt-app')).toHaveCount(0)
  await expect(page.getByRole('heading', { name: '高级数据工具', exact: true })).toBeVisible()
})

test('data-center flag ON mounts every migrated read route on its existing URL', async ({ page }) => {
  test.setTimeout(120_000)
  await login(page, 'http://127.0.0.1:8018')
  const routes = [
    ['', '数据中心'],
    ['export/', '导出数据包'],
    ['exports/', '导出数据包'],
    ['quality/', '数据质量'],
    ['exports/history/', '导出记录'],
    ['advanced/', '高级数据工具'],
  ] as const

  for (const [suffix, heading] of routes) {
    await page.goto(`http://127.0.0.1:8018/teacher/courses/1/data/${suffix}`)
    await expect(page.locator('#kt-app')).toBeVisible()
    await expect(page.getByRole('heading', { name: heading, exact: true })).toBeVisible()
    await expect(page.getByRole('region', { name: '当前数据范围' })).toBeVisible()
  }

  await page.reload()
  await expect(page.getByRole('heading', { name: '高级数据工具', exact: true })).toBeVisible()
})

test('data-center keeps scope and export writes in Django document forms', async ({ browserName, page }) => {
  test.skip(browserName !== 'chromium', 'One browser covers the document-form boundaries.')
  await login(page, 'http://127.0.0.1:8018')
  await page.goto('http://127.0.0.1:8018/teacher/courses/1/data/')

  const scopeForm = page.getByRole('region', { name: '当前数据范围' }).locator('form')
  await scopeForm.locator('select[name="experiment_id"]').selectOption({ label: '演示交互数据采集' })
  await scopeForm.locator('select[name="class_id"]').selectOption({ label: '2026 认知追踪实验班' })
  await scopeForm.getByRole('button', { name: '应用' }).click()
  await expect(page).toHaveURL(/\/teacher\/courses\/1\/data\/$/)
  await expect(page.getByText('已选择教学范围')).toBeVisible()

  await page.getByRole('link', { name: '导出', exact: true }).click()
  await expect(page.getByRole('heading', { name: '导出数据包', exact: true })).toBeVisible()
  const versionForm = page.getByRole('heading', { name: '选择导出版本' }).locator('..').locator('form')
  await versionForm.getByLabel('实验版本').selectOption({ index: 1 })
  await versionForm.getByRole('button', { name: '使用此版本' }).click()
  await expect(page.getByText(/本次导出版本：Version/)).toBeVisible()
  await page.getByRole('link', { name: '选择用途' }).first().click()

  const confirmation = page.getByRole('heading', { name: /确认导出/ }).locator('..').locator('form')
  await expect(confirmation).toHaveAttribute('method', 'post')
  await expect(confirmation.locator('input[name="csrfmiddlewaretoken"]')).toHaveValue(/.+/)
  await expect(confirmation.locator('input[name="confirm_identified"]')).toHaveValue('yes')
})

test('data-center overview and advanced pages reflow at required viewports', async ({ browserName, page }, testInfo) => {
  test.skip(browserName !== 'chromium', 'One rendering engine provides the required viewport evidence.')
  await login(page, 'http://127.0.0.1:8018')

  for (const route of [
    { suffix: '', heading: '数据中心', name: 'data-overview' },
    { suffix: 'advanced/', heading: '高级数据工具', name: 'data-advanced' },
  ]) {
    for (const viewport of [
      { name: 'desktop-1440x900', width: 1440, height: 900 },
      { name: 'mobile-390x844', width: 390, height: 844 },
      { name: 'zoom-200-percent-equivalent', width: 720, height: 450 },
    ]) {
      await page.setViewportSize(viewport)
      await page.goto(`http://127.0.0.1:8018/teacher/courses/1/data/${route.suffix}`)
      const heading = page.getByRole('heading', { name: route.heading, exact: true })
      await expect(heading).toBeVisible()
      await page.evaluate(() => window.scrollTo(0, 0))
      await expect(heading).toBeInViewport()
      await expect(page.getByRole('status')).toHaveCount(0)
      expect(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)).toBe(false)
      await page.screenshot({ path: testInfo.outputPath(`${route.name}-${viewport.name}.png`), fullPage: true })
    }
  }
})
