# Stage C client design and acceptance boundaries

Status: Stage C implementation released after Stage B diagnostics. Client local tests and build checks are running; authenticated browser integration and load acceptance belong to the parent integration task. Dependencies and IndexedDB database version remain unchanged.

## Chosen boundary

Replace the global `KTProcessData` loader and per-question bridge with one typed session controller, one persistent fact store, and one sender for each authenticated assessment session in each tab. The controller survives assignment route changes; its active attempt context changes only after an authorized entry receipt and DTO are validated. A second tab retains its independent client instance and existing server writer-lease arbitration. This does not make the browser authoritative for deadlines, permissions, task lifecycle, scoring, Coverage, randomization, or feedback.

Keeping a module wrapper around the legacy global would leave ownership and duplicate lifecycle queues unresolved. A navigation-only DTO change would leave per-question HTML fetching, the global collector, and fragmented recovery intact. Neither satisfies the approved Stage C target. The selected approach ports existing semantics into typed modules rather than inventing a second event or submission protocol.

## Bounded implementation files

| File | Responsibility |
| --- | --- |
| `src/assessment-runtime/session-controller.ts` | Own session identity, active attempt/visit, initialization generation, transition state, frozen entry/leave/submission intents, writer lease, lifecycle listeners, and disposal. Expose explicit record, navigation, submission, and recovery operations. |
| `src/assessment-runtime/fact-store.ts` | Typed IndexedDB transactions, raw event/checkpoint storage, sequence allocation, response recovery, immutable intent ownership, and exact receipt acknowledgement. |
| `src/assessment-runtime/process-sender.ts` | One shared drain/checkpoint queue per controller, bounded snapshots, per-client sequence acknowledgement, backoff and permanent rejection handling, clock synchronization, and safe keepalive behavior. |
| `src/assessment-runtime/process-types.ts` | Existing event/checkpoint/envelope shapes and payload allowlists; no new scientific measurements or event renaming. |
| `src/assessment-runtime/api.ts` | Separate read-only `question` GET and `enterQuestion` POST on the same current questions URL, returning the full authorized DTO and immutable entry receipt. Preserve existing save, lease, research-tool, submit, and recovery URLs. |
| `src/assessment-runtime/StudentAssessment.vue` | Render authorized facts and emit UI intent to the controller. Remove ownership of competing lifecycle/transition queues; keep presentation, accessibility, IME and current confirmation behavior. |
| `src/assessment-runtime/StudentResearchTools.vue` | Consume the typed controller record/confirmation interface; retain existing answer-event and content-version binding. |
| `src/assessment-runtime/offline-cache.ts` and the client-owned offline worker | Prepare one versioned shell and hashed assets, then authorized per-question DTOs. Stop fetching HTML for each question. Preserve worker URL and scope. |
| `src/app/bootstrap.ts` | Read offline identity/bootstrap from the authorized cached envelope when the worker supplies the generic shell. Keep online authentication and logout invalidation authoritative. |
| `vite.config.ts` | Emit the versioned shell and worker artifacts without runtime global script injection. |

Retire `collector-loader.ts` and `process-bridge.ts` after their consumers and tests move. Retire the legacy collector source when source search proves no active consumer remains. The remaining worker delivery view reads client-owned source today; switching worker delivery to built artifacts is a coordinated server/deployment integration, not a hidden client fallback.

Existing `response-trajectory.ts`, `text-observation.ts`, `writer-lease.ts`, and `operation-timing.ts` remain useful typed units. Reuse their contracts and tests. The store and sender absorb legacy collector behavior; they must not simply import the UMD source or access a global object.

## Durable data and confirmation

Keep IndexedDB database `kt-process-events-v1`, version 2, and existing `events`, `meta`, `checkpoints`, `dead_letters` stores and key formats. Keep raw records, event IDs, original client IDs and sequence numbers, acknowledgement ranges, response heads, and rejected records. Opening the new store must not clear, rewrite, deduplicate, or fabricate raw records. No memory fallback is allowed for formal capture-v2 writes.

Existing `submission-intent:<session>:<attempt>` and `navigation-intent:<session>:<attempt>` records are immutable until matching server confirmation or explicit server-confirmed rejection/cancellation. The sender excludes evidence owned by frozen intents from ordinary drains. Validate exact event IDs/sequences and checkpoint IDs before deleting confirmed evidence. Partial, mismatched, duplicate, or uncommitted receipts retain the entire uncertain snapshot.

