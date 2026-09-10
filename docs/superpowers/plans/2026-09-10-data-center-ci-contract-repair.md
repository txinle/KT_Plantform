# Data-center CI Contract Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the application E2E job by preserving the redesigned data-center UI, giving the advanced empty-scope state a stable page heading, and aligning stale browser contracts with current behavior.

**Architecture:** Keep Django APIs and data-scope semantics unchanged. Make one accessibility-focused Vue template adjustment, then update only the browser assertions that still encode retired labels, controls, or the shared sidebar width.

**Tech Stack:** Vue 3, TypeScript, Vitest, Playwright, Django 5.2, PostgreSQL 17

---

### Task 1: Preserve the advanced-page heading in an incomplete scope

**Files:**
- Modify: `frontend/src/features/teacher-data-center/TeacherDataAdvanced.test.ts`
- Modify: `frontend/src/features/teacher-data-center/TeacherDataAdvanced.vue`

- [ ] **Step 1: Write the failing component test**

Add a test that returns the existing advanced response with `scope.is_complete: false`, then verifies one `研究数据预览` level-one heading and the `请先选择实验和班级` empty state:

```ts
it('keeps its page heading when the research scope is incomplete', async () => {
  const incomplete = response()
  incomplete.scope.is_complete = false
  mocks.api.advanced.mockResolvedValueOnce(incomplete)
  const wrapper = mount(TeacherDataAdvanced)
  await flushPromises()
  expect(wrapper.findAll('h1')).toHaveLength(1)
  expect(wrapper.get('h1').text()).toBe('研究数据预览')
  expect(wrapper.text()).toContain('请先选择实验和班级')
})
```

- [ ] **Step 2: Verify the test fails for the missing heading**

Run from `frontend`:

```powershell
npm run test:unit -- src/features/teacher-data-center/TeacherDataAdvanced.test.ts
```

Expected: the new test fails because the incomplete-scope branch currently renders no `h1`.

- [ ] **Step 3: Move the overview heading before the scope branch**

In `TeacherDataAdvanced.vue`, render the existing overview header immediately inside `v-else-if="data"`, before the incomplete-scope `EmptyState`. Remove the same header from the complete-scope overview branch so every overview state has exactly one heading:

```vue
<template v-else-if="data">
  <header v-if="view === 'overview'" class="kt-research-heading">
    <div>
      <p class="kt-research-kicker">实验数据</p>
      <h1>研究数据预览</h1>
      <p>按实验、班级、学生和作答会话查看可直接用于研究的六张数据表。</p>
    </div>
    <aside v-if="selectedStudent" class="kt-step-note">
      <ResearchDataIcon name="info" />
      <div><strong>步骤 1/5：筛选单一学生</strong><span>通过选择学生，查看该学生的研究数据预览。</span></div>
    </aside>
  </header>
```

- [ ] **Step 4: Verify focused component tests pass**

Run the same Vitest command. Expected: all `TeacherDataAdvanced` tests pass.

- [ ] **Step 5: Commit the component repair**

```powershell
git add frontend/src/features/teacher-data-center/TeacherDataAdvanced.vue frontend/src/features/teacher-data-center/TeacherDataAdvanced.test.ts
git commit -m "fix: preserve advanced data page identity"
```

### Task 2: Align stale browser contracts with the approved data-center UI

**Files:**
- Modify: `frontend/e2e/foundation.spec.ts`
- Modify: `frontend/e2e/ui-layout.spec.ts`

- [ ] **Step 1: Update the foundation data-center contract**

Use `导出研究数据` for both export URLs and `研究数据预览` for the advanced route and reload. In the scope mutation test, select the experiment checkbox and class select, then verify the new summary:

```ts
await scope.getByRole('checkbox', { name: '演示交互数据采集', exact: true }).check()
await scope.locator('select[name="class_id"]').selectOption({ label: '2026 认知追踪实验班' })
// after the POST succeeds
await expect(scope).toContainText('班级：2026 认知追踪实验班')
await expect(scope).toContainText('已选择实验：演示交互数据采集（1 个实验）')
```

Update the responsive advanced route heading to `研究数据预览`.

- [ ] **Step 2: Preserve the explicit sidebar-width contract per workspace**

In `inspect`, derive the expected width from the workspace type rather than weakening the equality assertion:

```ts
const expectedSidebarWidth = await page.locator('.kt-workspace').evaluate(
  element => element.classList.contains('kt-data-center') ? 236 : 224,
)
expect(sidebar?.width).toBe(expectedSidebarWidth)
```

- [ ] **Step 3: Run the affected Chromium E2E cases**

After preparing isolated `kt_e2e`, run:

```powershell
npx playwright test e2e/foundation.spec.ts e2e/ui-layout.spec.ts --project=chromium --grep "data-center|teacher course and module|Vue course forms"
```

Expected: all selected tests pass, including scope mutation, current labels, empty-scope heading, and the two data-center width visits.

- [ ] **Step 4: Run cross-browser heading/layout audits**

```powershell
npx playwright test e2e/layout-audit.spec.ts e2e/standalone-ui.spec.ts
```

Expected: Chromium, Firefox, and WebKit complete with no missing advanced-page heading.

- [ ] **Step 5: Commit the E2E contract repair**

```powershell
git add frontend/e2e/foundation.spec.ts frontend/e2e/ui-layout.spec.ts
git commit -m "test: align data-center browser contracts"
```

### Task 3: Verify, integrate, and run GitHub CI

**Files:**
- No additional source files

- [ ] **Step 1: Run focused frontend checks**

```powershell
npm run lint
npm run typecheck
npm run test:unit
npm run build
```

Expected: every command exits 0.

- [ ] **Step 2: Run the full application E2E configuration**

```powershell
npx playwright test --config playwright.config.ts
```

Expected: 0 failed tests across Chromium, Firefox, and WebKit.

- [ ] **Step 3: Inspect branch state and diff**

```powershell
git status --short --branch
git diff --check main...HEAD
git diff --stat main...HEAD
```

Expected: a clean feature worktree and no whitespace errors.

- [ ] **Step 4: Fast-forward local main and push GitHub**

From the clean main worktree:

```powershell
git merge --ff-only fix/student-assessment-p1-p3-v2
git push origin main
```

Expected: local and GitHub `main` point to the verified feature head. Do not push Gitee.

- [ ] **Step 5: Observe the triggered GitHub Actions run**

Use `gh run list` to identify the run for the pushed main SHA and `gh run watch` or bounded status reads until completion. Report every job conclusion and any unresolved failure without claiming success early.

