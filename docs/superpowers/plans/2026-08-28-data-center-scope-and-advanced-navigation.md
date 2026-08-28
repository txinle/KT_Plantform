# Data Center Scope and Advanced Navigation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the teacher-facing full research scope with experiment-and-class navigation, preserve the complete class roster, and organize advanced research tables into six searchable semantic groups.

**Architecture:** Add a focused `TeachingScope` service that resolves course roster students and experiment sessions through immutable class snapshots while leaving `DataScope` intact for research reads. Build advanced-table presentation metadata separately from export paths, then render it as semantic `<details>` groups with conditional extension modules and a right-side technical-information panel.

**Tech Stack:** Django 5 templates/views/models, Bootstrap-compatible native CSS, small native JavaScript, Django `TestCase`.

---

### Task 1: Lock the teaching-scope contract

**Files:**
- Create: `apps/analytics/teaching_scope.py`
- Modify: `apps/analytics/test_data_center_ui_v2.py`
- Modify: `apps/analytics/test_teaching_data.py`

- [ ] **Step 1: Write failing tests for the two-field scope form**

Assert that the ordinary range form contains `experiment_id` and `class_id`, and does not contain `version_id`, `student_id`, `session_id`, `condition_id`, statuses, or UTC bounds.

- [ ] **Step 2: Run the focused UI tests and verify failure**

Run: `python manage.py test apps.analytics.test_data_center_ui_v2 -v 2`

Expected: FAIL because the current shared scope editor exposes the complete `DataScope`.

- [ ] **Step 3: Write failing roster tests**

Create an active `CourseEnrollment` with no Session and assert that `build_teaching_roster()` returns the student with `data_status="not_participated"` and null teaching metrics.

- [ ] **Step 4: Run the roster test and verify failure**

Run: `python manage.py test apps.analytics.test_teaching_data -v 2`

Expected: FAIL because the current implementation starts from Session rows.

- [ ] **Step 5: Implement the minimal TeachingScope service**

Define an immutable service object containing `course`, `experiment_id`, and `class_id`; validate course ownership; expose `roster()`, `sessions()`, `attempts()`, and a serializer used only by ordinary pages.

- [ ] **Step 6: Implement roster-based teaching summaries**

Seed the result map from active `CourseEnrollment` rows, then attach scoped sessions, attempts, submissions, and effective grades. Derive the five teacher-facing data states without changing stored research status fields.

- [ ] **Step 7: Run focused scope and teaching tests**

Run: `python manage.py test apps.analytics.test_teaching_data apps.analytics.test_data_center_ui_v2 -v 2`

Expected: PASS.

### Task 2: Replace the ordinary scope editor

**Files:**
- Modify: `apps/accounts/course_views.py`
- Modify: `templates/teacher/data_center/_workbench_nav.html`
- Modify: `templates/teacher/data_center/overview.html`
- Modify: `templates/teacher/data_center/teaching.html`
- Modify: `static/css/app.css`

- [ ] **Step 1: Write failing view tests for remembered experiment/class scope**

Post `experiment_id` and `class_id`, assert the remembered ordinary scope contains only those IDs, and assert all ordinary workbench routes render the compact summary without Version text.

- [ ] **Step 2: Run the tests and verify failure**

Run: `python manage.py test apps.analytics.test_data_center_ui_v2 -v 2`

Expected: FAIL on legacy complete-scope storage and visible Version labels.

- [ ] **Step 3: Route ordinary pages through TeachingScope**

Keep `DataScope` for exports, quality and advanced technical filtering, but derive their default research scope from the chosen experiment/class without exposing research-only inputs. Preserve compatibility URLs by resolving them before advanced reads.

- [ ] **Step 4: Render the compact scope bar**

Use only experiment and live class dropdowns. Remove student, version, Session, Condition, status and UTC controls. Collapse the editor after selection and display only experiment/class labels.

- [ ] **Step 5: Render roster statuses and search**

Add a `q` name/student-number search over the already selected roster; do not write `student_id` into ordinary scope. Link a student row to drill-down parameters without changing remembered scope.

- [ ] **Step 6: Add focused CSS states**

Reduce the expanded scope height, keep controls aligned, add accessible focus styles, tabular data numerals and clear status labels without introducing a new framework.

- [ ] **Step 7: Run ordinary workbench tests**

