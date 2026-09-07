# Capture-to-export coverage audit

## Scope and conclusion

This audit follows only platform facts that are actually captured, persisted, and
exported by the capture-v2 assessment flow. It does not treat the absence of an
event as non-use, a zero, a skipped action, or evidence about cognition.

`complete` is the only all-function archival package. `process` is a broad
behavioural package, while `kt`, `cd`, `confidence`, `simulation`,
`longitudinal`, and `teaching` are intentional analysis subsets. They must not
be described as interchangeable full-platform archives. The local `kt_platform`
database currently has zero sessions, so these findings establish code and
fixture behaviour, not a real participant-behaviour sample.

| Platform fact | Capture and persistence | Export coverage and scientific boundary |
| --- | --- | --- |
| Session, task, attempt, and authorized timing | Server-side sessions, assignments, attempts, deadlines, terminal reason, and immutable participant/release snapshots. | `sessions`, `assignments`, `item_instances`, and `attempts` are in complete/process. Missing client events do not establish that an item was never opened or answered. |
| Response state for all question types | capture-v2 `response_transition` stores the parent event, before/after canonical states, hashes, operation ID, and materialized snapshots. The implemented set is six types: single choice, multiple choice, true/false, short answer, fill blank, and numeric. | Raw `assessment_events`, curated `response_events`, `response_snapshots`, `response_trajectory`, and `response_replay` are in complete/process. They replay persisted states only; they cannot recreate an unsent local state. |
| Text editing and IME | `input_operation` records input type, composition phase, selection range, branch instance, parent, and operation ID. IME start/update/confirm/cancel are represented without storing an inferred psychological state. | `input_operation` is now in curated `response_events` and in authoritative `assessment_events`; `response_input_observations` is explicitly a legacy-protocol projection. |
| Text-operation completeness | Coverage v3 matches each formal-boundary text `response_transition` to a valid, unique, same-attempt `input_operation` ID. Multiple IME notifications may share one operation ID; a cancelled composition without a transition is not missing. | A missing, invalid, foreign-attempt, or reused transition operation ID marks the attempt partial with an explicit reason. This prevents a 3-transition/1-operation record from being labelled complete. |
| Offline, time, and capture failure | Client sequence numbers, wall/monotonic times, time syncs, IndexedDB outbox checkpoints, pending/failed counts, missing ranges, and quality flags are retained when reported to the server. | `client_time_syncs`, `client_capture_checkpoints`, raw envelopes, and coverage outputs are in complete/process. Sequence gaps, tail gaps/unknown tails, dead letters, and non-persistent outbox are evidence of incompleteness, not evidence of no action. |
| Hint, check, doubt, and research-tool actions | Client requests, server-authorized tool actions, and client observations carry tool/stage, answer reference, initiation, and frozen content version. | Raw events plus `exposure_events` and frozen `research_tools` configuration are in complete/process. A recorded presentation is not evidence of attention, reading, comprehension, or effect. |
| Feedback exposure | Server policy controls fields; `feedback_exposure` records the authorized stage and referenced answer/snapshot. | Raw/exposure projections are in complete/process. No exposure record cannot be converted into a claim that feedback was not seen. |
| Confidence | Raw confidence/tool observations and structured `ConfidenceAssignment` retain condition, response status, probability, and timestamps. | `confidence_assignments` is in complete and the confidence package; process keeps raw observations but is not a complete structured confidence archive. A self-report is not an ability estimate. |
| KC and item content | Frozen question content and KC mapping snapshots, mapping hashes, and release fingerprints persist with the item. | Complete includes KC dictionaries/mappings/Q matrices. Process retains frozen item mapping/content but not the complete KC ontology. `kc_visible` means availability, not attention or learning. |
| Formal freeze, submission, and grades | Active/final response pointers, formal capture boundary, final snapshots, submission events, and immutable grade history are server-controlled. | Complete/process export `submissions`, `grade_records`, response replay, and attempts. Grade history includes grader/version and voiding provenance. |
| Coverage provenance | Coverage run definition, parameter/hash, algorithm/code version, source cutoff/high-water marks, and contract hashes are persisted. | Both complete/process export `recording_coverage_runs`, sessions, and attempts, allowing a reported complete/partial result to be traced to its source window. |
| Branches and draft pointer conflicts | Parent references, state hashes, server pointers, formal boundary, and explicit resolution form the persisted response DAG. `branch_id` is a per-trajectory opaque client instance token. | The field dictionary now states that `branch_id` is not a cross-instance logical branch key and that the parent DAG plus server pointers are authoritative. A new server-only `response_pointer_conflict_observed` event records each returned draft-save 409 without an answer body. |

## Verified fixes

### Text operation linkage and curated projection

