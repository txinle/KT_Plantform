# KT Research UI audit

Audit date: 2026-08-04

Baseline commands and runtime:

- `python manage.py check`: passed.
- `python manage.py test`: 15 tests discovered; 14 passed and 1 errored because the assessment test reused a stale pre-publication `Experiment` instance after publishing.
- Local server: `http://127.0.0.1:8000/` with the documented teacher and student demo accounts.
- Rendered checks: 1440 x 900 and 390 x 844.

## Page inventory and key paths

- Login: `/login/`
- Teacher: dashboard, question banks, bank detail, question edit, question/KC imports, KC library, classes, students, experiments, experiment detail, results/exports.
- Student: experiment list, start/resume, single-question assessment, finish, completion.
- Admin: `/admin/`

Teacher path: login -> bank list -> bank detail -> question edit/import -> experiment list -> configuration -> student assignment -> publish -> results/export.

Student path: login -> experiment list -> start/resume -> visit question -> autosave -> navigate -> submit/lock -> skip -> finish.

## Findings

| Page | Severity | Current issue | User impact | Planned repair | Files | Research risk |
|---|---|---|---|---|---|---|
| All pages | P0 | Bootstrap is loaded only from a CDN. When the request fails, containers, navigation, forms, tables, and the question layout collapse. The mobile question baseline became nearly unusable. | Local/offline data collection cannot be operated reliably. | Vendor Bootstrap 5.3 runtime and license; load local assets. | `templates/base.html`, `static/vendor/bootstrap/*` | Low, but operationally critical. |
| Student question | P0 | Submission, skip, navigation-save failure, timeout, and experiment finish rely on native `confirm()` or `alert()`. | Inconsistent focus behavior, inaccessible errors, and blocked recovery; mobile dialogs interrupt the task. | Add an accessible Bootstrap confirmation dialog, inline error region, loading state, and focus recovery while preserving Fetch contracts. | `templates/student/question.html`, `static/js/assessment.js` | High. Draft-before-navigation, idempotency, and lock semantics must remain unchanged. |
| Student question | P1 | Question status legend relies mainly on color; current question lacks `aria-current`; save state lacks an ARIA live region. | Keyboard and screen-reader users cannot reliably distinguish state or saving outcome. | Pair every state with text/symbol, add `aria-current="step"`, an accessible legend, and `aria-live="polite"`. | `templates/student/question.html`, `static/css/app.css` | Medium. Do not create extra visit events. |
| All authenticated pages | P1 | Navigation has no active-page state, weak mobile behavior, no skip link, and minimal landmark/ARIA structure. | Orientation and keyboard traversal are inefficient. | Add semantic header/nav/main, skip link, active nav state, collapsed mobile navigation, and alert live regions. | `templates/base.html`, `static/css/app.css`, `static/js/app.js` | Low. Logout remains POST with CSRF. |
| Teacher forms | P1 | Several forms use `form.as_div` or an undifferentiated field loop. Errors lack a linked page summary and fields are not grouped by business meaning. | Teachers miss validation errors and configuration dependencies. | Add reusable field and error-summary components; group question and experiment fields; preserve field names and validation. | teacher form templates, `templates/components/*` | Low. No backend validation changes. |
| Experiment detail | P1 | Synchronize and publish actions appear beside ordinary navigation; publish uses native confirmation and has no readiness checklist. | Accidental irreversible publication and unclear lifecycle. | Add lifecycle status, pre-publication checklist, differentiated actions, and accessible confirmation dialog. | `templates/teacher/experiment_detail.html`, `static/js/app.js` | High. Publication snapshot behavior must stay unchanged. |
| Results/export | P1 | Anonymous KT, navigation events, and identity mapping are presented as near-equal download buttons. | Users can misunderstand privacy scope and export identity data casually. | Explain each export, isolate the identity export in a warning surface, and retain exact endpoints/semantics. | `templates/teacher/experiment_results.html` | High. Export columns and authorization must not change. |
| Login | P1 | Demo credentials are always visible; labels are not explicitly associated with inputs; hierarchy resembles a generic card. | Credentials can be exposed outside debug mode and keyboard/screen-reader labeling is weak. | Show demo credentials only in debug context, add explicit labels/errors and an institutional product layout. | `templates/registration/login.html`, login context | Medium. Authentication behavior remains Django-native. |
| Teacher dashboard | P2 | Three equal cards include a decorative “implementation principle” instead of actionable work state. | Space is consumed without helping the next decision. | Use restrained metrics and direct workflow entry points; emphasize recent banks and experiments. | `templates/teacher/dashboard.html` | Low. |
| Bank list/detail | P2 | Owner, updated time, visibility, import distinction, long stems, and empty-state next steps are weak or absent. | Teachers scan slowly and may choose the wrong import action. | Improve table hierarchy, status labels, responsive shell, and explicit empty-state actions. | bank templates | Low. |
| Import pages | P2 | Upload and preview lack a visible three-stage model, file/error summary, and clear irreversible confirmation hierarchy. | Teachers cannot see where they are or what confirmation means. | Add upload -> preview -> import step indicator and structured preview/errors. | import templates | Medium. Transaction logic remains unchanged. |
| KC, class, student lists | P2 | Scope/status is plain text and tables do not adapt cleanly to small screens. Password reset is dense inside each row. | Slow scanning and poor mobile usability. | Add semantic badges, responsive table shells, direct helper text, and stronger focus states. | list templates | Low. |
| Student dashboard/completion | P2 | Status and action language are under-specified; completion uses a decorative check character and generic statistic cards. | Students may not know whether to start, continue, or only view completion. | Make state/action explicit and show only factual completion counts with a clear return path. | student dashboard and completion templates | Low. |
| Error pages/Admin | P2 | No branded 403/404/500 templates; Admin has default branding. | Dead ends and inconsistent institutional identity. | Add direct error pages and lightweight Admin title/style customization. | error templates, admin site config/static CSS | Low. |

## Baseline design diagnosis

- Existing design dials: variance 2, motion 1, density 6.
- Preserve: navy identity, compact teacher information, single-question assessment, server-rendered navigation, current Chinese copy register.
- Retire: CDN-only runtime, identical rounded cards everywhere, weak focus/state semantics, inline styles, native dialogs, color-only status, and decorative metrics.
- Information architecture, routes, field names, and core workflows are sound and must remain stable.