Run: `python manage.py test apps.analytics.test_data_center_ui_v2 apps.analytics.test_data_center apps.analytics.test_teaching_data -v 2`

Expected: PASS.

### Task 3: Build semantic advanced-table metadata

**Files:**
- Create: `apps/analytics/advanced_catalog.py`
- Modify: `apps/analytics/test_data_center_ui_v2.py`
- Modify: `apps/accounts/course_views.py`

- [ ] **Step 1: Write failing tests for the six groups**

Assert every advertised table belongs to exactly one required semantic group, `q_matrix` belongs to derived research data, and `assessment_events` is marked authoritative while the event projections are classified views.

- [ ] **Step 2: Write failing tests for conditional extension modules**

Assert Confidence and Simulation entries are absent when the selected experiment neither enables nor contains those observations, and present when enabled.

- [ ] **Step 3: Run the catalog tests and verify failure**

Run: `python manage.py test apps.analytics.test_data_center_ui_v2 -v 2`

Expected: FAIL because the view currently exposes a flat `tables` list.

- [ ] **Step 4: Implement the advanced presentation catalog**

Map export-table keys to six semantic groups, short logical names, descriptions, source roles, schema labels and full export paths. Filter optional entries using selected experiment configuration/data presence.

- [ ] **Step 5: Expose current group and technical metadata from the view**

Resolve the selected table safely, open its owning group, and keep complete path/schema/source metadata out of navigation labels.

- [ ] **Step 6: Run catalog tests**

Run: `python manage.py test apps.analytics.test_data_center_ui_v2 -v 2`

Expected: PASS.

### Task 4: Replace the flat advanced sidebar

**Files:**
- Modify: `templates/teacher/data_center/advanced.html`
- Modify: `static/css/app.css`
- Create: `static/js/data-center-advanced.js`
- Modify: `apps/analytics/test_data_center_ui_v2.py`

- [ ] **Step 1: Write failing markup tests**

Assert six semantic `<details>` groups, only the selected group has `open`, navigation labels omit `.csv` paths, and the right pane contains the complete export path and source-role labels.

- [ ] **Step 2: Run markup tests and verify failure**

Run: `python manage.py test apps.analytics.test_data_center_ui_v2 -v 2`

Expected: FAIL on the current flat list group.

- [ ] **Step 3: Render grouped navigation and technical information**

Add a search input; render logical table labels and names only; highlight the selected table; add category descriptions, the authoritative-event callout, derived-data notice and right-side technical details.

- [ ] **Step 4: Implement local table search**

Filter table links and hide empty groups by Chinese label, logical name and description. Preserve the selected group and do not mutate URL scope parameters.

- [ ] **Step 5: Add responsive CSS**

Keep the navigation between 300–500 px in ordinary use, avoid path wrapping, provide visible focus/hover/current states, and stack navigation over preview on narrow screens.

- [ ] **Step 6: Run markup and JavaScript syntax tests**

Run: `python manage.py test apps.analytics.test_data_center_ui_v2 -v 2`

Run: `node --check static/js/data-center-advanced.js`

Expected: PASS.

### Task 5: Regression and visual verification

**Files:**
- Modify: `docs/data-center-workbench-v2.md`
- Modify: `docs/data-dictionary.md`

- [ ] **Step 1: Update the product and data contracts**

Document TeachingScope versus ResearchScope, roster-left-join semantics, the five data states, six advanced groups and the authoritative/classified event distinction.

- [ ] **Step 2: Run focused data-center tests**

Run: `python manage.py test apps.analytics.test_data_center_ui_v2 apps.analytics.test_data_center apps.analytics.test_teaching_data apps.analytics.test_data_scope apps.analytics.test_export_scope_v2 apps.analytics.test_quality_reports -v 2`

Expected: PASS.

- [ ] **Step 3: Run structural checks**

Run: `python manage.py check`

Run: `python manage.py makemigrations --check --dry-run`

Run: `python manage.py collectstatic --noinput`

Expected: all commands exit 0 and no migration changes are detected.

- [ ] **Step 4: Run the complete regression suite**

Run: `python manage.py test -v 1`

Expected: all tests pass; only explicitly designed skips remain.

- [ ] **Step 5: Inspect the authenticated pages visually**

Verify desktop and narrow layouts for overview, teaching data and advanced tools. Confirm the compact range, complete roster including “未参与”, only-current accordion expansion, table search and technical path placement.
