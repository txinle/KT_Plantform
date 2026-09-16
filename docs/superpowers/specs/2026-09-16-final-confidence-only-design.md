# Final Confidence Only Design

## Goal

Only the confidence value confirmed with the final answer submission is treated as the student's confidence result. Slider and number-input movement must not create confidence-value events.

## Design

- The student UI keeps confidence edits in local component state.
- `confidence_observed` remains available for prompt presentation and explicit skipping, but the client no longer emits `stage=responded` for each valid input change.
- The atomic answer submission remains authoritative. Its existing backend `commit_confidence` path stores the selected probability and appends one `confidence_committed` event.
- The teacher research-process projection excludes historical `confidence_observed` events whose stage is `responded`, while retaining prompt presentation, skipping, and the authoritative committed value.
- The research materialization projection version is incremented so completed sessions are rebuilt with the corrected projection.

## Verification

- A component regression test moves through several valid confidence values and proves no responded event is emitted while the final API payload contains only the last value.
- An analytics regression test proves historical responded observations are excluded while the final committed value remains.
- Targeted frontend and backend tests, frontend type checking, and the production build must pass before deployment.
