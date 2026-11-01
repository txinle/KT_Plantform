# R3 local implementation and acceptance

Updated: 2026-10-31 22:53 (Asia/Shanghai). Repository: `E:/ATOP/kt-data-platform-complete`. Baseline: `8f5ad282d201c14a4e6281e663a43b11abfcdd21`.

## Final scope and remaining acceptance

The latest user amendment is authoritative: **both course and unbound banks support reading/cloze group import and whole-group editing; formal group publication, student work, results and export use course experiments and A+ class tasks.** All exports reuse standard `research-export-v2`, one SessionID/subquestion record per row. No v3 pipeline, group-only CSV, additional CSV columns or standalone group student entry remains. Existing historical endpoints and ordinary standalone APIs retain their behavior.

Local implementation and automated acceptance are complete. **Native 200% browser zoom and a real-phone soft-keyboard check remain unverified.** Computer Use reported a physical Escape stop; no further desktop input was performed. Automated 720×450 CSS viewports are labelled separately. No claim of native zoom, real-device keyboard acceptance, deployment or production UAT is made.

No branch, commit, push or deployment was created. Pre-existing research directories were left outside this task.

## Stage state

| Stage | State | Concrete result |
|---|---|---|
| P0 reference archive | verified | Sites v1 HTML/CSS/JS plus two user annotations archived with manifest; 68 registered entries mapped. |
| P1 model and completeness | verified | `QuestionGroup`, same-bank membership, ordered members, revision checks, positive per-item Decimal scores and default score 1. Feature migration question_bank/0009. |
| P2 import | verified | Excel/CSV/DOCX grouped formats, strict headers, persistent preview/confirmation, whole-group selection, deduplication and idempotency. Both bank scopes; existing standalone formats retained. |
| P3 teacher views | verified | Shared material display, answer-safe preview, whole-group editor, scores/case settings, scoped standalone primary KC without creating a course or silently creating KCs. |
| P4 grading | verified | Frozen full scores and case/whitespace matching applied to automatic, whole-session and manual grading. Pending grades remain distinct from scored answers. |
| P5 publication | verified | Course-only central guard; complete groups, deterministic unit ordering, fingerprint invalidation, frozen group snapshots (experiments/0034), runtime capability and prepared manifest v2. Teacher form can create/edit an all-pool group configuration. |
| P6 student runtime | verified | One serialized activation controller, per-item writes/leases, retained group DOM, durable pending input and receipt-based acknowledgement. Clear/reload/switch and final submission passed three browser engines. |
| P7 results/export | verified | Authorized grouped results and weighted score progress. Existing v2 7-CSV + 1-JSON ZIP; 18 per-item responses verified. Group material is included only in the existing question-library JSON. Old field-selection Excel UI removed from the compatibility result page; historical download endpoints preserved. |
| P8 platform UI | implemented; automated checks verified | Reference colors/fonts, wide workspace, measured sticky header and scoped feature styles. Route 204-state check, existing 8 layout audits, 108 group-layout states and 12 final class-layout states passed. Native zoom/phone keyboard remain pending as above. |

## Final verification evidence

These counts refer to their own commands and overlap; they must not be summed as unique tests.

| Check | Actual outcome | Evidence under artifacts/english-groups-ui-20261031 |
|---|---|---|
| Full frontend unit | 115 files / 936 tests passed | `frontend-unit-final.log` |
| Frontend lint | exit 0; 0 errors, 2 existing WorkspaceShell.test.ts warnings | `frontend-lint-final.log` |
| API types and schema | generation, api:check and export_api_schema --check exit 0 | `api-schema-generation.log`, `frontend-api-final.log`, `backend-schema-final.log` |
| Final build incl. typecheck | exit 0 after the final class CSS change | `frontend-build-final.log` |
| Backend metadata/static checks | check, migration dry-run and collectstatic exit 0 | `backend-checks.log`, `backend-static.log` |
| Full backend first run | 2626 tests / 40 skipped; initially 4 failures + 4 errors | `backend-full.log` |
| All identified backend failures | corrected; 26-test correction set passed | `backend-failure-corrections.log` |
| Final group publication/API | 9 tests passed, including latest create/edit/readiness and course-only guard | `group-publication-final.log` |
| Course runtime regression | 77 tests passed | `course-runtime-regression.log` |
| Standard export compatibility | 68 tests passed | `standard-export-override-green.log` |
| Ordinary standalone API preservation | 6 tests passed | `ordinary-standalone-preservation.log` |
| Real grouped journey, Chromium | 1 passed / 1.4 min | `e2e-course-approved-chromium.log`, `playwright-course-approved/chromium/` |
| Same journey, Firefox + WebKit | 2 passed / 3.8 min | `e2e-course-approved-remaining.log`, `playwright-course-approved/remaining/` |
| 68 routes + existing layout audits | 9 tests passed / 4.4 min; 204 route states, no recorded issues | `e2e-routes-final.log`, `playwright-routes-final/` |
| Final class button styling | 12 viewport states passed; no overflow, visible text buttons >=44px and >=14px | `class-controls-final.log`, `class-controls-final/findings.json` |

