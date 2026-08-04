# Repository instructions

- For UI audits or interface changes, use `$kt-research-ui` from `.agents/skills/kt-research-ui/` first.
- Preserve Django 5.2, Django Templates, Bootstrap 5.3, native CSS, and native JavaScript. Do not migrate to an SPA or add React, Vue, Tailwind, HTMX, GSAP, npm, or a frontend build step.
- Treat `.agents/skills/kt-research-ui/references/RESEARCH-INVARIANTS.md` as mandatory. UI work must not change visit, draft, submission, timing, snapshot, authorization, CSRF, idempotency, or export semantics.
- Preserve URLs, form names, DOM hooks, and Fetch contracts unless the same change includes regression coverage.
- Verify with `python manage.py check`, `python manage.py test`, `python manage.py collectstatic --noinput`, and Node syntax checks for both JavaScript files.
- Visually verify the affected teacher and student paths at 1440 x 900 and 390 x 844. Record only checks actually performed.
