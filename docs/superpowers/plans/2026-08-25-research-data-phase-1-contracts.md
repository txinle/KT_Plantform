# Research Data Phase 1 Contracts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the versioned event, snapshot, stable subject identity, item presentation and Confidence assignment contracts required by later collection and export phases.

**Architecture:** Models remain in their owning Django apps: stable people identifiers in `accounts`, experimental presentation/configuration in `experiments`, and process observations in `assessment`. A focused event-schema registry validates repository-owned Draft 2020-12 JSON Schemas before any event can be appended; no collection endpoint changes in this phase.

**Tech Stack:** Django 5.2, Python 3.13, SQLite/PostgreSQL, jsonschema 4.26.0, Django TestCase/TransactionTestCase

---

### Task 1: Pin and verify the JSON Schema dependency

**Files:**
- Modify: `requirements.txt`
- Test: `apps/assessment/test_event_schema_registry.py`

- [x] **Step 1: Write a failing import/version test**

```python
from importlib.metadata import version
from django.test import SimpleTestCase


class JsonSchemaDependencyTests(SimpleTestCase):
    def test_supported_jsonschema_version_is_installed(self):
        self.assertEqual(version("jsonschema"), "4.26.0")
```

- [x] **Step 2: Run the test and verify it fails**

Run: `..\\..\\.venv\\Scripts\\python.exe manage.py test apps.assessment.test_event_schema_registry.JsonSchemaDependencyTests -v 2`
Expected: FAIL because `jsonschema` is not installed or has a different version.

- [x] **Step 3: Add and install the pinned dependency**

Add to `requirements.txt`:

```text
jsonschema==4.26.0
```

Run: `..\\..\\.venv\\Scripts\\python.exe -m pip install jsonschema==4.26.0`

- [x] **Step 4: Re-run the test**

Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add requirements.txt apps/assessment/test_event_schema_registry.py
git commit -m "build: add event schema validation dependency"
```

### Task 2: Implement the immutable event schema registry

**Files:**
- Create: `apps/assessment/event_schemas/common.json`
- Create: `apps/assessment/event_schemas/` with one `v1.json` beneath each of `session_started`, `session_completed`, `session_expired`, `session_invalidated`, `item_entered`, `item_rendered`, `item_left`, `response_input_started`, `response_state_changed`, `response_commit_requested`, `response_committed`, `response_commit_rejected`, `attempt_closed`, `feedback_shown`, `explanation_shown`, `hint_opened`, `hint_closed`, `resource_opened`, `resource_closed`, `document_visible`, `document_hidden`, `window_focused`, `window_blurred`, `pageshow`, `pagehide`, `network_online`, `network_offline`, `activity_heartbeat`, `confidence_prompt_shown`, `confidence_committed`, `confidence_skipped`, `simulation_parameter_changed`, `simulation_run_started`, `simulation_run_completed`, `simulation_error_returned`, `legacy_visit_observed`, `legacy_draft_observed`, `legacy_submission_observed`, `legacy_session_start_observed`, and `legacy_session_end_observed`
- Create: `apps/assessment/event_schema_registry.py`
- Modify: `apps/assessment/test_event_schema_registry.py`

- [x] **Step 1: Add failing registry tests**

Tests must assert that:

```python
schema = get_event_schema("response_state_changed", 1)
self.assertFalse(schema["additionalProperties"])
self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
validate_event_payload(
    "response_state_changed",
    1,
    {"question_type": "single_choice", "response_state_after": "A", "change_source": "student"},
)
with self.assertRaises(EventSchemaNotFound):
    get_event_schema("unknown_event", 1)
with self.assertRaises(EventPayloadValidationError):
    validate_event_payload("activity_heartbeat", 1, {"mouse_x": 10})
self.assertRegex(event_schema_sha256("response_state_changed", 1), r"^[0-9a-f]{64}$")
```

The test also enumerates every approved event type and requires a v1 schema file.

- [x] **Step 2: Run tests and verify missing registry failures**

Run: `..\\..\\.venv\\Scripts\\python.exe manage.py test apps.assessment.test_event_schema_registry -v 2`
Expected: FAIL because the registry does not exist.

- [x] **Step 3: Implement a cached repository registry**

`event_schema_registry.py` must expose:

```python
class EventSchemaNotFound(LookupError): ...
class EventPayloadValidationError(ValueError): ...

