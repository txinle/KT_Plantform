# KT Data Lab Platform UI Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** 将 Django、Vue 和学生作答运行时更新为已批准的 KT Data Lab 统一视觉系统，同时保持全部业务逻辑、路由、表单和 API 契约不变。

**Architecture:** 新增由 Django 与 Vue shell 共同加载的 static/css/kt-theme.css，作为平台视觉令牌的单一来源。Django 继续通过 static/css/app.css 和现有模板呈现，Vue 继续通过 frontend/src/design-system 与 feature CSS 呈现；两者只改变展示结构与样式，不新增客户端业务状态或权限判断。

**Tech Stack:** Python 3.13、Django 5.2.16、Vue 3.5、TypeScript 5.9、Vite 8、Vitest、Playwright、Bootstrap 5.3、原生 CSS。

**Execution note:** 当前仓库规则禁止未授权提交，因此使用 git diff 检查点替代提交。实施在当前 worktree 原地完成，不创建新 worktree 或子代理。

---

## File map

Create:

- static/css/kt-theme.css: Django 与 Vue 共同加载的平台视觉令牌。
- E:/ATOP/outputs/ui-refresh-screenshots/: 更新后的桌面截图。
- E:/ATOP/outputs/UI更新截图索引.md: 更新后的功能树与截图索引。

Shared shell:

- templates/base.html
- templates/spa/vue_shell.html
- static/css/app.css
- tests/test_ui_contracts.py
- apps/api/test_shell.py

Vue design system:

- frontend/src/design-system/tokens.css
- frontend/src/design-system/foundations.css
- frontend/src/design-system/components/AppHeader.vue
- frontend/src/design-system/components/PageHeader.vue
- frontend/src/design-system/components.test.ts

Teacher Vue:

- frontend/src/features/teacher-experiments/TeacherExperimentsLayout.vue
- frontend/src/features/teacher-experiments/teacher-experiments.css
- frontend/src/features/teacher-question-banks/TeacherQuestionBanksLayout.vue
- frontend/src/features/teacher-question-banks/teacher-question-banks.css
- frontend/src/features/teacher-classes/TeacherClassesLayout.vue
- frontend/src/features/teacher-classes/teacher-classes.css
- frontend/src/features/teacher-data-center/TeacherDataCenterLayout.vue
- frontend/src/features/teacher-data-center/teacher-data-center.css

Student Vue and assessment:

- frontend/src/features/student-courses/StudentCoursesLayout.vue
- frontend/src/features/student-courses/student-courses.css
- frontend/src/assessment-runtime/student-assessment.css
- frontend/src/features/student-learning/StudentLearning.vue
- frontend/src/features/student-results/StudentResult.vue

High-value Django pages:

- templates/registration/login.html
- templates/teacher/course_experiment_detail.html
- templates/teacher/course_experiment_form.html
- templates/teacher/course_experiment_publish_wizard.html
- templates/teacher/course_experiment_results.html
- templates/teacher/data_center/_workbench_nav.html
- templates/student/question.html
- templates/student/complete.html

Verification:

- frontend/e2e/foundation.spec.ts
- frontend/e2e/student.spec.ts

---

### Task 1: Establish the shared visual-token contract

**Files:**

- Create: static/css/kt-theme.css
- Modify: templates/base.html
- Modify: templates/spa/vue_shell.html
- Modify: frontend/src/design-system/tokens.css
- Modify: tests/test_ui_contracts.py
- Modify: apps/api/test_shell.py

- [ ] **Step 1: Write failing shared-theme tests**

Add to tests/test_ui_contracts.py:

    def test_shared_theme_is_loaded_before_django_surface_styles(self):
        source = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
        self.assertIn("css/kt-theme.css", source)
        self.assertLess(source.index("css/kt-theme.css"), source.index("css/app.css"))

    def test_shared_theme_defines_approved_tokens(self):
        source = (ROOT / "static" / "css" / "kt-theme.css").read_text(encoding="utf-8")
        for token in (
            "--kt-brand-900: #0c3266",
            "--kt-action-600: #145bea",
            "--kt-page: #f6f8fb",
            "--kt-radius-surface: 0.75rem",
            "--kt-font-sans:",
        ):
            self.assertIn(token, source)

Add to VueShellRolloutTests in apps/api/test_shell.py:

    @override_settings(DEBUG=True, VITE_DEV_SERVER_URL="http://localhost:5173")
    def test_vue_shell_loads_shared_theme_before_vite_assets(self):
        with self.rollout_env(VUE_TEACHER_EXPERIMENTS_ENABLED="1"):
            response = self.client.get(self.url)
        body = response.content.decode()
        self.assertIn("/static/css/kt-theme.css", body)
        self.assertLess(body.index("/static/css/kt-theme.css"), body.index("/@vite/client"))

