# Offline preparation authentication repair

Scope: prevent authenticated pages from being blocked when a browser worker does not carry the page's session cookie. Preserve account isolation and offline recovery. No schema, dependency, scoring, or deployment changes.

1. Add focused backend and worker regression tests first. Cover cookie-free verification, assignment ownership, tampering, 60-second expiry, logout/password invalidation, transient failure cache retention, and identity-change races.
2. Add `/api/v1/auth/offline-preparation/` (authenticated, CSRF-protected POST) and `/api/v1/auth/offline-preparation/verify/` (POST accepting only a scoped encrypted proof). Reuse cryptography. Encrypt the session key with a purpose-derived server key; never expose a usable session cookie. Verification reloads the original server session and Django user authentication hash. Proof grants only an identity/scope confirmation, never a business write.
3. In `offline-cache.ts`, obtain a fresh proof through the page before sending each preparation message. The worker verifies it without depending on cookies, compares user/session/assignment, and never stores the proof. Keep authenticated legacy messages working for already-open pages. Preserve existing epoch invalidation and authorized DTO checks.
4. Classify authentication-required, unavailable, invalid-proof and account-mismatch failures. Retain cache on temporary errors. Provide re-login for authentication expiry; retry gets a fresh proof. No unlimited retries or new timer infrastructure.
5. Run PostgreSQL backend tests for this API, related offline worker/cache/component tests, touched-file lint, type/build and API contract generation/check. Exercise the browser initialization failure/recovery path with the existing focused witness where applicable. Existing remote CI runs its configured full checks; do not duplicate the full suite locally.
6. Inspect the scoped diff, commit only these files and generated API contracts, push main to origin (GitHub) and gitee, and follow the current commit's CI. Do not deploy production.

Routing prerequisite: register the two new POST paths in the three existing school Nginx pool maps so they use the established authentication pool rather than the default core pool. Verify only these route mappings and existing route-isolation tests.

Acceptance: preparation succeeds when the page is authenticated but worker cookies are absent; expired/logged-out/other-account proofs cannot authorize preparation; temporary failures do not erase previously prepared recovery; repeated preparation uses fresh authority.
