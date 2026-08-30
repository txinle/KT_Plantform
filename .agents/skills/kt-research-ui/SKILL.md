---
name: kt-research-ui
description: Use when auditing, redesigning, or iterating the KT research platform UI across progressive Vue 3 pages and retained Django templates, especially teacher experiments, question banks, classes, data views, student assessment screens, responsive states, visual-reference matching, and accessibility.
---

# KT Research UI

## Design read

Treat this repository as an educational research data-collection product, not a marketing site. Make teacher workflows precise and information-dense, and keep student assessment screens calm and cognitively stable.

Defaults: `DESIGN_VARIANCE: 4`, `MOTION_INTENSITY: 2`, `VISUAL_DENSITY: 6`, `DATA_CLARITY: 10`, `ACCESSIBILITY_PRIORITY: 10`.

## Required workflow

1. Read [RESEARCH-INVARIANTS.md](references/RESEARCH-INVARIANTS.md) before changing UI that can affect publishing, assessment, timing, feedback, or export.
2. Inventory the affected URLs, Vue routes/components or retained Django templates, server projections, forms, DOM hooks, and user paths.
3. For a reference-led request, read [EXPERIMENT-REFERENCE.md](references/EXPERIMENT-REFERENCE.md) and use the named asset as the primary visual authority.
4. Audit desktop, mobile, zoom, empty, loading, success, and error states with [PAGE-AUDIT.md](references/PAGE-AUDIT.md).
5. Read [DESIGN-SYSTEM.md](references/DESIGN-SYSTEM.md), then implement the smallest scoped change.
6. Verify the real flow and complete [ACCEPTANCE-CHECKLIST.md](references/ACCEPTANCE-CHECKLIST.md).

## Architecture and authority

- The approved architecture is Django 5.2 + DRF with progressive Vue 3 + TypeScript + Vite. Retained pages may still use Django templates, Bootstrap, native CSS, and native JavaScript.
- Django owns permissions, task state, deadlines, scoring, Coverage, randomization, feedback visibility, snapshots, schedules, and publication semantics. Vue renders authoritative responses and user interaction; it must not infer these fields.
- Preserve existing business URLs and server-side rollout controls. Do not add a permanent SPA fallback or unrelated route.
- Preserve CSRF, payloads, idempotency, form/DOM/Fetch contracts, assessment runtime, exports, and retained templates unless the scoped migration explicitly covers them.
- A visual-reference task never authorizes copying the reference's class names, counts, participants, question order, warnings, dates, or outcomes. Use real API data and existing domain behavior.
- Do not change APIs, seed data, database state, or domain services merely to make a screenshot resemble reference content.
- Scope component CSS to the migrated module. A feature stylesheet imported from Vue must not redefine shared `.kt-*` or Bootstrap classes outside its feature root.
- Do not add dependencies unless the existing stack cannot reasonably implement the requirement. Use local fonts and SVG icons.

## Visual rules

- For the six experiment states, the persisted 1586×992 reference images outrank generic taste rules. Match the skeleton first, then typography, controls, color, and spacing.
- Use restrained navy/blue for brand and action, cool neutral surfaces, and green/amber/red only for real semantic status.
- Avoid gradients, glass panels, decorative statistics, giant marketing headlines, emoji icons, excessive shadows, and pill badges everywhere.
- Preserve visible focus, true labels, useful error summaries, non-color status cues, WCAG AA contrast, and reduced-motion behavior.
- Keep primary touch targets at least 44 px on student/mobile surfaces. Use tabular figures for IDs, counts, dates, durations, and timers.

## Verification gate

For migrated Vue work run the affected unit/API tests, `npm run lint`, `npm run typecheck`, `npm run test:unit`, `npm run api:check`, and `npm run build`. For retained Django work run the narrow relevant Django tests and checks. Run broader regression only when repository instructions or change scope require it.

For the six experiment states, capture 1586×992 at DPR 1 and compare against the named reference. Also check 1440×900, 390×844, keyboard operation, 200% zoom, WCAG AA contrast, no text overflow, and `prefers-reduced-motion`.
