# Research Data Platform Implementation Roadmap

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the approved process-data collection, pseudonymized research export, identity governance, and course data-center design without breaking the platform's existing research invariants.

**Architecture:** Keep business-state tables authoritative and add append-only events, versioned state snapshots, and versioned derived exports around them. Deliver the system in five independently testable phases so that each migration leaves SQLite and PostgreSQL-compatible code in a releasable state.

**Tech Stack:** Django 5.2, Django Templates, Bootstrap 5.3, native JavaScript, SQLite, PostgreSQL 17, openpyxl, jsonschema 4.26.0

---

## Phase map

| Phase | Detailed plan | Independently verifiable outcome |
| --- | --- | --- |
| 1. Data contracts | `2026-08-25-research-data-phase-1-contracts.md` | Event envelope, schema registry, state snapshot models, stable subject IDs, item instances, confidence assignment model, migrations and invariants |
| 2. Native collection | `2026-08-25-research-data-phase-2-collection.md` | Client clock synchronization, durable offline event batches, response snapshots, lifecycle/telemetry events, transactional commit events and Confidence flow |
| 3. Research export | `2026-08-25-research-data-phase-3-export.md` | Consistent export snapshots, leased jobs, protected artifacts, multi-table ZIP/Excel packages, schema hashes and derived lineage |
| 4. Data center and governance | `2026-08-25-research-data-phase-4-ui-governance.md` | Course data-center navigation, quality dashboard, experiment data tree, separate identity area and controlled purge workflow |
| 5. Legacy, scale and release | `2026-08-25-research-data-phase-5-migration-release.md` | Idempotent legacy projections, event volume monitoring, PostgreSQL-specific tests, documentation and complete desktop/mobile acceptance audit |

## Cross-phase gates

- [ ] Existing legacy export URLs and headers remain byte-for-byte compatible where tests currently assert them.
- [ ] No change redefines first-open, last-open, draft, submission, timing, feedback, snapshot, authorization, CSRF or idempotency semantics.
- [ ] Every raw event type has a versioned JSON Schema with `additionalProperties: false` and a recorded hash.
- [ ] `server_ingest_sequence_no` is never exposed as behavioral order in copy, documentation or derived code.
- [ ] New research exports use `course_subject_id`; only explicit study exports use `study_subject_id`.
- [ ] Identity artifacts and pseudonymized research artifacts use distinct job types, directories, endpoints and authorization checks.
- [ ] SQLite remains supported; PostgreSQL-specific locking, BRIN and repeatable-read behavior is guarded and separately tested.
- [ ] Every phase ends with Django checks, relevant tests, migration drift check and `git diff --check`.
- [ ] Phases touching JavaScript also run both Node syntax checks; phases touching UI also complete the required two-size visual audit.

## Completion evidence

The roadmap is complete only when all five detailed plans are checked off, the approved design's requirement matrix has no uncovered row, the full test suite passes, PostgreSQL concurrency tests pass in the Docker deployment, static collection succeeds, JavaScript syntax checks pass, and recorded 1440×900 and 390×844 screenshots show the teacher and student acceptance paths without horizontal overflow.
