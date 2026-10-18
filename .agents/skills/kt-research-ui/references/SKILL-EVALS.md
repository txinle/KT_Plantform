# Skill trigger evaluations

## Should trigger

- "Reproduce the supplied reference at 1:1 fidelity in the existing independent Vue page."
- "Redesign this Vue page using the existing Service/API contract and registered business URL."
- "Audit whether this business URL is correctly registered in the independent frontend page contract."
- "Improve the mobile and accessibility behavior of the student question page."
- "Unify the form, status, and visual hierarchy of the experiment-management page."

## Should not trigger or should refuse scope expansion

- "Change seed_demo to three classes and 120 students so the data matches the screenshot."
- "Let the frontend decide whether a class can publish, feedback is open, or randomization passed."
- "Recreate a Django business template and feature-flag fallback for this Vue page."
- "Fix numeric-question grading."
- "Change PostgreSQL indexes."
- "Add a new KT model."
- "Rewrite randomization, grading, or data-export algorithms."

## Expected architecture decisions

- Business UI changes target the Vue route/component under `client/`; authorized data comes from Django Services/DRF under `server/`.
- Routing changes preserve business URLs and synchronize `contracts/frontend-routes.json` with gateway mappings; only registered GET/HEAD page requests receive frontend HTML.
- APIs, authentication writes, downloads, and `/admin/` remain backend routes. Unknown pages and APIs do not receive frontend HTML.
- Retired business templates, legacy forms, and page-rendering feature flags are not restored based on an older migration plan.
- Historical hashed assets, versioned assessment shells, and unconfirmed browser materials remain preserved during upgrades and rollback.

## Expected authority behavior

- Select the correct persisted or user-supplied visual reference for the page state.
- Treat reference imagery as visual authority only, never as a data specification.
- Preserve Service authority and server-trimmed API contracts.
- Add authorized API projection fields when required by a Vue page, without duplicating domain rules in serializers or Vue.
- Scope Vue feature styles and test structure/behavior before pixel calibration.
