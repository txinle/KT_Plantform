# Research Data Phase 2 Native Collection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans`; use `superpowers:test-driven-development` for every behavior change and `.agents/skills/kt-research-ui` for student/teacher UI work.

**Goal:** Collect scientifically interpretable native process data from the existing student assessment flow without changing the business truth of visits, drafts, submissions, scoring, feedback, authorization or idempotency.

**Architecture:** Existing `Attempt`, `SubmissionEvent`, session and scoring services remain authoritative. A same-origin native JavaScript collector writes a durable IndexedDB outbox and uploads versioned event envelopes to authenticated Django endpoints. Server-side lifecycle/commit events are appended from existing business transactions. Time synchronization uses a two-step round-trip protocol; no server-only approximation is labelled as RTT. Confidence assignment is materialized independently of event presence, and feedback remains withheld until the configured Confidence step resolves.

**Tech Stack:** Django 5.2, Django Templates, native JavaScript, IndexedDB, Bootstrap 5.3, SQLite/PostgreSQL, Django TestCase/TransactionTestCase, Node syntax checks.

---

## Locked API and measurement decisions

- Keep all existing assessment URLs and request keys. New fields in draft/submit bodies are optional until the new collector is active.
- Add versioned endpoints under `/assessment/process/`; all are same-origin, CSRF-protected and student-owned.
- A batch contains at most 100 events. Success returns an acknowledgement per client sequence; the browser deletes only acknowledged rows.
- `client_instance_id` is a random UUID scoped to one page time origin. Refresh creates a new instance; IndexedDB retains unsent events from previous instances.
- A durable per-instance `client_sequence_no` is allocated transactionally in IndexedDB before an event is considered queued.
- Time sync is two-step: create a pending sample, then finalize it with the observed client receive monotonic time and receive wall time. Only finalized valid samples generate `estimated_occurred_at_utc`.
- Text/numeric/fill response snapshots are created by the draft endpoint and returned to the browser; events reference the returned snapshot. Choice states remain directly in `response_state_changed`.
- `response_commit_requested` is durably recorded before the scoring transaction. `response_committed` is written in the same database transaction as `Attempt` and `SubmissionEvent`. Rejections use a stable reason code and no exception text.
- Confidence assignment rows are created for every attempt, including `not_assigned` and `control`. Missing events never determine assignment.
- Feedback payload is returned only after Confidence is resolved when the condition is `prompt`. The browser records `feedback_shown` and `explanation_shown` only when rendered.
- No mouse coordinates, key contents, clipboard, browser history, camera, microphone, GPS, eye tracking or physiological signals are collected.

## Task 1: Lock payload schemas and time-sync state semantics

**Files:**
- Modify: `apps/assessment/event_schemas/response_state_changed/v1.json`
- Modify: lifecycle/commit/Confidence schemas only where required by the approved payload contract
- Modify: `apps/assessment/models.py`
- Create: `apps/assessment/migrations/0008_time_sync_pending_state.py`
- Modify: `apps/assessment/test_event_schema_registry.py`
- Create: `apps/assessment/test_time_sync_services.py`

- [x] **Step 1: Write failing schema and time-sync tests**

Prove that choice responses accept the direct state form, text-like responses require `response_snapshot_id + content_hash + content_length + change_source`, and neither form accepts undeclared key-level data. Prove that a new time-sync sample is `pending` with null estimates and that only a valid finalize operation fills estimates.

- [x] **Step 2: Add pending/valid/invalid/clock_changed sync states**

Make `estimated_clock_offset_ms` and `estimated_rtt_ms` nullable until finalization. Preserve the exact raw timestamps; do not silently replace invalid samples.

- [x] **Step 3: Implement two-step time-sync service**

Compute network RTT as client monotonic round trip minus server processing duration. Compute offset from client/server midpoints. Reject negative, implausibly large or cross-instance observations. Mark a client wall-clock discontinuity when wall time and monotonic extrapolation differ beyond the documented threshold.