**The full backend suite was not rerun after its eight identified failures were fixed.** Each identified failure was reverified in the targeted correction set, followed by the course/runtime, export and latest publication checks. This is not an all-green full-backend-suite claim. The full-run failures were: removed standalone list payload, missing max_score in test fixtures, new migration leaf expectation, query-count regression (fixed by related snapshot loading), and a stale in-memory publication-plan revision in an existing test fixture.

The final real journey uploads reading-3 and cloze-15 files into each bank (18 items per bank), edits all four groups, saves the course full-pool form through actual PATCH, performs A+ preview/commit, answers 18 items, confirms clear/reload/switch persistence, submits the paper and verifies **16/21 points, 14/18 correct, 8 export files and 18 response rows**. Browser repetition is engineering acceptance, not an independent research result or user-load test. Student-visible material was checked for forbidden grading fields.

## Main changed paths and responsibilities

| Responsibility | Main paths |
|---|---|
| Group model and editing | `server/apps/question_bank/models.py`, `services/question_groups.py`, `services/standalone_kc.py`, `api/group_views.py`, migration `0009_question_groups_and_weighted_scores.py` |
| Import/readiness/templates | `services/unified_reader.py`, `unified_import.py`, `word_import.py`, `preview_batch.py`, `import_templates.py`, `question_readiness.py`, management serializers/views and downloads |
| Frozen publication | `server/apps/experiments/question_group_publication.py`, `publication_services.py`, `services.py`, `models.py`, API serializers/views, migration `0034_questionsnapshot_blank_id_and_more.py` |
| Grading and student contract | `server/apps/assessment/group_runtime.py`, `prepared_services.py`, `feedback_services.py`, `manual_grading_services.py`, `session_submission_services.py`, `student_results.py`, API serializers/session_summary and group event schemas |
| Results and original export integration | `server/apps/analytics/teaching/student_projection.py`, `student_result_snapshots.py`, `research_export_v2.py`, `downloads.py`, `export_fields.py` |
| Shared group presentation | `client/src/question-content/`, including content/results components, schema types, CSS and tests |
| Teacher group import/edit | `client/src/features/teacher-bank-management/QuestionGroupEditor.vue`, `QuestionGroupImportPreview.vue`, `group-api.ts`, both import/detail entrypoints; ordinary question editors for score/case fields |
| Teacher course configuration | `client/src/features/teacher-experiments/TeacherExperimentForm.vue` and readiness API type; retired checkbox export UI in `TeacherStandaloneExperimentResults.vue` |
| Student writing and recovery | `client/src/assessment-runtime/StudentAssessment.vue`, `group-activation.ts`, `prepared-paper.ts`, `process-types.ts`, CSS and focused tests |
| Student results | `client/src/features/student-results/StudentResult.vue`, `StudentSessionComplete.vue`, authorized API types and shared group-result component |
| Platform appearance | `client/src/design-system/` tokens/foundations/workspace/AppHeader and scoped teacher/student feature CSS; final class text-button rules stay under `#class-main` |
| API generated artifacts | `contracts/openapi.yaml`, `client/src/api/generated/schema.d.ts`; generated using repository commands |
| Demo and real acceptance | `seed_demo.py`, bounded `seed_e2e_english_groups.py`, the two new English group E2E specs and targeted server/client tests |

<details>
<summary>Actual changed/new client, server and contract paths (130)</summary>

