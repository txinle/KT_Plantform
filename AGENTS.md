# Repository instructions

## Product and architecture boundaries

- For UI audits or interface changes, use `$kt-research-ui` from `.agents/skills/kt-research-ui/` first. Treat `.agents/skills/kt-research-ui/references/RESEARCH-INVARIANTS.md` as mandatory.
- The target architecture is a Django 5.2 modular monolith with DRF plus a progressive Vue 3 + TypeScript + Vite frontend. Keep authoritative domain logic in Django services.
- Migrate modules on their existing business URLs. Use server-side module kill switches and Course/User allowlists; do not create a permanent `/app` route or an Nginx SPA fallback.
- Vue Router may handle only migrated module routes. Navigation to an unmigrated page must perform a normal document navigation to Django.
- Do not introduce microservices, JWT, GraphQL, Kubernetes, Tailwind, or a full-site SPA rewrite without an approved product need.
- Preserve Django Templates, Bootstrap 5.3, native CSS/JavaScript, and existing URLs until each migrated module satisfies its explicit legacy-exit criteria.

## A+ experiment invariants

- Experiment defines frozen content and post-start time limits; each `ClassExperimentTask` defines class participation, opens/closes, schedule revisions, and termination facts.
- `PublishPlan` may save incomplete but semantically valid setup. Saving must not generate a preview; full validation and deterministic sampling/order happen only on preview.
- A preview must cover every selected participant, must not create formal assignments, must not expose raw seeds, and must not reroll the Experiment × Class randomization root across plans.
- Commit is atomic: freeze one release definition and mapping, then create every selected class task and participant snapshot or create none.
- Student start/resume and all time-sensitive writes must resolve through the authoritative task, participant snapshot, schedule, and `DeadlineDecisionService`.
- Feedback fields are server-projected by `feedback_mode`; never send forbidden answers, explanations, scores, averages, or ranks and hide them only in Vue.
- Preserve assessment event payloads, IndexedDB `kt-process-events-v1` version 2, idempotency, writer lease, checkpoint, CSRF, authorization, Coverage, and export semantics.

## Data, environment, and verification

- Current local data is disposable demo data. Rebuild the development database when an unshipped schema changes; do not add migrations solely to adapt intermediate demo states. Never apply this rule to real or deployed data.
- For Phase 6, do not migrate existing local question-bank rows. Clear the current development-only question-bank data plus the minimum required demo dependency closure, then recreate representative banks, questions, and KC mappings through `seed_demo`. Preserve production lifecycle and schema contracts; never apply this deletion rule to real or deployed data.
- Runtime baselines are Python 3.13, Django 5.2.16, PostgreSQL 17, Node 24 LTS, Vue 3.5, Vite 8, npm, and `package-lock.json`. Do not add a third-party icon library; use approved SVG assets or text controls.
- If the host lacks a required baseline runtime or Docker is unavailable, download and provision a project-scoped or user-scoped portable toolchain matching those versions (including PostgreSQL and Node) and record the exact executable/data paths used. Do not silently downgrade versions or substitute SQLite for PostgreSQL verification.
- On this Windows workspace, use the already provisioned Node `C:\Users\31606\Downloads\ATOP-tooling\node-v24.20.0-win-x64\node.exe`, PostgreSQL binaries under `C:\Users\31606\Downloads\ATOP-tooling\PostgreSQL-17.11-portable\pgsql\bin`, PostgreSQL data directory `C:\Users\31606\Downloads\ATOP-tooling\PostgreSQL-17.11-data`, and Playwright browsers at `C:\Users\31606\Downloads\ATOP-tooling\pw-fresh-browsers`.
- Run Python through the active checkout's `.venv\Scripts\python.exe`; do not borrow a sibling checkout's environment. Before frontend or PostgreSQL commands, prepend the provisioned Node and PostgreSQL `bin` directories to `PATH` and set `PLAYWRIGHT_BROWSERS_PATH=C:\Users\31606\Downloads\ATOP-tooling\pw-fresh-browsers`.
- The portable PostgreSQL instance listens on `127.0.0.1:55432`; use role `kt_user`, the repository's local-only password `kt_password`, development database `kt_platform`, and isolated E2E database `kt_e2e`. Pass these through `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_DB`.
- Docker Desktop is installed but its daemon is unavailable because WSL is not operational. For the current implementation phases, use the portable PostgreSQL instance for required PostgreSQL verification and do not retry Docker/WSL unless the user asks or the host state changes.
- Prefer Docker Compose for PostgreSQL and production-parity checks. SQLite is only a fast local test path and cannot prove PostgreSQL constraints, locking, or concurrency behavior.
- Use test-driven development for behavior changes. Run the narrowest relevant tests first, then the phase checkpoint commands required by the implementation plan.
- Backend checkpoint: `python manage.py check`, `python manage.py makemigrations --check --dry-run`, `python manage.py test`, and `python manage.py collectstatic --noinput`.
- Frontend checkpoint: `npm ci`, `npm run lint`, `npm run typecheck`, `npm run test:unit`, `npm run api:check`, and `npm run build`.
- Visually verify affected teacher and student paths at 1440×900, 390×844, and 200% zoom. Record only checks actually performed.
- Keep changes scoped, inspect `git diff`/`git status`, preserve unrelated user edits, and do not commit, branch, or open a PR unless requested.
