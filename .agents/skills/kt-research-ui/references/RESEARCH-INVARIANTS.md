# Research invariants

Read this file before changing UI code that can affect navigation, drafts, submission, timing, feedback, publishing, or export.

- Update visit records only after navigation successfully enters the requested question.
- `first_opened_at` is the server time of the first successful entry into a question.
- `last_opened_at` is the server time of the last successful entry before formal submission.
- Draft autosave must never create a formal submission.
- Raw behavioral evidence is append-only; draft saving, content reuse and conflict resolution cannot replace or delete transitions. Capture-v2 records each confirmed state, including empty states, before asynchronous transport.
- Sessions bound to `capture-v2-stable-input-v1` record text states after 1000 ms without input or at an explicit boundary, with one linked burst-summary input operation per transition. Continuous input has no periodic state snapshots. Empty states and returns to earlier content remain separate transitions. Existing `capture-v2` sessions keep every confirmed-transition semantics; an unopened prepared session is already bound and must not switch contracts.
- Rendering, first interaction, first nonempty answer, formal submission and feedback exposure are distinct observations. Initialization and restored state are not student actions.
- `submitted_at` is the server time at which the database transaction succeeds.
- Lock answers after formal submission.
- Allow only one effective formal submission for each question.
- Preserve assigned order, first-visit order, and submission order as distinct sequences.
- Do not encode skipped, unanswered, or timed-out attempts as incorrect automatically.
- In no-feedback mode, do not expose correctness, correct answers, or explanations.
- Later question-bank edits must not alter snapshots already published to an experiment.
- Preserve the identified export contract in docs/changes/2026-08-27-identified-data-contract.md; frozen export identity must not drift on rerun.
- UI changes must not alter CSRF, authorization, database transactions, idempotency tokens, or export semantics.

Any change to a file that implements one of these rules requires automated regression coverage.