- `client/e2e/english-groups-route-visual.spec.ts`
- `client/e2e/english-question-groups.spec.ts`
- `client/src/api/generated/schema.d.ts`
- `client/src/assessment-runtime/StudentAssessment.test.ts`
- `client/src/assessment-runtime/StudentAssessment.vue`
- `client/src/assessment-runtime/api.ts`
- `client/src/assessment-runtime/group-activation.test.ts`
- `client/src/assessment-runtime/group-activation.ts`
- `client/src/assessment-runtime/prepared-paper.ts`
- `client/src/assessment-runtime/process-types.test.ts`
- `client/src/assessment-runtime/process-types.ts`
- `client/src/assessment-runtime/student-assessment.css`
- `client/src/design-system/AppHeader.test.ts`
- `client/src/design-system/components/AppHeader.vue`
- `client/src/design-system/foundations.css`
- `client/src/design-system/tokens.css`
- `client/src/design-system/workspace.css`
- `client/src/features/student-courses/api.ts`
- `client/src/features/student-courses/student-courses.css`
- `client/src/features/student-results/StudentResult.vue`
- `client/src/features/student-results/StudentSessionComplete.vue`
- `client/src/features/teacher-account-management/teacher-account-management.css`
- `client/src/features/teacher-analysis/TeacherAttemptProcess.vue`
- `client/src/features/teacher-analysis/TeacherExperimentResultsCompatibility.vue`
- `client/src/features/teacher-analysis/TeacherExperimentSessionCompatibility.vue`
- `client/src/features/teacher-analysis/TeacherQuestionDetail.vue`
- `client/src/features/teacher-analysis/TeacherStudentDetail.vue`
- `client/src/features/teacher-analysis/TeacherStudentExperimentDetail.vue`
- `client/src/features/teacher-bank-management/QuestionGroupEditor.test.ts`
- `client/src/features/teacher-bank-management/QuestionGroupEditor.vue`
- `client/src/features/teacher-bank-management/QuestionGroupImportPreview.test.ts`
- `client/src/features/teacher-bank-management/QuestionGroupImportPreview.vue`
- `client/src/features/teacher-bank-management/TeacherBankManagement.test.ts`
- `client/src/features/teacher-bank-management/TeacherQuestionEditor.test.ts`
- `client/src/features/teacher-bank-management/TeacherQuestionEditor.vue`
- `client/src/features/teacher-bank-management/TeacherQuestionImport.vue`
- `client/src/features/teacher-bank-management/TeacherStandaloneBankDetail.vue`
- `client/src/features/teacher-bank-management/TeacherStandaloneQuestionEditor.vue`
- `client/src/features/teacher-bank-management/TeacherStandaloneQuestionImport.test.ts`
- `client/src/features/teacher-bank-management/TeacherStandaloneQuestionImport.vue`
- `client/src/features/teacher-bank-management/api.ts`
- `client/src/features/teacher-bank-management/group-api.ts`
- `client/src/features/teacher-bank-management/question-import.css`
- `client/src/features/teacher-bank-management/standalone-api.ts`
- `client/src/features/teacher-classes/ClassOverviewCharts.vue`
- `client/src/features/teacher-classes/teacher-classes.css`
- `client/src/features/teacher-courses/teacher-courses.css`
- `client/src/features/teacher-data-center/teacher-data-center.css`
- `client/src/features/teacher-experiments/TeacherExperimentForm.test.ts`
- `client/src/features/teacher-experiments/TeacherExperimentForm.vue`
- `client/src/features/teacher-experiments/TeacherStandaloneExperimentResults.vue`
- `client/src/features/teacher-experiments/TeacherStandaloneExperimentViews.test.ts`
- `client/src/features/teacher-experiments/api.ts`
- `client/src/features/teacher-experiments/teacher-experiments.css`
- `client/src/features/teacher-question-banks/TeacherQuestionBankDetail.vue`
- `client/src/features/teacher-question-banks/api.ts`
- `client/src/features/teacher-question-banks/teacher-question-banks.css`
- `client/src/features/teacher-teaching-data/teacher-teaching-data.css`
- `client/src/question-content/QuestionGroupContent.test.ts`
- `client/src/question-content/QuestionGroupContent.vue`
- `client/src/question-content/QuestionGroupResults.vue`
- `client/src/question-content/question-group-content.css`
- `client/src/question-content/question-group-content.ts`
- `client/src/question-content/question-group-results.ts`
- `contracts/openapi.yaml`
- `server/apps/accounts/management/commands/seed_demo.py`
- `server/apps/analytics/downloads.py`
- `server/apps/analytics/export_fields.py`
- `server/apps/analytics/research_export_v2.py`
- `server/apps/analytics/student_result_snapshots.py`
- `server/apps/analytics/teaching/student_projection.py`
- `server/apps/analytics/test_group_student_results.py`
- `server/apps/analytics/test_question_group_exports.py`
- `server/apps/assessment/api/serializers.py`
- `server/apps/assessment/api/session_summary.py`
- `server/apps/assessment/event_batch_services.py`
- `server/apps/assessment/event_invariants.py`
- `server/apps/assessment/event_schema_registry.py`
- `server/apps/assessment/event_schemas/group_left/v1.json`
- `server/apps/assessment/event_schemas/group_presented/v1.json`
- `server/apps/assessment/feedback_services.py`
- `server/apps/assessment/group_runtime.py`
- `server/apps/assessment/manual_grading_services.py`
- `server/apps/assessment/prepared_services.py`
- `server/apps/assessment/services.py`
- `server/apps/assessment/session_submission_services.py`
- `server/apps/assessment/student_results.py`
- `server/apps/assessment/test_feedback_services.py`
- `server/apps/assessment/test_group_presentation_events.py`
- `server/apps/experiments/api/serializers.py`
- `server/apps/experiments/api/views.py`
- `server/apps/experiments/migrations/0034_questionsnapshot_blank_id_and_more.py`
- `server/apps/experiments/models.py`
- `server/apps/experiments/publication_services.py`
- `server/apps/experiments/question_group_publication.py`
- `server/apps/experiments/services.py`
- `server/apps/experiments/test_a_plus_migration_contract.py`
- `server/apps/experiments/test_group_snapshots.py`
- `server/apps/experiments/test_multi_bank_workflow.py`
- `server/apps/question_bank/admin.py`
- `server/apps/question_bank/api/download_urls.py`
- `server/apps/question_bank/api/group_views.py`
- `server/apps/question_bank/api/management_serializers.py`
- `server/apps/question_bank/api/management_urls.py`
- `server/apps/question_bank/api/management_views.py`
- `server/apps/question_bank/api/serializers.py`
- `server/apps/question_bank/api/views.py`
- `server/apps/question_bank/downloads.py`
- `server/apps/question_bank/forms.py`
- `server/apps/question_bank/management/commands/seed_e2e_english_groups.py`
- `server/apps/question_bank/migrations/0009_question_groups_and_weighted_scores.py`
- `server/apps/question_bank/models.py`
- `server/apps/question_bank/services/example_groups.py`
- `server/apps/question_bank/services/import_service.py`
- `server/apps/question_bank/services/import_templates.py`
- `server/apps/question_bank/services/lifecycle.py`
- `server/apps/question_bank/services/preview_batch.py`
- `server/apps/question_bank/services/question_groups.py`
- `server/apps/question_bank/services/question_readiness.py`
- `server/apps/question_bank/services/scoring.py`
- `server/apps/question_bank/services/standalone_kc.py`
- `server/apps/question_bank/services/unified_import.py`
- `server/apps/question_bank/services/unified_reader.py`
- `server/apps/question_bank/services/word_import.py`
- `server/apps/question_bank/test_english_group_fixtures.py`
- `server/apps/question_bank/test_group_import.py`
- `server/apps/question_bank/test_group_import_templates.py`
- `server/apps/question_bank/test_group_management_api.py`
- `server/apps/question_bank/test_question_groups.py`
- `server/apps/question_bank/test_standalone_group_kcs.py`