- [ ] **Step 2: Verify the tests fail**

Run:

    .\.venv\Scripts\python.exe manage.py test tests.test_ui_contracts.UIContractTests.test_shared_theme_is_loaded_before_django_surface_styles tests.test_ui_contracts.UIContractTests.test_shared_theme_defines_approved_tokens apps.api.test_shell.VueShellRolloutTests.test_vue_shell_loads_shared_theme_before_vite_assets --verbosity 2

Expected: failures because kt-theme.css and link elements do not exist.

- [ ] **Step 3: Create the canonical theme**

Create static/css/kt-theme.css with the approved values:

    :root {
      color-scheme: light;
      --kt-brand-900: #0c3266;
      --kt-brand-800: #123f7b;
      --kt-action-600: #145bea;
      --kt-action-700: #0f49c6;
      --kt-action-050: #edf4ff;
      --kt-ink: #111827;
      --kt-muted: #667085;
      --kt-page: #f6f8fb;
      --kt-surface: #ffffff;
      --kt-surface-subtle: #f9fafb;
      --kt-border: #dfe5ee;
      --kt-border-strong: #c7d0dd;
      --kt-success: #229246;
      --kt-success-bg: #edf8f0;
      --kt-warning: #b56a00;
      --kt-warning-bg: #fff3dc;
      --kt-danger: #c93b3b;
      --kt-danger-bg: #fff0f0;
      --kt-info: #295795;
      --kt-info-bg: #f1f6ff;
      --kt-font-sans: "Segoe UI", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif;
      --kt-font-xs: 0.75rem;
      --kt-font-sm: 0.8125rem;
      --kt-font-base: 0.9375rem;
      --kt-font-lg: 1.125rem;
      --kt-font-title: clamp(1.625rem, 2vw, 1.875rem);
      --kt-radius-badge: 999px;
      --kt-radius-control: 0.5rem;
      --kt-radius-surface: 0.75rem;
      --kt-shadow-raised: 0 0.75rem 2.4rem rgb(12 50 102 / 7.5%);
      --kt-shadow-control: 0 0.25rem 0.8rem rgb(20 91 234 / 14%);
      --kt-motion-fast: 120ms;
      --kt-motion-base: 180ms;
      --kt-content-max: 90rem;
      --kt-z-nav: 20;
      --kt-z-actions: 30;
      --kt-z-dialog: 50;
    }

- [ ] **Step 4: Load the theme in both shells**

In templates/base.html, load kt-theme.css after Bootstrap and before app.css. In templates/spa/vue_shell.html, add load static and load kt-theme.css before Vite assets.

- [ ] **Step 5: Map Vue aliases**

Replace hard-coded values in frontend/src/design-system/tokens.css with aliases:

    :root {
      --kt-color-page: var(--kt-page);
      --kt-color-surface: var(--kt-surface);
      --kt-color-text: var(--kt-ink);
      --kt-color-muted: var(--kt-muted);
      --kt-color-border: var(--kt-border);
      --kt-color-primary: var(--kt-action-600);
      --kt-color-primary-hover: var(--kt-action-700);
      --kt-color-success: var(--kt-success);
      --kt-color-success-bg: var(--kt-success-bg);
      --kt-color-warning: var(--kt-warning);
      --kt-color-warning-bg: var(--kt-warning-bg);
      --kt-color-danger: var(--kt-danger);
      --kt-color-danger-bg: var(--kt-danger-bg);
      --kt-radius-badge: var(--kt-radius-badge);
      --kt-radius-control: var(--kt-radius-control);
      --kt-radius-surface: var(--kt-radius-surface);
      --kt-shadow-raised: var(--kt-shadow-raised);
      --kt-font-sans: var(--kt-font-sans);
      --kt-motion-fast: var(--kt-motion-fast);
    }

- [ ] **Step 6: Run tests and inspect diff**

Run the command from Step 2, then:

    git diff --check
    git diff -- static/css/kt-theme.css templates/base.html templates/spa/vue_shell.html frontend/src/design-system/tokens.css tests/test_ui_contracts.py apps/api/test_shell.py

Expected: all three tests pass and no whitespace errors.

---

### Task 2: Rebuild the shared application header

**Files:**

