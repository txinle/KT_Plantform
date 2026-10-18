---
name: kt-research-ui
description: Use when auditing or redesigning KT research platform interfaces in the independently built Vue 3 frontend backed by Django/DRF, especially visual-reference matching, responsive states, and accessibility.
---

# KT Research UI

> **Research semantics have one Django backend authority; business pages use the independently built Vue frontend.**

## Design read

Treat this repository as an educational research data-collection product, not a marketing site. Make teacher workflows precise and information-dense, and keep student assessment screens calm and cognitively stable.

Defaults: `DESIGN_VARIANCE: 4`, `MOTION_INTENSITY: 2`, `VISUAL_DENSITY: 6`, `DATA_CLARITY: 10`, `ACCESSIBILITY_PRIORITY: 10`.

## Required workflow

1. Read [RESEARCH-INVARIANTS.md](references/RESEARCH-INVARIANTS.md) before changing UI that can affect publishing, assessment, timing, feedback, or export.
   On Windows, read skill and instruction files with an explicit UTF-8 encoding, for example `Get-Content -Encoding utf8`, rather than relying on the active code page.
2. Locate the existing business URL in `contracts/frontend-routes.json`, its Vue route/component under `client/`, the authoritative Service/DRF API under `server/`, and relevant tests. Check README and the local Node gateway/production Nginx mapping when routing is affected.
3. Inventory the authoritative Service/API contract, affected Vue entrypoint, stable runtime/DOM hooks, and the real user path. Django retains Session authentication, CSRF, APIs, downloads, and `/admin/`.
4. For a reference-led request, read [EXPERIMENT-REFERENCE.md](references/EXPERIMENT-REFERENCE.md) when it covers the requested state; otherwise use the user-supplied asset as the primary visual authority.
5. Audit desktop, mobile, zoom, empty, loading, success, and error states with [PAGE-AUDIT.md](references/PAGE-AUDIT.md).
6. Read [DESIGN-SYSTEM.md](references/DESIGN-SYSTEM.md), implement business UI in Vue, then verify with [ACCEPTANCE-CHECKLIST.md](references/ACCEPTANCE-CHECKLIST.md).

## Presentation boundaries

- Business pages belong to `client/`; Django business templates, legacy forms, and page-rendering feature flags are retired. Do not recreate them because an older plan describes progressive migration.
- `contracts/frontend-routes.json` registers business page URLs. The local gateway and production Nginx serve frontend HTML only for registered GET/HEAD requests; unknown pages/APIs must not receive it.
- Django keeps page reverse URL names without reading client build artifacts. Backend downloads and `/admin/` use document navigation.
- Preserve historical hashed assets, versioned assessment shells, worker URL/scope, IndexedDB, and unconfirmed materials during updates and rollback. These retained assets do not authorize a Django template fallback.

## Architecture and authority

- The current architecture is a Django 5.2 modular monolith with DRF in `server/` plus an independently built Vue 3 + TypeScript + Vite frontend in `client/`.
- Domain Services remain the sole authority for permissions, KC identity and mappings, experiment definitions, deterministic sampling, assignment generation, snapshots, deadlines, scoring, feedback policy, audits, Coverage, and research exports.
- Django API views, DRF serializers, Vue, publication services, assignment generators, and export modules must not each implement their own version of these rules. Views orchestrate; serializers project; Vue renders authorized responses and collects interaction.
- Treat the API response shape as the presentation contract. Permission-sensitive fields such as scores, correct answers, explanations, comparisons, and ranks must be omitted server-side when forbidden. Vue may test whether an authorized field exists; it must not infer whether the user should receive it.
- Add or refine an API contract when a Vue page lacks authorized data required by the UI. Reuse the underlying Service and frozen snapshot facts; do not encode domain logic in serializers or alter seeds, database state, or research semantics to resemble a screenshot.
- Preserve existing business URLs, session authorization, CSRF, and the explicit frontend page contract. Do not add a permanent `/app` route, a blanket SPA fallback, or template-rendering rollout branches.
- A visual-reference task never authorizes copying the reference's class names, counts, participants, question order, warnings, dates, or outcomes. Use real API data and existing domain behavior.
- Scope component CSS to the affected module. A feature stylesheet imported from Vue must not redefine shared `.kt-*` or Bootstrap classes outside its feature root.

## Visual rules

- Persisted repository references and user-supplied assets define visual state only, never business data. Match the skeleton first, then typography, controls, color, and spacing.
- Use restrained navy/blue for brand and action, cool neutral surfaces, and green/amber/red only for real server-projected semantic status.
- Avoid gradients, glass panels, decorative statistics, giant marketing headlines, emoji icons, excessive shadows, and pill badges everywhere.
- Preserve visible focus, true labels, useful error summaries, non-color status cues, WCAG AA contrast, and reduced-motion behavior.
- Keep primary touch targets at least 44 px on student/mobile surfaces. Use tabular figures for IDs, counts, dates, durations, and timers.

## Verification gate

Verify two layers separately:

1. **Research semantics:** exercise the relevant Service/API tests for permissions, transactions, frozen definitions/snapshots, KC mappings, deterministic sampling, grading, feedback omission, audit, and export invariants. UI success cannot substitute for this evidence.
2. **Presentation and routing:** verify the Vue page and its Service/API contract. For routing changes, check registered GET/HEAD page delivery, backend API/download/admin routing, and unknown-path rejection using the affected gateway tests. Do not require or recreate a template fallback.

For Vue work run affected unit/API tests, `npm run lint`, `npm run typecheck`, `npm run test:unit`, `npm run api:check`, and `npm run build`. Run broader regression only when repository instructions or change scope require it.

For the six experiment states, capture 1586x992 at DPR 1 and compare against the named reference. For other supplied references, use their native viewport. Also check 1440x900, 390x844, keyboard operation, 200% zoom, WCAG AA contrast, no text overflow, and `prefers-reduced-motion`.