New entry intent metadata preserves the original navigation UUID, assignment, reason, wall clock, monotonic clock, and time origin. For navigation, persist this material before sending the save-and-leave request; bind it to the durable leave snapshot through the original navigation token and source assignment; persist the confirmed leave phase before clearing the acknowledged snapshot so a crash between leave confirmation and target entry cannot lose the original click clocks. Old leave records lacking the extra clocks must remain recoverable: replay their frozen save-and-leave first, then capture and persist a separately identified new formal-entry action after authoritative confirmation. Do not invent missing historical clocks or mutate the old snapshot.

The sender continues draining old client evidence without relabeling it as the current client. Preserve the existing submission backlog/gap/envelope limits and dead-letter semantics. Transaction completion, not `IDBRequest` success alone, establishes a durable fact. A quota error or blocked upgrade keeps writes locked and leaves existing data intact.

## Entry and transition contract

1. Online initialization uses formal entry POST and its full DTO. `GET /api/v1/student/sessions/<session>/questions/<assignment>/` remains an authorized read-only recovery lookup. Nullable visit fields do not mean a new visit occurred. GET cannot unlock a new formal attempt or manufacture a deadline.
2. Freeze and persist the click UUID and all four timing fields before awaiting draft, snapshot, leave, or HTTP work. Direct opening, browser history entry, and reload recovery need explicit entry intent; readonly DTO lookup alone is insufficient.
3. For a question transition, wait for response capture, freeze the existing bounded save-and-leave snapshot, and confirm the original leave receipt through the existing domain endpoint. Unknown results keep the source attempt locked and the original snapshot intact. No second save/submit path is introduced.
4. POST the frozen entry material to the same questions URL. The request includes `navigation_id`, `client_occurred_at`, decimal `client_monotonic_ms`, `client_time_origin`, and reason. A lost response is replayed with identical material; do not mint a new token on retry.
5. Validate the returned immutable entry identity against the session, assignment, intent and active generation; install its full DTO atomically. Update the route with the existing business URL without issuing a second GET, clear consumed navigation query material, and ignore late responses from replaced generations.
6. Prepare the authorized offline DTO and versioned shell/assets, establish the response trajectory and lease state, and emit `item_rendered` once only when controls become interactive. Failed persistence or preparation withdraws readiness. Existing view-generation safeguards still prevent unmounted components from writing facts.

Server timing proposes immutable receipt fields derived solely from the existing event and visit: navigation/session/assignment/attempt/visit identities, visit index, entry event ID and entered-at time. `presented_deadline_at`, remaining seconds and other changing authorized fields remain in the DTO. Receipt replay equality must not imply byte equality of every DTO field. The server owner froze these eight receipt fields and `ENTRY_MATERIAL_CONFLICT`; generated types now consume the committed schema.

Do not map all HTTP 409 responses to retry. Response version, lease, navigation/submission material and task conflicts keep their existing specific handling. Unknown entry success stays uncertain; explicit material conflict cannot silently replace the original intent; termination prevents new interaction. The server remains responsible for start/finish/expiry and matching receipts.

## Versioned offline shell

Cache one generic shell per build version and hashed resources separately from authorized question DTOs. The shell contains no question, answer, account or permission data. On an offline question navigation, serve it only when the worker has an authorized cached envelope for the exact user/session/assignment/build and a matching DTO. Bootstrap comes from that envelope, not arbitrary HTML fetched for each question. Preparing the next question transfers its DTO and scoped bootstrap; it never fetches that question's HTML.

Maintain `/assessment/process/capture-v2/offline-worker.js` and `/student/sessions/` registration scope. Preserve the live `/api/v1/auth/session/` identity check on authorization, `identityEpoch` invalidation during races, logout/account-switch isolation, same-origin restrictions and mandatory resource verification. Asset preparation is shared per build; question authorization is not inferred from the existence of an asset or readonly GET. Readonly DTOs without an entry receipt do not create new offline formal entry. Previously entered cached attempts can restore their local trajectory; network-only POST mutations must never be intercepted as synthetic success.

Build switches require compatible shell/DTO versions. Keep needed old hashed chunks available through the existing deployment asset retention. Cache-version invalidation may clear shell/DTO caches but cannot clear raw IndexedDB evidence or unconfirmed intents. An old page/worker and new shell mix must fail readiness explicitly rather than discard facts or guess permissions.

## Acceptance tests to implement before each behavior change