</details>

## References, data and next action

- Main specification: `docs/superpowers/plans/2026-10-31-english-groups-ui.md`.
- Visual source and archive: `docs/design/english-groups-ui-20261031/visual-spec.md`, `reference/manifest.json`; archived CSS SHA256 `6812258D2673D4865D0E67FBFC3FE354E74AA85A7B79F38D7EE85142D1F3848B`.
- Per-route screenshots, existing redirects and unchecked native zoom: `docs/design/english-groups-ui-20261031/route-checklist.md`.
- Runtime/toolchain: checkout `.venv/Scripts/python.exe`; Node `C:/Users/31606/Downloads/ATOP-tooling/node-v24.20.0-win-x64/node.exe`; PostgreSQL 17.11 portable binaries/data and port 55432 as specified in AGENTS.md; Playwright browsers `C:/Users/31606/Downloads/ATOP-tooling/pw-fresh-browsers`.
- Database actions: the previously empty `kt_platform` schema was initialized with migrations and `seed_demo` (logged `development-initialize.log`); no database drop or production-data reset. Browser acceptance used isolated `kt_e2e`, whose R3 seeder resets only its own fixture closure. Backend tests used isolated test databases.
- Next acceptance is **native browser 200% zoom and a real-phone soft keyboard**, when desktop input/device access is available. No additional feature, export pipeline, formal experiment, deployment or full-suite rerun is implied by completion of this local implementation.