- [x] **Step 4: Verify migration reversibility and schema hashes**

Run schema registry, model and migration tests. Record that changing a v1 schema is permitted only because native collection has not yet shipped; after this phase, changes require v2.

## Task 2: Add authenticated batch and time-sync endpoints

**Files:**
- Create: `apps/assessment/process_views.py`
- Modify: `apps/assessment/urls.py`
- Create: `apps/assessment/event_batch_services.py`
- Create: `apps/assessment/test_process_endpoints.py`

- [ ] **Step 1: Write failing authorization and contract tests**

Cover CSRF, non-student rejection, cross-student session/attempt/assignment references, malformed UUID/time/decimal values, unknown schemas, over-100 batches and payload size limits. A valid batch must preserve client order but allocate ingest order independently.

- [ ] **Step 2: Implement strict envelope parsing**

Resolve all foreign keys from the authenticated student session rather than trusting submitted relationships. Reject inconsistent links before appending any batch row. Return structured per-event conflict/validation errors without echoing confidential payload values.

- [ ] **Step 3: Implement batch idempotency**

Repeated identical batches acknowledge existing rows. A conflicting event at the same client key returns HTTP 409 and leaves every original event unchanged. Define whether the batch is atomic; the selected default is all-or-nothing for validation/linkage and per-event idempotent for already persisted rows.

- [ ] **Step 4: Implement create/finalize time-sync endpoints**

Bind samples to the authenticated browser instance and return server receive/respond timestamps. Finalization is idempotent only for identical observations and rejects attempts to rewrite a finalized sample.

## Task 3: Add response snapshot services without redefining drafts

**Files:**
- Create: `apps/assessment/response_snapshot_services.py`
- Modify: `apps/assessment/services.py`
- Modify: `apps/assessment/views.py`
- Modify: `apps/assessment/test_question_types.py`
- Create: `apps/assessment/test_response_snapshots.py`

- [ ] **Step 1: Write failing snapshot tests**

Cover UTF-8 SHA-256, exact character length, same-attempt deduplication, cross-attempt separation, numeric/fill/short types, empty drafts, repeated 800 ms saves and forced saves before navigation/submit.

- [ ] **Step 2: Create snapshots inside the existing draft transaction**

Do not convert a snapshot into a submission and do not change `DraftSaveEvent` compatibility semantics. Return snapshot ID/hash/length from the existing draft endpoint for text-like response event construction.

- [ ] **Step 3: Keep choice response states canonical**

Single/true-false states are scalar; multiple-choice states are sorted unique arrays. No duplicate derived answer-change counters are stored.

## Task 4: Integrate lifecycle, commit and Confidence projections transactionally

**Files:**
- Create: `apps/assessment/confidence_services.py`
- Modify: `apps/assessment/services.py`
- Modify: `apps/assessment/views.py`
- Modify: `apps/assessment/test_event_services.py`
- Create: `apps/assessment/test_transactional_process_events.py`
- Create: `apps/assessment/test_confidence_flow.py`

- [ ] **Step 1: Write failing business/event invariant tests**

Prove `response_committed` implies one `Attempt` and one `SubmissionEvent`, shares `correlation_id`, and rolls back with business state on injected event-write failure. Prove invalid submit attempts produce typed `response_commit_rejected` without a committed event.

- [ ] **Step 2: Materialize deterministic Confidence assignments**

Use frozen experiment mode, sampling unit, probability, seed and assignment version. Student-level assignment is identical across that student's attempts, session-level is identical within a session, attempt-level varies by deterministic attempt key. Create explicit rows for off/control/prompt conditions.

- [ ] **Step 3: Enforce feedback ordering**

For prompt assignments, submit returns the Confidence prompt contract and withholds correctness/answer/explanation. Confidence commit/skip updates the projection and then returns feedback according to the published experiment configuration. Control/not-assigned flows retain existing feedback timing.

- [ ] **Step 4: Append server lifecycle events from authoritative transactions**

