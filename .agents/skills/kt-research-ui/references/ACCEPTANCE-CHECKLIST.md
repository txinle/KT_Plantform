# Acceptance checklist

- [ ] Existing URLs, payloads, CSRF, permissions, idempotency, snapshots, schedules, randomization, feedback, and export semantics remain server-authoritative.
- [ ] Reference content was not copied into source, API projections, seed data, or database records; only visual style and structure were reproduced.
- [ ] Feature styles are scoped and do not alter other Vue modules or retained Django pages.
- [ ] All affected empty, loading, saving, success, conflict, and error states still work with real data.
- [ ] Keyboard order, visible focus, labels/ARIA, and non-color status cues are present.
- [ ] Text and controls meet WCAG AA; reduced-motion preferences are honored.
- [ ] 1586×992 DPR 1 captures exist for the six experiment states with no text overflow, unexpected scrollbar, or clipping.
- [ ] Key containers/controls are within 4 px and font/control sizing within 2 px where the real content permits direct comparison.
- [ ] 1440×900, 390×844, and 200% zoom reflow without page-level horizontal overflow or lost actions.
- [ ] Relevant Vue tests and Django/API checks pass; lint, typecheck, unit, API schema check, and build are fresh for migrated UI work.
- [ ] The actual diff is scoped; unrelated user changes remain untouched.