## Historical execution record

The following is retained for recovery and red/green evidence. Its interim standalone/v3 requirements and pending test states were superseded by the final scope and verification above.

# R3 implementation progress

Repository: E:/ATOP/kt-data-platform-complete
Baseline: 8f5ad282d201c14a4e6281e663a43b11abfcdd21
User authorization: implement R3 locally with the later course-only formal-flow/export amendment in the main plan; no branch, commit, push or deploy.

## Active scope correction (latest user instructions)

The user rejected the old standalone Excel field-selection interface and explicitly requested reuse of the existing export with one SessionID/subquestion record per row. In a scope clarification the user selected: “统一进入课程实验，复用现有导出”. Both bank scopes retain import/edit; formal group publish/student/results/export are course-only. Remove our standalone student entry, v3 export branch/tables/columns, and legacy Excel expansions. Preserve existing standard v2 export and historical endpoints. Current older stage/export acceptance below is historical until reconciled with this amendment.

Parent next: remove standalone-only frontend/API/runtime additions; enforce course-only group publishing with a failing test; retire old checkbox export block from the touched compatibility page. Backend agent is reverting only owned v3/Excel/teacher-standalone additions and updating real E2E to both-bank import/edit + course formal journey + v2 eight-file ZIP/18 rows. Do not start a new export pipeline or make course foreign keys nullable. Full backend test process63812/61468 is still running on test_kt_platform; use test_kt_e2e for targeted tests.

## Stages

| Stage | Status | Evidence / next step |
|---|---|---|
| P0 archive | verified | Exact accepted specification saved; 3 Sites source files and 2 annotations copied; SHA256 manifest written; 68 registered routes inventoried. |
| P1 group models | implemented, targeted verified | QuestionGroup, same-bank/marker/order/revision checks, feature migration 0009. Bank suite247 green; later private-KC additions76 combined tests green. |
| P2 parsing/import | implemented, targeted verified | Persistent batches both scopes, group confirmation/idempotency, strict old/new templates, Word labels, 3 grouped template URLs. Full bank regression247 green before final private-KC additions. |
| P3 teacher UI | implemented, awaiting real E2E | Shared content/preview/editor; both bank details; whole-group selection; all-question score/case UI. Targeted editor/detail/import tests green. |
| P4 scoring | implemented, targeted verified | Frozen max_score/case propagated across automatic/session/manual grading. Snapshot+question-type+manual grading10 green. |
| P5 publication | implemented, targeted verified | Group snapshot0034, source fingerprint, complete-unit sorting both entrypoints; prepared manifest2 capability. Publication/prepared regression52 tests OK,2 skipped. |
| P6 student runtime | implemented, browser verification in progress | Group renderer, first-input activation queue, same-group DOM preservation, durable pending-input recovery; standalone student start/list/results and group observation events. Targeted runtime tests green; real18-item browser checks running. |
| P7 results/export | implemented, targeted verified | Grouped exportv3 preserves frozenv2. Student/teacher totals and group breakdown connected; standalone optional Excel columns use frozen identities and scores. New standaloneP7 tests4green; compatibility70green. Final browser exports in progress. |
| P8 global UI | in progress | Tokens/fonts/wide canvas/sticky header measured height implemented; header test green. Agent applying remaining feature CSS and route mappings; visual ticks remain unchecked. |
| Integrated acceptance | in progress | Latest frontend115files/935tests green; typecheck/lint/build/API checks green. Django check/migrations/schema/static green. Existing browser layout audits8green. Full backend still running; final3engine E2E in progress. |

