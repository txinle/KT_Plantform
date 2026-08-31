# Page audit

Record each finding with page/state, severity (`P0`, `P1`, or `P2`), user impact, proposed repair, affected files, and research-semantic risk.

Before visual inspection, record:

1. The module migration state: legacy Django, Vue migration in progress, Vue primary + Django fallback, or Vue migration complete.
2. The repository evidence for that state: module specification/plan, business URL, rollout branch, DRF API, Vue route/component, and fallback presence.
3. The authoritative Service/API fields and the presentation path that may be changed. Do not treat the fallback as a second visual target for a Vue-primary module.

For reference-led work, audit in this order:

1. Skeleton: shell height, content width, breadcrumb, title, tabs, columns, panels, table and action placement.
2. Typography: font family, size, weight, line-height, wrapping, numeric alignment, and any overflow.
3. Controls: height, padding, border, radius, icons, focus, disabled/loading states, and action hierarchy.
4. Color and elevation: brand/status colors, borders, surfaces, shadows, and WCAG AA contrast.
5. Spacing: gutters, section gaps, card padding, row density, and alignment.

Also check:

- Real Service/API content remains authoritative; no screenshot data was copied into code, fixtures, seeds, or APIs.
- Permission-sensitive fields are removed by the server rather than hidden after Vue derives policy.
- Feature CSS stays inside the formal module path and does not leak into other Vue modules or the kill-switch fallback.
- Empty, loading, saving, success, conflict, and server-error states remain understandable.
- The reference's native viewport plus 1440x900, 390x844, keyboard, 200% zoom, reduced motion, and page-level overflow.
- Destructive-action hierarchy, confirmation, idempotency, and recovery.
- Student assessment cognitive load and research invariants.

Prioritize broken behavior and research-risk ambiguity first, then migration-boundary violations, skeleton deviations, and fine pixel calibration.