def get_event_schema(event_type: str, event_version: int) -> dict: ...
def validate_event_payload(event_type: str, event_version: int, payload: dict) -> None: ...
def event_schema_sha256(event_type: str, event_version: int) -> str: ...
def registered_event_schemas() -> tuple[tuple[str, int], ...]: ...
```

Use `Draft202012Validator.check_schema`, `Draft202012Validator.iter_errors`, deterministic JSON serialization for hashes, `functools.lru_cache`, and `Path(__file__).with_name("event_schemas")`. Error messages identify the event type/version and the first invalid JSON path without echoing confidential payload values.

- [x] **Step 4: Add v1 payload schemas**

Every file declares Draft 2020-12, object type and `additionalProperties: false`. Empty-payload lifecycle events use no properties. Required structured payloads include:

```text
response_state_changed: question_type, response_state_after, change_source
response_commit_requested: submission_token
response_committed: submission_token, score, is_correct
response_commit_rejected: submission_token, reason_code
attempt_closed: close_reason
activity_heartbeat: attempt_id, document_visible, window_focused, last_device_interaction_age_ms
confidence_prompt_shown: confidence_assignment_id, scale_version, prompt_version
confidence_committed: confidence_assignment_id, probability
confidence_skipped: confidence_assignment_id, reason_code
simulation events: simulation_snapshot_id and the event-specific result/status fields
legacy_draft_observed: answer_hash only
```

- [x] **Step 5: Re-run registry tests**

Expected: PASS with every schema discovered and validated.

- [x] **Step 6: Commit**

```bash
git add apps/assessment/event_schemas apps/assessment/event_schema_registry.py apps/assessment/test_event_schema_registry.py
git commit -m "feat: add versioned assessment event schemas"
```

### Task 3: Add stable course and research-study subject identifiers

**Files:**
- Modify: `apps/accounts/models.py`
- Create: `apps/accounts/migrations/0006_research_subject_ids.py`
- Create: `apps/accounts/test_research_subjects.py`
- Modify: `apps/accounts/admin.py`

- [x] **Step 1: Write failing model and stability tests**

The tests create one student in two courses and two experiments in one course, then assert:

```python
course_a_first = CourseSubject.objects.get_or_create(course=course_a, student=student)[0]
course_a_second = CourseSubject.objects.get_or_create(course=course_a, student=student)[0]
course_b_subject = CourseSubject.objects.get_or_create(course=course_b, student=student)[0]
self.assertEqual(course_a_first.public_uuid, course_a_second.public_uuid)
self.assertNotEqual(course_a_first.public_uuid, course_b_subject.public_uuid)
self.assertNotEqual(str(course_a_first.public_uuid), str(student.pk))
```

They also assert that `ResearchStudyParticipant` is stable within a study, differs across studies, and `ResearchStudyCourse` prevents duplicate course links.

- [x] **Step 2: Run tests and verify missing model failures**

Run: `..\\..\\.venv\\Scripts\\python.exe manage.py test apps.accounts.test_research_subjects -v 2`.

- [x] **Step 3: Implement the models**

Use `UUIDField(default=uuid.uuid4, editable=False, unique=True)` and these constraints:

```python
UniqueConstraint(fields=["course", "student"], name="unique_course_subject")
UniqueConstraint(fields=["study", "course"], name="unique_research_study_course")
UniqueConstraint(fields=["study", "student"], name="unique_research_study_participant")
```

`ResearchStudy` stores UUID primary public identifier, name, description, owner, active flag and timestamps. Courses and users remain protected by `on_delete=PROTECT` where deletion would silently break longitudinal linkage.

- [x] **Step 4: Generate and inspect migration**

Run: `..\\..\\.venv\\Scripts\\python.exe manage.py makemigrations accounts --name research_subject_ids`
Then inspect that UUID defaults are callable and no data-dependent values are embedded.

- [x] **Step 5: Add idempotent CourseSubject backfill**

The migration creates one random `CourseSubject` for each existing distinct `(course_id, student_id)` in `CourseEnrollment`, `ExperimentStudent`, or `ExperimentSession` through a course experiment. It must use `bulk_create(..., ignore_conflicts=True)` and remain safe when rerun through migration tests.

- [x] **Step 6: Run model and migration tests**

Run: `..\\..\\.venv\\Scripts\\python.exe manage.py test apps.accounts.test_research_subjects apps.accounts.tests_course_migration -v 2`.

- [x] **Step 7: Commit**

```bash
git add apps/accounts
git commit -m "feat: add stable pseudonymized subject identifiers"
```

### Task 4: Add experiment presentation and Confidence configuration contracts

**Files:**
- Modify: `apps/experiments/models.py`
- Modify: `apps/experiments/forms.py`
- Modify: `apps/experiments/services.py`
- Create: `apps/experiments/migrations/0006_item_instance_confidence.py`
- Create: `apps/experiments/test_research_configuration.py`
- Modify: `apps/assessment/models.py`
- Modify: `apps/assessment/services.py`
- Create: `apps/assessment/migrations/0003_assignment_item_instance.py`
- Modify: `apps/assessment/tests.py`

- [x] **Step 1: Add failing validation and snapshot tests**

Cover defaults and boundaries:

```python
self.assertEqual(experiment.confidence_mode, "off")
self.assertEqual(experiment.heartbeat_interval_seconds, 30)
experiment.heartbeat_interval_seconds = 14
with self.assertRaises(ValidationError): experiment.full_clean()
experiment.confidence_probability = Decimal("1.01")
with self.assertRaises(ValidationError): experiment.full_clean()
```

Publishing must freeze all Confidence settings and heartbeat interval into `ExperimentVersion.configuration`.

- [x] **Step 2: Run tests and verify missing-field failures**

Run: `..\\..\\.venv\\Scripts\\python.exe manage.py test apps.experiments.test_research_configuration -v 2`.

- [x] **Step 3: Add validated Experiment fields**

Add choices for Confidence mode and sampling unit, decimal probability constrained to `[0, 1]`, randomization seed, assignment/scale/prompt versions, and heartbeat interval with `MinValueValidator(15)` / `MaxValueValidator(120)`. `clean()` requires probability only in sampled mode and clears no values implicitly.

- [x] **Step 4: Add ItemInstance**

The model uses UUID primary identity and fields from the approved design. Add a unique default presentation per `(question_snapshot, presentation_hash)` and deterministic `presentation_hash` generation in a service helper from canonical JSON containing option order, parameter values and variant.

- [x] **Step 5: Create default instances during session assignment**

Update `get_or_create_session` through a service API so every assignment references a default `ItemInstance`. Do not change assigned, visit or submission ordering semantics. A data migration creates default instances for all existing assignments and then makes the assignment FK non-null.

- [x] **Step 6: Freeze configuration on publish**

Update the existing publish configuration dictionary with the exact Confidence and heartbeat fields. Add regression assertions that later draft edits do not alter an existing `ExperimentVersion`.

- [x] **Step 7: Run experiment and assessment regression tests**

Run: `..\\..\\.venv\\Scripts\\python.exe manage.py test apps.experiments apps.assessment.tests apps.assessment.tests_course_navigation -v 1`.

- [x] **Step 8: Commit**

```bash
git add apps/experiments apps/assessment/services.py apps/assessment/models.py apps/assessment/migrations
git commit -m "feat: freeze presentation and confidence configuration"
```

### Task 5: Add process event, time-sync and state snapshot models

**Files:**
- Modify: `apps/assessment/models.py`
- Create: `apps/assessment/migrations/0004_process_data_contracts.py`
- Create: `apps/assessment/test_process_data_models.py`
- Modify: `apps/assessment/admin.py`

- [x] **Step 1: Write failing model contract tests**

Tests assert all approved fields, UUID identities, nullable linkage, choices, uniqueness and append-only behavior. They specifically prove:

```python
self.assertIsNone(event.estimated_occurred_at_utc)
with self.assertRaises(IntegrityError):
    AssessmentEvent.objects.create(
        session=session,
        client_instance_id=event.client_instance_id,
        client_sequence_no=event.client_sequence_no,
        ...
    )