## Baseline checks actually performed

- git status --short: only pre-existing untracked research directories before P0; none touched.
- PostgreSQL pg_isready -h 127.0.0.1 -p 55432: accepting connections.
- checkout .venv/Scripts/python.exe and client/node_modules exist.
- P0 archive command exited 0; 5 files; 68 routes; CSS SHA256 6812258D2673D4865D0E67FBFC3FE354E74AA85A7B79F38D7EE85142D1F3848B.

## Resume protocol

Read main plan, this progress file, visual-spec.md and reference/manifest.json, then inspect git status and relevant diffs. Do not rerun completed stages. Each implementation stage records changed paths, red/green commands and exit codes, evidence paths, unresolved items and the exact next action.

## Current next action

Complete real18-item E2E for both scopes in Chromium/Firefox/WebKit, then existing layout audits and remaining visual sizes. Full backend process PID63812/61468 started20:47 uses test_kt_platform; any targeted backend tests use test_kt_e2e while that runs. prepare-e2e --capture-v2 has succeeded once on isolatedkt_e2e; do not reset it again. New group spec resets only its own R3 fixture closure per run. No production/dev DB migrations or resets.

## Verified commands / red-green evidence through initial integration

- vitest QuestionGroupContent: red missing component; green4; additional IME regression red then green5.
- vitest AppHeader: red missing measured offset; green1, verifies64->112px wrapped header.
- vitest QuestionGroupImportPreview: red missing component; green1, entire group selection and teacher-secret omission.
- vitest TeacherStandaloneQuestionImport + preview + TeacherBankManagement: red group selection absent; green12.
- vitest QuestionGroupEditor + two bank details: green6 after score input retained as a string.
- vitest ordinary course/standalone question editors: red missing score field; green9.
- manage.py test apps.experiments.test_group_snapshots: initial red no frozen score/order; group order/fingerprint red [3,1,2] and unchanged digest; green after helpers. New grouped session capability red0!=1, then green.
- manage.py test group_snapshots + assessment.test_question_types + assessment.test_manual_short_answer_grading:10 green after preserving default manual Decimal representation.
- manage.py test group_snapshots + experiments.test_sampling_v2:18 green.
- manage.py test group_snapshots + experiments.test_a_plus_publication + assessment.test_prepared_content:52 OK,2 skipped,67.419s.
- vitest StudentAssessment + group-activation + QuestionGroupContent:110 green (103+2+5). Expected jsdom document-navigation notice in existing tests.
- Frontend typecheck passed before latest editor integration; will rerun after generated API update.

## Integrated gates and browser corrections (20:47 onward)

