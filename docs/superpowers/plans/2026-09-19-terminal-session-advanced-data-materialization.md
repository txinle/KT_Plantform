# Terminal Session Advanced Data Materialization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Advanced Data and Data Quality expose an individual student only after that student's assessment session reaches an existing terminal state and its asynchronous projection is current, with terminal-but-pending work visible only as an anonymous aggregate preparation count.

**Architecture:** PostgreSQL remains authoritative for synchronous assessment writes. The existing `analytics-worker` builds versioned per-session projections, including quality rows, and atomically promotes the newest successful projection. Materialized endpoints read only promoted projections while the feature flag is enabled; legacy reads remain available only for courses outside the flag.

**Tech Stack:** Django 5.2, Django REST Framework, PostgreSQL 17, Vue 3, TypeScript, Vitest.

**Scope constraints:** Reuse `completed` and `expired`; do not add a timeout state, queue service, Docker service, database, commit, push, or deployment.

---

## Task 1: Enforce terminal-and-ready visibility in Advanced Data

**Files:**
- Modify: `apps/analytics/advanced_materialized.py`
- Modify: `apps/analytics/api/serializers.py`
- Test: `apps/analytics/test_advanced_materialized_api.py`

1. Add API tests proving `in_progress` sessions are absent from student/session rows and counts.
2. Add a test proving a terminal session without a current successful materialization is not identity-visible and increments only an aggregate `pending_count`.
3. Run the focused test module and confirm the new tests fail for the current live-session/fallback behavior.
4. Restrict the materialized read scope to `completed` and `expired`; derive all visible rows, session options, student summaries, workspace entries, and table counts from current successful projections only.
5. Extend preparation metadata with anonymous ready/pending/failed/updating counts and rerun the focused tests.

## Task 2: Persist quality rows in each session projection

**Files:**
- Modify: `apps/analytics/models.py`
- Add: `apps/analytics/migrations/0011_researchsessionmaterialization_quality_rows.py`
- Modify: `apps/analytics/research_materialization.py`
- Test: `apps/analytics/test_research_materialization.py`

1. Add a worker test asserting successful materialization stores serialized quality rows with stable question references.
2. Run the focused worker test and confirm failure because no quality projection exists.
3. Add a `quality_rows` JSON field and migration.
4. Build quality rows inside the existing watermark-checked materialization attempt and store them before the atomic current-version switch.
5. Rerun focused worker tests and migration checks.

## Task 3: Serve Data Quality exclusively from promoted projections

**Files:**
- Add: `apps/analytics/quality_materialized.py`
- Modify: `apps/analytics/api/views.py`
- Modify: `apps/analytics/api/serializers.py`
- Test: `apps/analytics/test_advanced_materialized_api.py`

1. Add an API test that patches the legacy quality builder and research dataset reader to fail if called while materialization is enabled.
2. Confirm the test fails against the current synchronous endpoint.
3. Implement a materialized quality payload that flattens stored quality rows from current projections, applies existing filters and pagination semantics, and returns aggregate preparation metadata.
4. Route flagged courses to this payload without any live fallback; preserve the legacy endpoint for unflagged courses.
5. Rerun focused API tests.

## Task 4: Show preparation status in the Data Quality UI

**Files:**
- Modify: `frontend/src/features/teacher-data-center/api.ts`
- Modify: `frontend/src/features/teacher-data-center/TeacherDataQuality.vue`
- Modify: `frontend/src/features/teacher-data-center/TeacherDataQuality.test.ts`
- Modify generated OpenAPI artifacts only through the repository's existing generation command if required.

1. Add a component test for the anonymous preparation message/count.
2. Confirm the test fails before UI support exists.
3. Extend the typed response and render a non-identifying informational alert when preparation is not fully ready.
4. Regenerate/validate API types if the repository contract requires it, then rerun the focused component test and TypeScript check.

## Task 5: Proportional verification and handoff

1. Apply migrations to the isolated local PostgreSQL test environment as required by the test runner.
2. Run focused backend materialization/API tests, Django checks, and the focused frontend unit/type checks.
3. Inspect `git diff` and `git status`; confirm only the design, plan, migration, implementation, tests, and generated contract files required by this feature changed.
4. Report what changed, exact verification evidence, and any unresolved issue. Do not commit, push, or deploy.
