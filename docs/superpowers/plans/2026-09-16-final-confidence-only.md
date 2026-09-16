# Final Confidence Only Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record and display only the confidence value atomically committed with the final answer.

**Architecture:** Keep tentative confidence changes in Vue state and rely on the existing backend submission transaction to append `confidence_committed`. Filter legacy `confidence_observed/responded` rows from the teacher-facing derived behavior projection and bump its version to rebuild completed-session projections.

**Tech Stack:** Vue 3, TypeScript, Vitest, Django 5.2, Python unittest, PostgreSQL-backed materialization worker.

---

### Task 1: Stop emitting confidence values during input

**Files:**
- Modify: `frontend/src/assessment-runtime/StudentAssessment.test.ts`
- Modify: `frontend/src/assessment-runtime/StudentAssessment.vue`

- [ ] Change the confidence-flow test to expect no `confidence_observed/responded` calls after moving the slider and editing the number field, while still expecting the final submit payload to contain the last valid value.
- [ ] Run the focused Vitest test and verify it fails because the current UI emits every valid intermediate value.
- [ ] Remove event emission and its queue from the input handlers, retaining touched-state, validation, synchronization between controls, and final submission payload construction.
- [ ] Re-run the focused test and verify it passes.

### Task 2: Correct historical teacher-facing projection

**Files:**
- Modify: `apps/analytics/test_research_visits.py`
- Modify: `apps/analytics/research_events.py`
- Modify: `apps/analytics/research_materialization.py`

- [ ] Add a projection test containing `confidence_observed/responded` values followed by one `confidence_committed` event; expect only the committed confidence row.
- [ ] Run the focused Django test and verify it fails because responded observations are currently projected.
- [ ] Skip only `confidence_observed` events with `stage=responded` in `build_standard_behavior`, retaining presented and skipped observations.
- [ ] Increment `PROJECTION_VERSION` from `research-session-v1` to `research-session-v2` so reconciliation rebuilds old materializations.
- [ ] Re-run the focused Django test and verify it passes.

### Task 3: Verify and deploy

**Files:**
- Verify all modified files with `git diff --check` and `git diff`.

- [ ] Run the complete affected frontend test file, frontend typecheck, and frontend production build.
- [ ] Run the affected analytics test modules, Django system check, and migration drift check against PostgreSQL 17.
- [ ] Commit the scoped change, push the exact commit to GitHub and Gitee, deploy by fast-forward pull, rebuild affected services, and verify SHA alignment and service health.