- Full frontend unit command exited0:114 files,929 tests. Logs frontend-unit.log; typecheck, lint and build exited0 with separate logs.
- export_api_schema generated actual contracts/openapi.yaml, then api:generate regenerated schema.d.ts. export_api_schema --check and npm api:check both exited0. Schema has19 pre-existing enum naming warnings and0 errors.
- manage.py check exited0; makemigrations --check --dry-run exited0 (no changes); collectstatic exited0 (155 unchanged). Evidence backend-checks.log, backend-schema-check.log, backend-static.log.
- First real Chromium attempt parsed18 rows/2ready groups, imported both banks, completed course A+preview/commit jobs and standalone publication. Subsequent runtime acceptance remains in progress.
- Browser-found preview header incorrectly counted ordinary-only rows; added mixed-group regression (red1!=2), fixed header allRows and ordinary table's own filters; TeacherBankManagement10green.
- Browser-found standalone student course list500 from nonexistent assessment_api reverse namespace; added failing GET collection regression, fixed registered name; test_standalone_groups4green.
- Agent group_backend owns newgroup E2E all3engines and module visual fixes; parent owns existing layout audits, remaining fixedsizes/zoom, full backend result and final progress/checklist. Avoid simultaneous managed gateway starts on8000.
- Frontend process event sanitizer was missinggroup_presented/group_left despite backend schemas. Added v1 payload fields with2red/green tests; server-denied/failed-init group inputs now readonly. Browser initialization thenpassed.
- Real Chromium/Firefox completedbothscope36response journeys with14correct/18 andweighted16/21; v3ZIP actualbytes verified10files/18responses/2groups, with CSVcorrectness encoded是/否. Final verifiercorrected toactualcontract.
- WebKit exposedconfirmedclear/pendingrecovery bug. Replacedtext-status watcher withreceipt-based durableack; red regression proved oldcodeclearedanunconfirmedbuffer prematurely. Latest runtime109green; finalWebKit acceptance pending.
- TeacherStandaloneExperimentResults nowrendersfrozencustomscore totals and expandable readonlygroups. Sharedrenderer adds group-levelscore_progress andteacheranswerlabel. Teacher/results10green beforelatestfullfrontend935green.
- manage.pytest apps.analytics.test_standalone_group_results pluslegacystandalone/Excel/download/v2/v3 compatibility70green; pendingmember score:null correctedtoomittedscore with4green newtests.
- Existing Chromium layout-audit.spec.ts andlayout-expanded-audit.spec.ts8green/3.2min; screenshots/log underplaywright-layout ande2e-layout.log.
- New68route visual sweep: client/e2e/english-groups-route-visual.spec.ts; read-onlyfixtures artifacts/english-groups-ui-20261031/route-fixtures.py. ESLint andstricttsc(--allowJs)succeeded; runafterfinal3engine whenS002feedbackfixturecompleted.
- NativeComputerUse200% check stopped bytoolbecausecurrentWindowsbrowserURLcouldnotbereliablyverified. No furtherdesktopinput. Native200%isNOTverified; automated720x450CSS-equivalent isrecordedseparately.
- kt_platform verifiedempty (nopublictables), initializedfreshmigrations+seed_demo(exit0) at21:31. VerifiedQuestionGroups [('reading',3),('cloze',15)]. Evidence development-initialize.log. NoDBdrop/reset performed.

## Implementation decisions / recovery details

- Existing assessment_flow_version/paper_flow_version1 mechanics remain; new groups bind question_group_runtime_version1 at release/session creation and prepared manifest version2. Old prepared/capture contracts are not rebound.
- Shared renderer only displays server material segments and emits intents. Controller serializes activation; no concurrent15-attempt writers.
- Inputs awaiting activation are kept in existing CaptureStore metadata group-pending-input:<session>. Refresh exposes explicit recovery of unconfirmed inputs; it does not manufacture student input events during initialization.
- Course group edit uses existing course KC mapping; standalone group members use scoped primary_concept_id. Missing standalone group primary KC is draft, not a falsely publishable active state.
- Backend new feature migrations: question_bank0009 and experiments0034. No intermediate migration patch chain.


## Scope reconciliation and latest gates (22:34)

- Latest course-only amendment is implemented: standalone student list/start/results and teacher result additions removed; the central publication guard rejects unbound group formal publication. Both bank import/edit remain.
- v3 export pipeline/tables/columns are removed. Current acceptance is standard v2 eight-file ZIP, 18 SessionID/subquestion rows; only existing question-library JSON carries shared group content.
- Standard-export compatibility: 68 backend tests green. Course-only gate: 12 green. Historical ordinary standalone APIs: 6 green. Course runtime regression: 77 green.
- Full backend run finished: 2626 tests, 40 skipped, initially 4 failures + 4 errors. All identified issues were corrected; the 26-test correction set passed. This is NOT an all-green full-suite claim; no second unchanged full run has been performed.
- Latest frontend integration passed: 115 files / 936 tests, lint (0 errors, 2 existing test-file warnings), API generation/check, schema check, build (including typecheck).
- Corrected course-only E2E previously passed all 3 engines in 5.1 minutes. Expanded teacher all-pool save-draft step exposed an 11.6667px auxiliary-text font in all engines before publication. Root cause: browser small relative sizing inside a 14px form. Added base small font using the existing 14px token; build green. Agent owns final focused rerun with low-font diagnostics and real PATCH assertion. Do not start another managed gateway on 8000 until released.
- Native Computer Use reported a physical Escape stop; no further desktop input is authorized in this turn. Native 200% zoom remains UNVERIFIED. Automated 720x450 CSS viewport is separate evidence.
- Parent next: after agent releases 8000, run the 68-route sweep plus existing layout audits with the current build; inspect findings/screenshots, update route checklist and final progress. No commit/push/deploy.
