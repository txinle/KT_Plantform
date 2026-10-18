# School teacher capacity v4 launch

**Paused by user on 2026-10-18. Do not resume load or controller stages without an explicit user instruction. No student load was released and no capacity result exists.**

These tools target only `kt-school-teacher-capacity-20261018`, root
`/home/ktdata/student-capacity-tests/20261018-teacher-capacity`, loopback18086.
They do not call the historical v3 matrix/retest controllers or copy their volumes.
The application protocol remains v3 so the existing independent DB reconciler
retains its original capture, answer, confidence and ownership checks.

## Freeze locally after actual correctness checks

Use this checkout's Python and provisioned Node24.20.0. The candidate frontend
must already have passed its checks and been built. The pack builder separately
builds the b363+P4 frontend, overlays only measurement tools and four unchanged
browser-helper exports, then packages both sources with their matching assets.

```powershell
.\.venv\Scripts\python.exe server/scripts/load/build_teacher_capacity_v4.py --checkout . --output .tmp-teacher-capacity-v4-pack
```

The JSON report records both actual archive SHA256 values. No `.env`, private
runtime tree, dependency tree, credentials, temporary log or old data volume is
packaged. The baseline staging directory remains available for inspection.

## Prepare on the school host; do not start load yet

Upload the two immutable archives, package report and a bootstrap tree preserving
`server/scripts/load/*.py` and `deploy/student-capacity-c4.json`. Execute the tools
in the authenticated root context. `BOOT` below is that bootstrap tree, `ROOT` is
the fixed owned project root. Use the SHA from the actual package report for each
phase; there is no flag that marks correctness as passed.

```bash
python3 "$BOOT/server/scripts/load/run_teacher_capacity_v4.py" --source-sha256 "$CANDIDATE_SHA" --emit-manifest "$BOOT/candidate-manifest.json" --emit-matrix "$BOOT/matrix.json"
python3 "$BOOT/server/scripts/load/run_teacher_capacity_v4.py" --source-sha256 "$BASELINE_SHA" --emit-manifest "$BOOT/baseline-manifest.json"
python3 "$BOOT/server/scripts/load/prepare_teacher_capacity_v4.py" --manifest "$BOOT/candidate-manifest.json" --archive "$CANDIDATE_ARCHIVE" --phase candidate --build --create-owned-slices
python3 "$BOOT/server/scripts/load/prepare_teacher_capacity_v4.py" --manifest "$BOOT/baseline-manifest.json" --archive "$BASELINE_ARCHIVE" --phase baseline --build
```

Prepare BOTH phases before starting any new-project container: preparation
refuses an existing project container or volume. Occupied18086 also blocks it.
Per-phase databases, media, cache/static volumes are freshly named inside the
new project. The default app/driver caps are10CPU/10GiB and2CPU/2GiB beneath a
new12CPU/12GiB parent. Shared `ktconcurrency.slice` and other projects are never
modified. The candidate cleanup worker uses the same app envelope and is counted
in observed resources. Cached image layers may be reused only after the operator
has verified the exact Node/Python/Playwright versions; there is no downgrade.

Save the actual correctness evidence for each frozen source as private JSON with
`passed: true`, `source_archive_sha256` matching that archive, and the real test
commands/results or their evidence hashes. A missing/mismatched gate blocks the
controller before container startup. Do not manufacture this record to bypass
failed or unrun tests.

## Run one bounded stage at a time

```bash
python3 "$ROOT/source/candidate/server/scripts/load/control_teacher_capacity_v4.py" \
  --baseline-manifest "$ROOT/private/manifest-baseline.json" \
  --candidate-manifest "$ROOT/private/manifest-candidate.json" \
  --baseline-correctness-gate "$ROOT/private/correctness-baseline.json" \
  --candidate-correctness-gate "$ROOT/private/correctness-candidate.json" \
  --stage qualification
```

The default stage is qualification:2 actual Chromium/WebKit students plus48
protocol actors, three independently authenticated teacher accounts and six
actual teacher pages. It normally needs11–17minutes after image build and fixture
preparation. It does not release180 users. Subsequent explicit stages are
`baseline`(12cells), `candidate-entry`(24cells), and `candidate-full-flow`(8cells).
Stage prerequisites and existing cell evidence are checked; a released/interrupted
cell is never silently overwritten or replayed.

Each cell installs only its phase, creates a fresh180-account/20-item source and
baseline in the explicit fixture-builder profile (2GiB/2CPU within the existing app parent; not inside the live web container), starts an independent host observer/log collector, waits for valid
coverage, runs the isolated driver, then verifies all intended50/180 users through
the existing repeatable-read PostgreSQL reconciler. Protocol/browser split retains
the complete roster's arrival offsets. Browser cold navigation and protocol cold
bootstrap are recorded separately from first-POST-to-list readiness.

An OOM, changed non-project container lifetime, invalid generator, stale/missing
monitoring or incomplete workflow stops further release. Only the identity-checked
owned driver may be stopped; foreign containers are never stopped. Server and
partial DB/driver evidence remain available. SQL, connection, commit, password
hash, memory/CPU/IO, waits/backlog, full HTTP records and generator heartbeat
artifacts are retained under the owned evidence tree.

## Interpret evidence

Eighteen six-page teacher lifecycle repetitions are bounded. The seven-successful
GET base budget per page is checked separately from search, manual refresh,
pagination and failures. The user-confirmed entry policy reports raw teacher samples, failures and maximum latency only, without a P95 acceptance claim. Full-flow teacher quantiles require at least100 samples per request type DURING actual student pressure. Later samples are explicitly excluded. The last full-flow lifecycle is scheduled at workload duration plus half the arrival window to cover submission/publication. Do not pool later quiet requests to manufacture a passing tail estimate.

The controller records baseline performance failures as controls, but invalid
baseline measurement stops the stage. Candidate failures stop subsequent cells.
Full-flow mixed/save/navigation/submission comparisons use the matching source,
configuration, resources, window, bootstrap mode and round; each P95 increase must
be at most10%. One cell never establishes capacity. Formal acceptance requires
the entire frozen matrix and paired comparisons. Runtime is at least about7hours
for the complete matrix, excluding builds/recovery. Configuration stays at C4;
any later bottleneck-driven adjustment requires its own frozen one-dimension
evidence record and must respect the plan's maximum of two adjustments.