- Modify: templates/base.html
- Modify: static/css/app.css
- Modify: frontend/src/design-system/components/AppHeader.vue
- Modify: frontend/src/design-system/components.test.ts
- Modify: frontend/src/design-system/foundations.css

- [ ] **Step 1: Write failing structure tests**

Add a Django test asserting brand-mark, primary-navigation, teacher aria label, and absence of navbar-dark. Extend components.test.ts to assert .kt-brand-mark text KT, .kt-wordmark__name text KT Data Lab, and user metadata.

- [ ] **Step 2: Run focused tests**

Run:

    .\.venv\Scripts\python.exe manage.py test tests.test_ui_contracts.UIContractTests.test_django_header_uses_light_single_line_brand_shell --verbosity 2
    Set-Location frontend
    $env:PATH='C:\Users\31606\Downloads\ATOP-tooling\node-v24.20.0-win-x64;'+$env:PATH
    npm run test:unit -- --run src/design-system/components.test.ts

Expected: failures on the old dark header and missing Vue brand mark.

- [ ] **Step 3: Update Vue AppHeader**

Use this exact visual structure while keeping the slot responsible for navigation:

    <header class="kt-app-header">
      <div class="kt-shell kt-app-header__inner">
        <a class="kt-wordmark" href="/" aria-label="KT Data Lab 首页">
          <span class="kt-brand-mark" aria-hidden="true">KT</span>
          <span class="kt-wordmark__name">KT Data Lab</span>
        </a>
        <nav class="kt-global-nav" aria-label="全局导航"><slot /></nav>
        <span class="kt-user-name">
          <strong>{{ userName }}</strong>
          <small v-if="userMeta">{{ userMeta }}</small>
        </span>
      </div>
    </header>

- [ ] **Step 4: Update Django header**

Keep every URL, aria-current, collapse target, account label, CSRF token and logout action. Make the desktop shell a single white row with brand left, existing navigation center, and existing account controls right. Keep the mobile collapse behavior and do not invent a notification feature.

- [ ] **Step 5: Apply shared header CSS**

Use the same 68px header, 36px KT mark, dark-blue wordmark, 15px navigation, 3px active underline and single-line desktop constraint in app.css and foundations.css. Add explicit mobile wrapping below 1024px without changing the collapse contract.

- [ ] **Step 6: Verify**

Run Django header/navigation tests and Vue design-system plus StudentCoursesLayout tests. Inspect the diff and confirm no route or action changed.

---

### Task 3: Apply typography and shared component styling

**Files:**

- Modify: static/css/app.css
- Modify: frontend/src/design-system/foundations.css
- Modify: frontend/src/design-system/components/PageHeader.vue
- Modify: frontend/src/design-system/components.test.ts

- [ ] **Step 1: Add failing component contracts**

Add a Django test for shared radius, 40px controls, tabular numbers and reduced-motion. Extend PageHeader tests to verify title, description slot and actions slot remain independently addressable.

- [ ] **Step 2: Run focused tests and confirm the new contract fails**

Run tests/test_ui_contracts and frontend/src/design-system/components.test.ts.

- [ ] **Step 3: Implement typography and controls**

Apply 15px body, 30px page title, 18px section title, 13px table text, 40px controls, 8px control radius, 12px surface radius, visible focus and 1px active translation.

- [ ] **Step 4: Implement shared surfaces**

Update Django and Vue styles for buttons, inputs, cards, tables, tabs, status badges, alerts, empty states, drawers and paginators. Tables use one row divider, low-contrast headers and tabular numbers. Shadows appear only on elevated workflow surfaces.

Core table rule:

    .table,
    .kt-table {
      width: 100%;
      margin: 0;
      border-collapse: collapse;
      color: var(--kt-ink);
      font-variant-numeric: tabular-nums;
    }

    .table > :not(caption) > * > *,
    .kt-table th,
    .kt-table td {
      padding: 0.8rem 0.9rem;
      border-bottom: 1px solid var(--kt-border);
      background: transparent;
      text-align: left;
      vertical-align: middle;
    }

- [ ] **Step 5: Verify**

Run:

    .\.venv\Scripts\python.exe manage.py test tests.test_ui_contracts --verbosity 2
    Set-Location frontend
    npm run test:unit -- --run src/design-system/components.test.ts

Expected: pass with unchanged props, events, visible strings and form hooks.

---

### Task 4: Update all teacher Vue workspaces

**Files:**

- Modify the four Teacher*Layout.vue files listed in File map.
- Modify the four teacher feature CSS files listed in File map.
- Test the existing teacher feature test files.