```

They also assert ResponseSnapshot hash uniqueness per attempt, SimulationSnapshot protected blob metadata, ConfidenceAssignment status distinctions, and ClientTimeSync instance-scoped semantics.

- [x] **Step 2: Run tests and verify missing-model failures**

Run: `..\\..\\.venv\\Scripts\\python.exe manage.py test apps.assessment.test_process_data_models -v 2`.

- [x] **Step 3: Implement focused model classes**

Define `ClientTimeSync`, `ResponseSnapshot`, `SimulationSnapshot`, `ConfidenceAssignment` and `AssessmentEvent`. Use database checks for positive versions/sequences, valid probability/range values and the documented choices. Foreign keys to business state use `PROTECT` or nullable `SET_NULL` according to purge requirements; event links must never trigger accidental cascades from routine UI deletion.

- [x] **Step 4: Enforce append-only behavior through managers/services**

Default managers expose reads but model `save()` rejects updates after initial insert and `delete()` rejects direct deletion. Internal purge uses a narrowly scoped queryset/service not imported by normal views. Bulk update/delete protections are tested; Django admin registers events read-only with no add/change/delete permission.

- [x] **Step 5: Add portable indexes and PostgreSQL BRIN operation**

Create the four portable indexes in model metadata. Use `SeparateDatabaseAndState` plus vendor-gated `RunPython` to create the BRIN index only on PostgreSQL and no-op on SQLite. Migration reverse removes the PostgreSQL index.

- [x] **Step 6: Run migration and model tests on SQLite**

Run:

```text
..\\..\\.venv\\Scripts\\python.exe manage.py makemigrations --check --dry-run
..\\..\\.venv\\Scripts\\python.exe manage.py migrate
..\\..\\.venv\\Scripts\\python.exe manage.py test apps.assessment.test_process_data_models -v 2
```

- [x] **Step 7: Commit**

```bash
git add apps/assessment
git commit -m "feat: add immutable process data contracts"
```

### Task 6: Add the sole event append service

**Files:**
- Create: `apps/assessment/event_services.py`
- Create: `apps/assessment/test_event_services.py`

- [x] **Step 1: Write failing append-service tests**

Cover payload validation, per-session ingest allocation, client idempotency, nullable time estimation, quality flags and correlation IDs. Two identical client events must return the same persisted row; a conflicting payload at the same `(client_instance_id, client_sequence_no)` must raise a structured conflict without mutating the original.

- [x] **Step 2: Run tests and verify missing service failures**

Run: `..\\..\\.venv\\Scripts\\python.exe manage.py test apps.assessment.test_event_services -v 2`.

- [x] **Step 3: Implement `append_assessment_event`**

Signature:

```python
def append_assessment_event(
    *, event_type, event_version, event_domain, actor_source,
    session, payload, assignment=None, attempt=None,
    question_snapshot=None, item_instance=None,
    client_instance_id=None, client_sequence_no=None,
    correlation_id=None, client_wall_time_utc=None,
    client_monotonic_ms=None, client_time_origin=None,
    time_sync=None, capture_mode="native", quality_flags=(),
    app_version="",
) -> AssessmentEvent:
    ...
