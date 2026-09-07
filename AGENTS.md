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

- Raw behavioral evidence is append-only. Draft saves, content deduplication and conflict resolution must never replace or delete response trajectories. Capture-v2 keeps each confirmed transition, including empty states and returns to prior content.
- V1 scope is short-response capture, time, confidence, frozen KC, hint/check behavior and feedback exposure. Do not implement structured steps, workspaces, complex planning/reflection or correction attempts for this release.

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

\## Core Principle



Work proportionally to the requested change.



Prefer the smallest correct implementation and the smallest sufficient verification.

Do not turn a local modification into a repository-wide audit unless the task explicitly requires it.



\## 1. Scope Control



Before editing, identify the minimum set of files relevant to the request.



\- Inspect only files needed to understand or implement the change.

\- Do not recursively inspect the entire repository by default.

\- Do not review unrelated modules.

\- Do not modify unrelated code for cleanup, formatting, refactoring, or style consistency.

\- Preserve existing behavior outside the requested scope.

\- If the user names specific files, treat those files as the primary scope.



Do not expand the task merely because additional improvements are possible.



\## 2. Editing Strategy



Make the smallest coherent change that satisfies the request.



\- Prefer editing existing implementations over introducing new abstractions.

\- Do not refactor working code unless refactoring is necessary for the requested change.

\- Do not rename unrelated variables, functions, files, or modules.

\- Do not reformat entire files for a small edit.

\- Do not add compatibility layers, fallbacks, wrappers, configuration options, or defensive branches unless there is a concrete need.

\- Do not introduce speculative handling for hypothetical future requirements.



When several implementations are valid, prefer the simplest one consistent with the existing codebase.



\## 3. Investigation Budget



Do enough investigation to make the change correctly, then implement it.



Avoid repeated cycles of:



search → inspect → reconsider → search again



when the relevant implementation has already been identified.



Do not repeatedly reread unchanged files.



Do not independently verify obvious facts through multiple equivalent methods.



Once sufficient evidence exists to proceed safely, proceed.



\## 4. Agent Usage



Do not spawn subagents by default.



Use additional agents only when the task contains genuinely independent workstreams that benefit from parallel investigation.



Do not:



\- create agents merely to double-check your own work;

\- assign multiple agents to inspect the same code;

\- create separate agents for trivial search, editing, testing, or review;

\- recursively delegate simple tasks;

\- wait for redundant agent opinions before making an obvious local change.



For ordinary bug fixes, small features, configuration changes, and localized refactors, work directly.



\## 5. Testing Policy



Verification must be proportional to the change.



\### Local changes



Run the narrowest relevant test first.



Examples:



\- changed one function → run its unit tests;

\- changed one module → run that module's tests;

\- changed one API endpoint → run endpoint/integration tests relevant to it;

\- changed configuration → validate that configuration;

\- changed syntax or imports → run the relevant compiler/type/lint check.



Do not automatically run the full repository test suite for every small change.



\### Full-suite testing



Run broad or full test suites only when:



1\. the user explicitly asks for them;

2\. the change affects shared/core infrastructure;

3\. the change has broad cross-module consequences;

4\. targeted tests reveal a regression requiring wider investigation;

5\. repository instructions explicitly require the full suite.



Do not repeatedly rerun an unchanged full test suite without a specific reason.



\## 6. Validation and Review



After editing:



1\. inspect the actual diff;

2\. confirm the requested behavior was implemented;

3\. run relevant targeted verification;

4\. fix concrete failures if present;

5\. stop when sufficient evidence of correctness exists.



Do not perform multiple redundant "final reviews" of the same unchanged diff.



Do not generate additional review passes merely because more validation is theoretically possible.



\## 7. Unmodified Files



Treat unmodified files as unchanged unless there is concrete evidence otherwise.



Do not:



\- hash every unmodified file;

\- compare checksums across the repository;

\- reopen all previously inspected files after editing;

\- validate byte-for-byte preservation of unrelated files;

\- create backup copies solely for comparison.



Use version-control diff/status as the normal source of truth.



\## 8. Git Discipline



Use `git diff` and `git status` to understand changes.



Before finishing:



\- ensure only intended files were changed;

\- inspect the relevant diff;

\- do not revert pre-existing user changes;

\- do not overwrite unrelated modifications.



Do not use destructive Git commands unless explicitly required.



Do not create commits, branches, tags, or pull requests unless requested.



\## 9. Error Handling



Investigate concrete errors, not hypothetical ones.



If a command fails:



1\. read the actual error;

2\. identify the likely cause;

3\. make the smallest appropriate correction;

4\. rerun the relevant command.



Do not respond to one failure by launching a repository-wide diagnostic process unless evidence points to a broader problem.



Do not add silent fallbacks merely to make an error disappear.



\## 10. Dependencies



Do not add or upgrade dependencies unless required.



Before adding a dependency, check whether the repository already contains a suitable implementation or dependency.



Avoid changing lockfiles unless dependency changes are necessary.



Do not perform broad dependency upgrades as part of an unrelated task.



\## 11. Documentation and Comments



Add documentation only where it improves understanding of the requested change.



Do not:



\- add comments that merely restate obvious code;

\- rewrite unrelated documentation;

\- create new markdown reports summarizing routine work;

\- generate implementation plans, audit reports, or change logs unless requested.



\## 12. Planning



For simple tasks, do not create an elaborate implementation plan.



A short internal sequence is sufficient:



understand → edit → targeted verify → report



Use a detailed plan only for genuinely multi-stage or architectural work.



Do not spend more effort planning the task than implementing it.



\## 13. Completion Criteria



A task is complete when:



\- the requested change is implemented;

\- the relevant diff is clean and scoped;

\- appropriate targeted verification passes;

\- no known issue caused by the change remains.



At that point, stop.



Do not continue searching for additional work.



\## 14. Final Response



Keep the final response concise.



Report:



\- what changed;

\- which files were changed;

\- what verification was run;

\- any concrete unresolved issue, if one exists.



Do not provide a long narrative of routine searches, reasoning, or intermediate checks.



\## Priority Rule



Correctness is more important than speed, but redundant verification is not correctness.



Use evidence-driven verification rather than maximum possible verification.



When uncertain whether another action is necessary, ask:



> "Will this action materially increase confidence in the requested change?"



If the answer is no, do not do it.
## Anti-Overengineering Rule

Do not invent additional requirements.

The user's explicit request defines the task boundary.

Unless required for correctness, do not independently add:

- extra features;
- generalized frameworks;
- backward-compatibility mechanisms;
- migration systems;
- fallback implementations;
- extensive input validation;
- additional tests for unrelated behavior;
- repository-wide cleanup;
- architectural refactoring;
- performance optimization;
- security hardening unrelated to the requested change.

If you notice an unrelated issue, do not fix it automatically.
Mention it briefly only if it is materially important.