- [ ] **Step 1: Add layout parity assertions**

Each layout test must verify existing destinations remain present and the current destination retains aria-current or is-active. Do not assert CSS values in unit tests.

- [ ] **Step 2: Run teacher feature tests**

Run:

    Set-Location frontend
    npm run test:unit -- --run src/features/teacher-experiments src/features/teacher-question-banks src/features/teacher-classes src/features/teacher-data-center

- [ ] **Step 3: Normalize presentation-only layout**

Keep API fetches, endpoints, route params, RouterLink targets and bootstrap access unchanged. Normalize header, course context, course navigation and main wrappers only.

- [ ] **Step 4: Apply approved feature CSS**

For all four workspaces:

- Use a 1440px content maximum and 40px desktop gutters.
- Use a white course context strip and single bottom border.
- Use 30px page titles and 18px section titles.
- Use text tabs with a 3px electric-blue active indicator.
- Use 12px surfaces and low-strength shadows.
- Collapse every multi-column detail, wizard and metric layout below 768px.
- Remove duplicate hard-coded navy, gray, radius and shadow values when a token exists.

- [ ] **Step 5: Verify**

Run teacher feature tests, npm run typecheck and npm run build. Confirm no API, router, generated schema, props or emitted event changed.

---

### Task 5: Update student Vue and assessment runtime

**Files:**

- Modify the student and assessment files listed in File map.
- Test existing student and assessment test files.

- [ ] **Step 1: Add shell assertions**

StudentCoursesLayout.test.ts must verify KT Data Lab, current My Courses navigation and existing Experiment Tasks/My Learning destinations. Keep all StudentAssessment behavior assertions unchanged.

- [ ] **Step 2: Run focused tests**

Run:

    Set-Location frontend
    npm run test:unit -- --run src/features/student-courses src/features/student-learning src/features/student-results src/assessment-runtime

- [ ] **Step 3: Apply student hierarchy**

Use the shared white header, compact course context, task cards with real status, numeric progress, one server-authorized action, desktop result tables and existing mobile card lists. Result summaries use numbers rather than filled progress tracks.

- [ ] **Step 4: Restyle assessment runtime**

Preserve every selector used by StudentAssessment.vue and process bridge tests. Apply shared focus header, 12px question surface, 44px primary actions, visible focus, compact navigation drawer and explicit single-column mobile actions. Do not modify API, text observation or process bridge TypeScript.

- [ ] **Step 5: Verify**

Run the focused tests. Inspect the diff and confirm answer serialization, process events, deadlines, result visibility and route logic did not change.

---

### Task 6: Bring Django pages to the shared system

**Files:**

- Modify static/css/app.css.
- Modify the high-value Django templates listed in File map.
- Test tests/test_ui_contracts.py and tests/test_course_ui_parity.py.

- [ ] **Step 1: Add high-value page wrapper contracts**

Assert experiment detail uses page-header and surface, experiment form uses page-header and form-section, publish wizard uses workflow-steps and surface, and results uses page-header and table-shell.

- [ ] **Step 2: Run the new contract and confirm missing wrappers fail**

Run the single test with unittest -v.

- [ ] **Step 3: Add presentation-only wrappers**

Keep every input, name, id, button text, method, action, CSRF tag and data hook in its current order. Add only page-header, surface, surface-elevated, workflow-steps, form-section and table-shell wrappers where needed.

- [ ] **Step 4: Implement page layouts**

- Experiment detail: 1.28fr and 0.94fr desktop grid.
- Publish wizard: horizontal two-step indicator, schedule table, warning panel and bottom actions.
- Experiment form: four visual sections without field reordering.
- Results: result table plus compact metric summary.
- Data workbench: shared local tabs.
- Login: centered 26rem form surface and shared brand.
- Student question and complete: focused width and shared controls.
- Below 768px every multi-column layout becomes one column.

- [ ] **Step 5: Run Django presentation and behavior tests**

Run:

    .\.venv\Scripts\python.exe manage.py test tests.test_ui_contracts tests.test_course_ui_parity --verbosity 2
    .\.venv\Scripts\python.exe manage.py test apps.experiments.test_a_plus_publication apps.experiments.test_participant_rules apps.assessment.test_student_api apps.assessment.test_deadline_decisions --verbosity 2

Expected: pass without changes to business code.

- [ ] **Step 6: Inspect sensitive attributes**

Search the diff for name=, id=, action=, method=, data-, URL tags, API paths and button labels. Revert any semantic change not required by a visual wrapper.

