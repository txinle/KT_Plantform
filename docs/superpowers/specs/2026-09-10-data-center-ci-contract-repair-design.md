# Data-center CI contract repair

## Problem

The application E2E job fails because the data-center redesign and its browser contracts diverged. The current product uses the headings `导出研究数据` and `研究数据预览`, supports multiple experiment selections through `experiment_ids` checkboxes, and gives the data-center workspace a 236 px desktop sidebar. Older E2E assertions still expect the retired headings, a single `experiment_id` select, and the shared 224 px sidebar width.

The advanced-data route also renders no level-one heading while its required experiment/class scope is incomplete. This makes the page identity disappear in a valid empty state and breaks the generic layout and standalone-shell audits.

## Approved design

- Keep the redesigned data-center behavior, multi-experiment scope, and 236 px data-center sidebar.
- Render the `研究数据预览` page heading for the advanced route before the scope-completeness branch, so loading, empty-scope, and populated states retain a stable page identity without duplicate headings.
- Update the focused browser contracts to use the current export and advanced headings.
- Update the scope mutation flow to select the named experiment checkbox and class select, then assert the current scope summary.
- Make the shared layout inspector expect 236 px only for the data-center workspace and 224 px elsewhere.
- Do not change backend APIs, database schema, permissions, assessment behavior, or deployment configuration.

## Verification

1. Add or adjust a component test proving that an incomplete advanced-data scope still renders exactly one `研究数据预览` heading.
2. Run that test before the production change and confirm the expected failure.
3. Implement the smallest template change and rerun the focused component tests.
4. Run the affected Chromium E2E cases, then the full application E2E matrix required by CI.
5. Inspect the final diff and merge only the verified feature branch into local `main` before pushing GitHub.

