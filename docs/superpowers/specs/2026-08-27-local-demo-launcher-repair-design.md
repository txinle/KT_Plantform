# Reliable Demo Login Launcher Design

## Goal

Make a double-click of `run_local.bat` start the local Django site, ensure the documented demo accounts exist, and open the ready site at `http://127.0.0.1:8000/`.

## Root cause

The launcher currently treats the existence of `db.sqlite3` as proof that demo data has been initialized. A migrated but empty database therefore skips `seed_demo`, leaving the login page credentials as documentation only. The browser helper also needs to be tied to the documented 8000 URL and to server readiness.

## Design

1. Keep the existing database and user records. Before launching, query the three required demo usernames (`admin`, `teacher`, `S001`) with a small Django management command or equivalent check.
2. Run the idempotent `seed_demo` command only when the required demo accounts are missing or unusable. Existing accounts are not deleted; `seed_demo` may reset the documented demo passwords as its current contract already does.
3. Keep dependency, migration, and seed failures visible in the batch window. Do not open a browser after a failed setup step.
4. Start Django on port 8000 and let a bounded readiness helper open the browser only after an HTTP response is available.
5. Add a regression test for an existing empty database and a smoke check that the seeded credentials authenticate.

## Non-goals

- No destructive database reset.
- No change to authentication semantics or production deployment.
- No automatic password reset for non-demo users.