| Boundary and test file | Required observable cases |
| --- | --- |
| `stage-c.contract.test.ts`, `api.test.ts` | GET has no entry query/write; nullable visits remain nullable; formal POST uses the same URL and original frozen clocks; full DTO installs without extra GET; lost response replay preserves material; business conflict propagates without generic retry. |
| `fact-store.test.ts` | Open a real legacy-v2 fixture with pending old-client events/checkpoints, response heads, dead letters and both intents; raw records remain identical; next sequence remains monotonic; concurrent enqueue is transactional; abort/quota/block errors do not report durability; exact and partial receipt acknowledgement; intent immutability and snapshot ownership; crash-safe leave-to-entry metadata. |
| `process-sender.test.ts` | Single flight across assignment change; record/snapshot/checkpoint fences; exact client partitioning; bounded oversize/backlog/gap failures; stale and partial acknowledgement retain rows; permanent rejection remains a dead letter; exponential backoff; pending intent evidence excluded from background batch; original clocks and operation IDs retained. |
| `session-controller.test.ts` | One controller/store/sender across question routes; only active attempt changes; one set of lifecycle listeners and clock/heartbeat timers; interaction remains locked through leave or entry uncertainty; replay after reload; duplicate click coalescing; A→B→A and history changes; stale DTO response ignored; dispose never drops raw data; pagehide/bfcache behavior; no global loader or continuous keyboard/mouse capture. |
| `StudentAssessment.test.ts`, `StudentResearchTools.test.ts` | Existing choice/text/IME trajectories, confirmation/confidence/research-tool bindings and feedback suppression remain correct; controls unlock only after exact DTO/offline/capture readiness; once-only item_rendered; save-and-leave keeps original answer/head/version; no silent final submission during navigation; authoritative deadlines and task errors stay locked. |
| `offline-cache.test.ts`, worker tests, `bootstrap.test.ts` | One shell/resource preparation per build, zero per-question HTML fetches; exact authorized DTO bootstrap; logout/account switch and in-flight invalidation; wrong assignment/build rejected; offline entered-attempt recovery; readonly null-visit DTO cannot unlock entry; network-only mutation; old cache invalidation retains IndexedDB facts; failed resource preparation does not unlock. |
| Existing capture-v2, initialization and submission-repair E2E | Real browser IndexedDB upgrade/reload, offline/reconnect, lease contention, lost navigation and submission response, old-intent recovery, timing sequence and mobile/real200% zoom. Local unit tests alone cannot establish these browser boundaries. |

The six initial API acceptance cases live in `src/assessment-runtime/stage-c.contract.test.ts`. Current Stage B implementation produces five expected failures (readonly URL still contains reason query and `enterQuestion` does not exist), with one nullable-DTO preservation case passing. This is a recorded red baseline, not a regression fix or Stage C completion. Existing Stage B tests are not changed to hide failures. Broader controller/store/worker tests follow the frozen server contract and the release gate; no placeholder implementation modules are added during preparation.

## Concrete risks and release criteria

The largest preservation risk is deletion or double sending of intent-owned evidence when the sender and atomic save/submission race. The largest lifecycle risk is one session controller continuing to emit facts for a previous attempt after DTO replacement. A shell that carries an old user bootstrap or receives a readonly null-visit DTO can otherwise reopen an unauthorized attempt. These require tests with deliberately interleaved asynchronous work, real browser storage, and lost HTTP responses; happy-path navigation is insufficient.

An old frozen navigation intent does not contain every new entry clock. Preserve and confirm it as historical material; never fill missing clocks with invented values. A stable entry receipt can be replayed while deadline/task DTO facts change, so state installation requires current authorization rather than an assumption of identical DTO bodies. Direct opening and refresh require a fresh intent only after any older pending entry has been resolved.

Stage C is complete only when the global collector bridge is retired for active client paths, one session controller owns all facts and sends, legacy raw records and intents remain recoverable, versioned shell plus authorized DTO replaces question-HTML preparation, server entry/leave/submission receipts agree, targeted tests and frontend gates pass, and real browser regression plus the approved diagnostic/load gate passes. Faster navigation alone does not meet this criterion.

## Implemented lifecycle and recovery order

The first controller activation binds physical page listeners, records initial pageshow/visibility/focus facts, and awaits the resume checkpoint attempt. These initial facts retain null time-sync identity. Clock synchronization starts in the background afterward, then repeats only every 15 minutes or on a real online event. SPA assignment changes do not rebind page listeners, synthesize page lifecycle facts, or recreate resume checkpoints. Every interactive presentation still records item_rendered. Existing attempt submit/finish close behavior retains item_left and the historically named session_end checkpoint; it is not redefined as a whole-exam-only event.

Entry metadata also records the source assignment and authoritative destination URL for crash recovery. While leave is uncertain, reloading the source replays its exact confirmed entry without consuming the frozen destination material, and keeps interaction locked until the original save-and-leave snapshot is confirmed. After leave confirmation is persisted, a source reload resumes the destination directly with its original entry material. An arbitrary new destination cannot replace an unknown entry. Worker preparation verifies current authentication independently on every question and caches only the generic versioned shell plus authorized DTOs; current network authorization is not inferred from page-supplied identity.