---

### Task 7: Add responsive and accessibility E2E gates

**Files:**

- Modify: frontend/e2e/foundation.spec.ts
- Modify: frontend/e2e/student.spec.ts
- Modify shared CSS only when a concrete failure appears.

- [ ] **Step 1: Add desktop overflow and button-wrap checks**

At 1440x900, visit a teacher experiment page. Assert document scrollWidth is no greater than innerWidth and every button has scrollWidth no greater than clientWidth plus one pixel.

- [ ] **Step 2: Add narrow student overflow check**

At 390x844, visit a student task page and assert no root overflow.

- [ ] **Step 3: Add reduced-motion and focus check**

Emulate reduced motion, press Tab, and assert :focus-visible is visible.

- [ ] **Step 4: Run E2E with approved runtimes**

Run:

    $env:PATH='C:\Users\31606\Downloads\ATOP-tooling\node-v24.20.0-win-x64;C:\Users\31606\Downloads\ATOP-tooling\PostgreSQL-17.11-portable\pgsql\bin;'+$env:PATH
    $env:PLAYWRIGHT_BROWSERS_PATH='C:\Users\31606\Downloads\ATOP-tooling\pw-fresh-browsers'
    $env:POSTGRES_HOST='127.0.0.1'
    $env:POSTGRES_PORT='55432'
    $env:POSTGRES_USER='kt_user'
    $env:POSTGRES_PASSWORD='kt_password'
    $env:POSTGRES_DB='kt_e2e'
    npm run test:e2e -- foundation.spec.ts student.spec.ts

Expected: pass. Fix exact containers; never hide overflow on the root as a fallback.

---

### Task 8: Full verification and refreshed review pack

**Files:**

- Create: E:/ATOP/outputs/ui-refresh-screenshots/*.png
- Create: E:/ATOP/outputs/UI更新截图索引.md
- Verify all Task 1 through Task 7 files.

- [ ] **Step 1: Run frontend gates with Node 24**

Run:

    Set-Location frontend
    $env:PATH='C:\Users\31606\Downloads\ATOP-tooling\node-v24.20.0-win-x64;'+$env:PATH
    npm run lint
    npm run typecheck
    npm run test:unit
    npm run api:check
    npm run build

Expected: all exit 0.

- [ ] **Step 2: Run Django gates**

Run:

    Set-Location ..
    .\.venv\Scripts\python.exe manage.py test tests.test_ui_contracts tests.test_course_ui_parity apps.api.test_shell apps.experiments.test_a_plus_publication apps.experiments.test_participant_rules apps.assessment.test_student_api apps.assessment.test_deadline_decisions --verbosity 2
    .\.venv\Scripts\python.exe manage.py collectstatic --noinput --dry-run

Expected: all tests pass and static assets resolve.

- [ ] **Step 3: Run full Playwright**

Use Task 7 environment and run npm run test:e2e. Expected: pass.

- [ ] **Step 4: Capture refreshed screenshots**

At 1440x900, capture the same functional coverage as E:/ATOP/outputs/功能目录与截图索引.md into E:/ATOP/outputs/ui-refresh-screenshots. Cover login, course, settings, question banks, classes, analytics, experiment authoring/publishing/results, data center, student tasks/learning/results and assessment completion.

- [ ] **Step 5: Write and validate screenshot index**

Create E:/ATOP/outputs/UI更新截图索引.md with a project tree and links to every image. Validate links:

    $root='E:\ATOP\outputs'
    $content=Get-Content -LiteralPath (Join-Path $root 'UI更新截图索引.md') -Raw
    $links=[regex]::Matches($content,'\]\(ui-refresh-screenshots/([^\)]+\.png)\)') | ForEach-Object { $_.Groups[1].Value } | Sort-Object -Unique
    $missing=@($links | Where-Object { -not (Test-Path -LiteralPath (Join-Path (Join-Path $root 'ui-refresh-screenshots') $_)) })
    if($missing.Count){ $missing; exit 2 }

Expected: zero missing links.

- [ ] **Step 6: Final pre-flight**

Run:

    git diff --check
    git status --short
    git diff --stat

Confirm one light theme, one electric-blue action palette, one radius system, single-line desktop navigation, no button wrapping, no overflow at 1440px or 390px, no business-logic changes, and no unrelated user modification overwritten.

---

## Completion evidence

Implementation is complete only when all eight tasks are checked, all listed commands pass, the refreshed screenshot index resolves every linked image, and the final diff contains only visual-system, presentation tests and requested output artifacts.
