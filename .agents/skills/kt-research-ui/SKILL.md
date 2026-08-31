---
name: kt-research-ui
description: Use when auditing, redesigning, or migrating KT research platform interfaces across legacy Django and module-scoped Vue 3 presentation paths, especially visual-reference matching, responsive states, and accessibility.
---

# KT Research UI

> **Research semantics have one backend authority; presentation migrates to Vue module by module; completed modules are not maintained twice.**

## Design read

Treat this repository as an educational research data-collection product, not a marketing site. Make teacher workflows precise and information-dense, and keep student assessment screens calm and cognitively stable.

Defaults: `DESIGN_VARIANCE: 4`, `MOTION_INTENSITY: 2`, `VISUAL_DENSITY: 6`, `DATA_CLARITY: 10`, `ACCESSIBILITY_PRIORITY: 10`.

## Required workflow

1. Read [RESEARCH-INVARIANTS.md](references/RESEARCH-INVARIANTS.md) before changing UI that can affect publishing, assessment, timing, feedback, or export.
   On Windows, read skill and instruction files with an explicit UTF-8 encoding, for example `Get-Content -Encoding utf8`, rather than relying on the active code page.
2. Determine the module migration state from repository evidence before choosing an implementation target. Inspect the module specification or plan, existing business URL, server `is_vue_module_enabled`/`render_vue_shell` branch, DRF API, Vue route/component, tests, and any retained template. A `.vue` file alone is not evidence that migration is complete.
3. Inventory the authoritative Service/API contract, rollout controls, affected Vue or legacy entrypoint, DOM/form hooks that must remain stable, and the real user path.
4. For a reference-led request, read [EXPERIMENT-REFERENCE.md](references/EXPERIMENT-REFERENCE.md) when it covers the requested state; otherwise use the user-supplied asset as the primary visual authority.
5. Audit desktop, mobile, zoom, empty, loading, success, and error states with [PAGE-AUDIT.md](references/PAGE-AUDIT.md).
6. Read [DESIGN-SYSTEM.md](references/DESIGN-SYSTEM.md), implement only in the authoritative presentation path for the migration state, then verify with [ACCEPTANCE-CHECKLIST.md](references/ACCEPTANCE-CHECKLIST.md).

## Migration states

| State | Repository evidence | UI implementation target |
| --- | --- | --- |
| Legacy Django | No approved Vue vertical slice, matching Vue route, or module rollout branch/API | Maintain the Django template, Bootstrap/native CSS, and native JavaScript path. |
| Vue migration in progress | An approved migration exists but its same-URL route, API, Vue states, or rollout/fallback coverage is incomplete | Treat the UI request as an opportunity to complete the scoped Vue vertical slice. Keep the old template only as a safe fallback. |
| Vue primary + Django fallback | The same business URL selects a Vue shell under a server feature flag and a retained template remains when the flag is off | Implement new UI, layout, and interaction only in Vue. Give the template only the minimum compatibility needed for a safe kill-switch fallback; do not synchronize new presentation work. |
| Vue migration complete | Exit criteria explicitly establish Vue as the only supported presentation path and the legacy path is retired | Maintain Vue only. Do not recreate or preserve a second template implementation. |

If repository evidence still conflicts after inspecting the sources above, stop and ask which migration state is intended. Do not silently choose a path that creates long-term dual maintenance.

## Architecture and authority

- The approved architecture is a Django 5.2 modular monolith with DRF plus module-scoped Vue 3 + TypeScript + Vite vertical slices.
- Domain Services remain the sole authority for permissions, KC identity and mappings, experiment definitions, deterministic sampling, assignment generation, snapshots, deadlines, scoring, feedback policy, audits, Coverage, and research exports.
- Django template views, DRF serializers, Vue, publication services, assignment generators, and export modules must not each implement their own version of these rules. Views orchestrate; serializers project; Vue renders authorized responses and collects interaction.
- Treat the API response shape as the presentation contract. Permission-sensitive fields such as scores, correct answers, explanations, comparisons, and ranks must be omitted server-side when forbidden. Vue may test whether an authorized field exists; it must not infer whether the user should receive it.
- Add or refine an API contract when a Vue vertical slice genuinely lacks authorized data required by the UI. Reuse the underlying Service and frozen snapshot facts; do not encode domain logic in serializers or alter seeds, database state, or research semantics to resemble a screenshot.
- Preserve existing business URLs, session authorization, CSRF, server-side module kill switches, Course/User allowlists, and required fallback behavior during migration. Do not add a permanent `/app` route or Nginx SPA fallback.
- A visual-reference task never authorizes copying the reference's class names, counts, participants, question order, warnings, dates, or outcomes. Use real API data and existing domain behavior.
- Scope component CSS to the migrated module. A feature stylesheet imported from Vue must not redefine shared `.kt-*` or Bootstrap classes outside its feature root.

## Visual rules

- Persisted repository references and user-supplied assets define visual state only, never business data. Match the skeleton first, then typography, controls, color, and spacing.
- Use restrained navy/blue for brand and action, cool neutral surfaces, and green/amber/red only for real server-projected semantic status.
- Avoid gradients, glass panels, decorative statistics, giant marketing headlines, emoji icons, excessive shadows, and pill badges everywhere.
- Preserve visible focus, true labels, useful error summaries, non-color status cues, WCAG AA contrast, and reduced-motion behavior.
- Keep primary touch targets at least 44 px on student/mobile surfaces. Use tabular figures for IDs, counts, dates, durations, and timers.

## Verification gate

Verify two layers separately:

1. **Research semantics:** exercise the relevant Service/API tests for permissions, transactions, frozen definitions/snapshots, KC mappings, deterministic sampling, grading, feedback omission, audit, and export invariants. UI success cannot substitute for this evidence.
2. **Presentation and rollout:** for Vue-primary modules, verify the feature flag ON path as the formal implementation and the flag OFF template only as a safe functional fallback. Do not require visual or interaction parity from the fallback. For legacy-only modules, run the narrow Django presentation and behavior checks.

For migrated Vue work run affected unit/API tests, `npm run lint`, `npm run typecheck`, `npm run test:unit`, `npm run api:check`, and `npm run build`. Run broader regression only when repository instructions or change scope require it.

For the six experiment states, capture 1586x992 at DPR 1 and compare against the named reference. For other supplied references, use their native viewport. Also check 1440x900, 390x844, keyboard operation, 200% zoom, WCAG AA contrast, no text overflow, and `prefers-reduced-motion`.
