# Data Center Workbench v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the course data center as a task-oriented teaching workspace, reproducible research-data interface, and auditable export system without weakening existing assessment invariants.

**Architecture:** Add immutable version-level class/participant snapshots and effective-grade semantics first. A single `DataScope` service will validate and apply the same range to summary pages, quality reports, table previews, and exports. The UI becomes separate Django template routes sharing one scope bar; the existing table view becomes an advanced tool with compatible deep links.

**Tech Stack:** Django 5.2, Django templates, Bootstrap 5.3, native CSS/JavaScript, SQLite/PostgreSQL-compatible ORM.

---

### Task 1: Freeze class membership and grade truth

**Files:**
- Modify: `apps/experiments/models.py`, `apps/experiments/services.py`
- Modify: `apps/assessment/models.py`, `apps/assessment/services.py`
- Create: migrations in `apps/experiments/migrations/` and `apps/assessment/migrations/`
- Test: `apps/experiments/test_class_snapshots.py`, `apps/assessment/test_grade_effective_state.py`

- [ ] Add failing tests for immutable native and legacy class snapshots, session linkage, one effective grade, candidate regrade, and voided grades.
- [ ] Run targeted tests and confirm failures identify missing snapshots/effective state.
- [ ] Add `ExperimentClassSnapshot`, version participant snapshots, nullable `ExperimentSession.class_snapshot`, and GradeRecord validity fields/constraints.
- [ ] Freeze new-version memberships at publication; migrate historical sessions into explicitly labelled legacy or unrecorded snapshots without guessing ClassGroup identity.
- [ ] Update grade services transactionally: candidate does not replace effective, completed supersedes the former effective, voided grades never enter effective statistics.
- [ ] Run targeted tests and migration checks; commit the isolated model-contract change.

### Task 2: Build scope, timing, replay presentation, and five-state quality services

**Files:**
- Create: `apps/analytics/data_scope.py`, `apps/analytics/teaching_data.py`, `apps/analytics/quality_reports.py`
- Modify: `apps/analytics/services.py`, `apps/assessment/event_invariants.py`
- Test: `apps/analytics/test_data_scope.py`, `apps/analytics/test_teaching_data.py`, `apps/analytics/test_quality_reports.py`

- [ ] Add failing tests for strict scope intersections, foreign-object denial, remembered/temporary scope serialization, whole-session time windows, multi-version teaching blocks, and legacy not-evaluable results.
- [ ] Add `DataScope` normalization, validation, canonical serialization, and session/attempt/event queryset adapters.
- [ ] Add `TimingSummary` with active, estimated-active, wall-clock, and unavailable outcomes; never use `Attempt.active_seconds` as the presentation truth.
- [ ] Add teaching summary and replay presentation adapters which preserve raw event semantics and expose quality labels rather than psychological inference.
- [ ] Add invariant report rows with PASS, WARN, FAIL, NOT_EVALUABLE, and NOT_APPLICABLE.
- [ ] Run targeted tests; commit the service layer.

### Task 3: Make exports scope-consistent, complete, and purge-safe

**Files:**
- Modify: `apps/analytics/models.py`, `apps/analytics/services.py`, `apps/analytics/export_catalog.py`, `apps/analytics/views.py`
- Create: migration in `apps/analytics/migrations/`
- Test: `apps/analytics/test_export_scope_v2.py`, `apps/analytics/test_export_tombstones.py`

- [ ] Add failing tests that a package uses the same DataScope as its preview, every request gets a new snapshot/job, KT/CD reject all-version scopes, and process/complete packages contain snapshots, stable-input observations, submissions, grades, conditions, and class snapshots.
- [ ] Persist only canonical scope filters in ExportSnapshot and produce manifest lineage for every package.
- [ ] Preserve each ExportJob record; artifact removal marks it expired rather than deleting the row.
- [ ] Add governance-only redaction after invalidation: delete artifacts and identifying scope/manifest values while retaining the minimal tombstone.
- [ ] Recheck course-member authorization on preflight, generation, download, expiration, and purge.
- [ ] Run targeted tests; commit the export-governance change.

### Task 4: Split the data center into task pages and retain advanced compatibility

**Files:**
- Modify: `apps/accounts/course_urls.py`, `apps/accounts/course_views.py`, `apps/analytics/views.py`
- Create: templates under `templates/teacher/data_center/`
- Modify: `templates/teacher/course_data.html`, `templates/teacher/course_quality.html`, `static/css/app.css`, `static/js/app.js`
- Test: `apps/analytics/test_data_center.py`, `apps/analytics/test_data_center_ui_v2.py`

- [ ] Add failing route and template-context tests for overview, teaching drill-down, export preflight, quality, job history, advanced tables, and legacy table URL redirects.
- [ ] Implement the shared scope bar and POST-only scope persistence while preserving CSRF and existing URL compatibility.
- [ ] Implement the six task pages: four-fact overview, student/session/attempt drill-down, two-step export, five-state quality report, export history, and advanced table/schema/lineage browser.
- [ ] Keep table field names, raw JSON, and technical filenames out of default teacher views; expose them only under Advanced Data Tools.
- [ ] Add empty, blocked, expired, invalidated, and error states; preserve keyboard focus, labels, and mobile layout.
- [ ] Run route/UI tests; commit the page change.

### Task 5: Verify end-to-end and review generated artifacts

**Files:**
- Modify: `docs/data-dictionary.md`, `docs/changes/2026-08-28-data-center-workbench-v2.md`
- Test: affected app tests and the full suite

- [ ] Update the data dictionary with snapshot, effective-grade, scope, timing, quality, export, and purge semantics.
- [ ] Run migrations against a fresh database and inspect legacy backfill behavior.
- [ ] Run `python manage.py check`, `python manage.py makemigrations --check --dry-run`, `python manage.py test`, `python manage.py collectstatic --noinput`, `node --check static/js/app.js`, and `node --check static/js/assessment.js`.
- [ ] Exercise teacher and student paths at 1440 × 900 and 390 × 844; record only observations actually performed.
- [ ] Commit documentation and verification evidence, then use branch-completion workflow.