Record session start/complete/expire/invalidate, item enter, attempt close, response commit and rejection without changing first-open, last-open, visit, draft, skip, timeout or unanswered semantics.

## Task 5: Build the durable native browser collector

**Files:**
- Create: `static/js/process-event-collector.js`
- Create: `apps/assessment/tests_js/process-event-collector.test.mjs` or the repository-approved dependency-free JS harness
- Modify: `templates/student/question.html`
- Modify: `static/js/assessment.js`

- [ ] **Step 1: Write failing dependency-free JavaScript tests**

Cover UUID instance lifecycle, atomic sequence allocation, queue-before-send, acknowledge-before-delete, identical retry, exponential backoff, online recovery, previous-instance backlog, max batch size and payload redaction.

- [ ] **Step 2: Implement IndexedDB outbox**

Use a versioned database/store, explicit transaction boundaries and no external dependency. If IndexedDB is unavailable, retain an in-memory fallback with `payload_recovered`/quality reporting; never claim durable offline capture in that mode.

- [ ] **Step 3: Implement synchronization and upload scheduling**

Sync on first load, `online`, and every 15 minutes. Upload while online, page visible or during bounded pagehide best effort. Normal fetch remains authoritative; `sendBeacon` may be used only with a CSRF-safe same-origin contract and must not delete rows without acknowledgement.

- [ ] **Step 4: Capture approved lifecycle and telemetry only**

Record rendered/left, visibility, focus, pageshow/pagehide, online/offline and heartbeat. Heartbeat runs only while the document is visible, the attempt is open and the configured interval has elapsed. Stop all timers after attempt close.

- [ ] **Step 5: Capture response events**

Record first input once, direct choice state changes, and returned text snapshot references. Force draft/snapshot completion before navigation, skip and submit. Never record keydown/up or per-keystroke text.

## Task 6: Add the Confidence UI and expose collection configuration

**Files:**
- Modify: `templates/student/question.html`
- Modify: `static/js/assessment.js`
- Modify: `templates/teacher/course_experiment_form.html`
- Modify: `templates/teacher/experiment_form.html`
- Modify: experiment detail templates
- Modify: existing CSS only as needed
- Create/modify: focused Django and DOM-contract tests

- [ ] **Step 1: Render teacher configuration fields**

Group Confidence mode, sampling unit/probability, versions and heartbeat under an explicit “研究过程采集” section. Explain that Confidence changes the student flow and is not inferred from behavior.

- [ ] **Step 2: Render accessible Confidence prompt**

Use 0–100% in 10% steps, labelled controls, keyboard operation, a visible skip action and live save/error states. Do not use native `alert` or `confirm`.

- [ ] **Step 3: Render feedback only from the resolved server response**

Record shown events after DOM insertion. Maintain feedback mode semantics and preserve correct-answer/explanation escaping.

- [ ] **Step 4: Run 1440×900 and 390×844 visual acceptance**

Verify no horizontal overflow, touch targets, focus order, status not conveyed by color alone, reduced motion and offline local assets.

## Task 7: Phase 2 release gate

- [ ] **Step 1: Run invariant and endpoint suites**

Include duplicate submits, duplicate batches, offline backlog/recovery, client ±5 minutes, wall-clock change, refresh/time-origin change, page lifecycle and injected transaction failures.

- [ ] **Step 2: Run PostgreSQL concurrency suite**

Run event allocation plus simultaneous batch/submit tests against PostgreSQL. SQLite skips do not satisfy this gate; record the actual Docker command and output.

- [ ] **Step 3: Run full platform gate**

```text
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test --verbosity 1
python manage.py collectstatic --noinput
node --check static/js/app.js
node --check static/js/assessment.js
node --check static/js/process-event-collector.js
git diff --check
```

- [ ] **Step 4: Update the data dictionary and roadmap**

Document endpoint limits, outbox guarantees/fallback, time-sync equations and error bounds, Confidence ordering, failure meanings and every shipped event producer. Do not start export implementation until raw event completeness and transaction invariants pass review.