```

Validate schema before locking. Inside `transaction.atomic`, lock the session row, allocate `server_ingest_sequence_no = max + 1`, compute estimates only from a valid time-sync sample, persist once, and convert uniqueness races into idempotent replay or explicit conflict.

- [x] **Step 4: Add concurrency tests**

SQLite tests prove sequential allocation and idempotent retry. PostgreSQL-only `TransactionTestCase` uses two connections/threads to prove unique ingest allocation and no duplicate client sequence. Skip PostgreSQL test with an explicit vendor condition on SQLite.

- [x] **Step 5: Run service and PostgreSQL test modules**

Run SQLite suite locally; record the PostgreSQL command for Docker execution in Phase 5.

- [x] **Step 6: Commit**

```bash
git add apps/assessment/event_services.py apps/assessment/test_event_services.py apps/assessment/tests_postgresql_events.py
git commit -m "feat: centralize immutable event appends"
```

### Task 7: Phase 1 compatibility and release gate

**Files:**
- Modify: `docs/data-dictionary.md`
- Modify: `docs/superpowers/plans/2026-08-25-research-data-phase-1-contracts.md`

- [x] **Step 1: Document raw versus derived semantics**

Document every new table, primary/foreign key, null rule, clock limitation, append-only rule, pseudonym scope and event/schema version. Explicitly state that ingest order is not behavioral order and that pseudonymized data is not anonymous.

- [x] **Step 2: Run the phase gate**

```text
..\\..\\.venv\\Scripts\\python.exe manage.py check
..\\..\\.venv\\Scripts\\python.exe manage.py makemigrations --check --dry-run
..\\..\\.venv\\Scripts\\python.exe manage.py test --verbosity 1
node --check static/js/app.js
node --check static/js/assessment.js
git diff --check
```

Expected: 0 failures; existing skips must remain explained.

- [x] **Step 3: Check off completed plan steps and commit documentation**

```bash
git add docs requirements.txt apps
git commit -m "docs: record process data contracts"
```

- [x] **Step 4: Review phase boundary**

Do not start native browser collection until the Phase 1 model/API review confirms that no event payload, time field, pseudonym scope or migration requirement remains ambiguous.

- [x] **Step 5: Close strict review findings**

Prove direct event inserts are blocked, schema artifacts and snapshots are immutable, event links and time-sync samples are internally consistent, business submissions and process events commit atomically, and authoritative submission events are server-only. Enforce one `response_committed` per attempt at both the locked service boundary and database layer. Keep a PostgreSQL two-connection concurrency test for the row-lock behavior even when the local SQLite gate skips it explicitly.