`apps/analytics/recording_coverage.py` now declares coverage-v3 and checks
`each_text_transition_operation_id`. Its explicit partial reasons are
`missing_input_operation_for_response_transition` and
`reused_input_operation_for_response_transition`. The corresponding source
tests cover 3:1, 3:3, repeated operation IDs, repeated IME composition events,
cross-attempt non-matching, and cancelled composition with no state transition.

`apps/analytics/export_catalog.py` includes `input_operation` in
`RESPONSE_EVENT_TYPES` and documents that capture-v2 operations belong in
`response_events` or the complete raw event table. It also documents
`branch_id` as opaque and identifies `parent_event_id`, state hashes, and the
server-selected pointers as the authoritative DAG.

The export audit ran:

```text
POSTGRES_DB=kt_e2e .venv\Scripts\python.exe manage.py test \
  apps.analytics.test_capture_v2_coverage.CaptureV2TextInputCoverageTests \
  apps.analytics.test_export_lineage_v2.ExportLineageV2Tests.test_client_time_sync_export_distinguishes_zero_from_missing_measurements \
  apps.analytics.test_export_lineage_v2.ExportLineageV2Tests.test_complete_export_freezes_question_content_and_capture_v2_replay \
  --keepdb --noinput
```

It passed 9 tests in 2.481 seconds. The lineage regression also confirms that
time values `0.000`, `0`, and `0` remain values in export, whereas
`None` exports as empty; the two must not be conflated.

### Server-observed 409 conflict event

`save_draft_api` now catches `ResponseVersionConflict` only after the failed
draft transaction has rolled back, then appends the server-controlled
`response_pointer_conflict_observed` event. Its payload contains only candidate
and current transition references, requested/current response versions, and the
request client instance. The event envelope supplies the server time and
attempt/session association; it does not change the answer, active head, or
formal state.

The local targeted PostgreSQL regression passed three cases: the conflict
preserves the active pointer while writing the observation; repeated 409
responses create distinct observations; and an unauthorized request writes no
event. The export audit also generated complete and process archives and
confirmed that this event appears in both raw `assessment_events` and curated
`response_events`, with no candidate/current/final/draft answer fields.

Each row means one observed server response, not a de-duplicated count of
underlying conflicts. Pre-deployment data cannot identify historical 409s from
the response DAG alone: the same graph can result from offline, abandoned, or
otherwise unsaved local work.

## Second audit: collector shutdown and grading eligibility

The second audit reproduced three transport races with failing Node tests:
bounded flushing could finish before queued event materialization; a background
flush could finish while a newly queued feedback event remained unsent; and the
final ACK checkpoint did not inherit the upload's keepalive setting. Bounded
flushing now waits for the existing record queue and an in-flight flush before
draining newly persisted events. ACK checkpoints inherit keepalive. Attempt
closure awaits bounded flushing, and Vue flushes newly recorded feedback before
navigation. Runtime disposal also stops the collector's synchronization and
upload timers. No event schema, answer state, IndexedDB version, or formal
submission semantics were changed.

The collector's 28 Node tests and the two affected Vue runtime files' 21 tests
passed. A fresh browser run also completed all five response types, composition
commit, offline refresh/reconnect, submission and feedback exposure. Its 157 raw
events, 28 response transitions, 21 snapshots and five formal submissions passed
the independently packaged replay script and frozen CSV rerun hash comparison.

The final question's checkpoint now confirms all 22 emitted events, with zero
pending events. An earlier, abruptly abandoned client still has a stale resume
checkpoint (emitted 3, ACK 0, pending 3), although the exported raw table contains
that client's continuous sequences 1–3. The conservative session-level tail
flag remains; it propagates to attempt replay eligibility. Offline-recovered
events also retain unsynchronized time flags. Neither fact is hidden by a
successful response-state replay. Old snapshots and their quality findings
remain frozen.

This audit also separated pending grading from submission contradictions in
outcome eligibility: a pending grade is unavailable for scored-outcome analysis,
with `grading_pending` and `effective_grade_unavailable`, not an invented
`submission_chain_mismatch`. Contradictory authoritative facts remain invalid
and unusable even when a completed grade exists.

## Remaining interpretation limits

1. An event that never reaches the server and a checkpoint that is never
   reported cannot be reconstructed from any package. Coverage must remain
   partial/unknown where its observed gaps require it.
2. A package subset is a design choice, not proof that omitted platform facts
   were absent. Analyses must name the package and its included tables.
3. Recorded actions, rendered hints/feedback, visible KC labels, response
   revisions, and confidence reports are behavioural or self-report facts.
   They do not identify attention, learning, comprehension, ability, or other
   psychological constructs without a separate valid design.
