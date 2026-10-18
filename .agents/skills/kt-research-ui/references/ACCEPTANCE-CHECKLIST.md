# Acceptance checklist

## Research semantics

- [ ] Domain Services remain authoritative for permissions, transactions, frozen definitions/snapshots, KC mappings, deterministic sampling, assignment generation, grading, feedback, audit, Coverage, and exports.
- [ ] Permission-sensitive API fields are omitted server-side when forbidden; Vue does not reconstruct or infer them.
- [ ] Existing URLs, session authorization, CSRF, idempotency, schedules, randomization, feedback, and export semantics remain stable.
- [ ] Reference content was not copied into source, API projections, seed data, or database records; only visual style and structure were reproduced.
- [ ] Relevant Service/API regression tests pass.

## UI and routing

- [ ] The business URL, page-contract entry, Vue route/component, and Service/API contract were identified before implementation.
- [ ] Business UI was implemented in the independent Vue frontend.
- [ ] For routing changes, only registered GET/HEAD page routes receive frontend HTML. APIs, authentication writes, downloads, and `/admin/` remain backend routes; unknown paths do not receive frontend HTML.
- [ ] No Django business template, legacy form entrypoint, or page-rendering feature-flag fallback was recreated.
- [ ] Feature styles are scoped and do not alter other modules.
- [ ] Empty, loading, saving, success, conflict, and error states still work with real data.
- [ ] Keyboard order, visible focus, labels/ARIA, and non-color status cues are present.
- [ ] Text and controls meet WCAG AA; reduced-motion preferences are honored.
- [ ] Reference-native captures have no text overflow, unexpected scrollbar, or clipping; key containers/controls are within 4 px and font/control sizing within 2 px where real content permits comparison.
- [ ] 1440x900, 390x844, and 200% zoom reflow without page-level horizontal overflow or lost actions.
- [ ] Relevant Vue tests, API schema checks, lint, typecheck, unit tests, and build are fresh for affected work.
- [ ] The actual diff is scoped; unrelated user changes remain untouched.
