# Research invariants

Read this file before changing UI code that can affect navigation, drafts, submission, timing, feedback, publishing, or export.

- Update visit records only after navigation successfully enters the requested question.
- `first_opened_at` is the server time of the first successful entry into a question.
- `last_opened_at` is the server time of the last successful entry before formal submission.
- Draft autosave must never create a formal submission.
- `submitted_at` is the server time at which the database transaction succeeds.
- Lock answers after formal submission.
- Allow only one effective formal submission for each question.
- Preserve assigned order, first-visit order, and submission order as distinct sequences.
- Do not encode skipped, unanswered, or timed-out attempts as incorrect automatically.
- In no-feedback mode, do not expose correctness, correct answers, or explanations.
- Later question-bank edits must not alter snapshots already published to an experiment.
- Do not include student number, name, or class in the anonymous KT export by default.
- UI changes must not alter CSRF, authorization, database transactions, idempotency tokens, or export semantics.

Any change to a file that implements one of these rules requires automated regression coverage.
