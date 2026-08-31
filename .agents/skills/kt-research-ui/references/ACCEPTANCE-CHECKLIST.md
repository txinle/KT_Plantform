# Acceptance checklist

## Research semantics

- [ ] Domain Services remain authoritative for permissions, transactions, frozen definitions/snapshots, KC mappings, deterministic sampling, assignment generation, grading, feedback, audit, Coverage, and exports.
- [ ] Permission-sensitive API fields are omitted server-side when forbidden; Vue does not reconstruct or infer them.
- [ ] Existing URLs, session authorization, CSRF, idempotency, schedules, randomization, feedback, and export semantics remain stable.
- [ ] Reference content was not copied into source, API projections, seed data, or database records; only visual style and structure were reproduced.
- [ ] Relevant Service/API regression tests pass.

## UI and rollout

- [ ] The migration state and its repository evidence were recorded before implementation.
- [ ] New UI was implemented only in the authoritative presentation path for that state.
- [ ] For Vue primary + Django fallback, feature flag ON serves the complete Vue implementation and flag OFF keeps the old template functionally safe without requiring new visual parity.
- [ ] For Vue-complete modules, no legacy UI implementation was recreated; for legacy modules, no unapproved partial Vue path was introduced.
- [ ] Feature styles are scoped and do not alter other modules or fallback templates.
- [ ] Empty, loading, saving, success, conflict, and error states still work with real data.
- [ ] Keyboard order, visible focus, labels/ARIA, and non-color status cues are present.
- [ ] Text and controls meet WCAG AA; reduced-motion preferences are honored.
- [ ] Reference-native captures have no text overflow, unexpected scrollbar, or clipping; key containers/controls are within 4 px and font/control sizing within 2 px where real content permits comparison.
- [ ] 1440x900, 390x844, and 200% zoom reflow without page-level horizontal overflow or lost actions.
- [ ] Relevant Vue tests, API schema checks, lint, typecheck, unit tests, and build are fresh for migrated work.
- [ ] The actual diff is scoped; unrelated user changes remain untouched.
