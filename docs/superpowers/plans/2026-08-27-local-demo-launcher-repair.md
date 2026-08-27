# Reliable Demo Login Launcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `run_local.bat` seed missing demo accounts and open the ready local site on port 8000 after a double-click.

**Architecture:** Add a narrowly scoped Django management command that reports whether the documented demo accounts are usable; the batch launcher calls `seed_demo` only when that check fails, then uses its existing bounded readiness helper. Existing records and database files are preserved.

**Tech Stack:** Django 5.2 management commands, Python unittest/Django TestCase, Windows batch and PowerShell.

---

### Task 1: Add a failing demo-account readiness test

**Files:**
- Create: `apps/accounts/tests_demo_readiness.py`
- Create: `apps/accounts/management/commands/check_demo_accounts.py`

- [ ] **Step 1: Write the failing test**

Create a Django test that starts with an empty database, calls `check_demo_accounts`, and expects a non-zero command failure until the command is implemented. Add a second test that creates the three users with the documented passwords and expects success.

- [ ] **Step 2: Run the focused test and verify the expected failure**

Run `\.venv\Scripts\python.exe manage.py test apps.accounts.tests_demo_readiness -v 2`.
Expected: failure because `check_demo_accounts` does not yet exist.

- [ ] **Step 3: Implement the minimal command**

Implement `check_demo_accounts` to inspect `admin`, `teacher`, and `S001`; print a concise missing/unusable list; exit with status 1 when any is absent, inactive, or has the documented password mismatch, and status 0 otherwise. Do not mutate the database.

- [ ] **Step 4: Run the focused test and verify it passes**

Run the same command. Expected: both tests pass.

### Task 2: Make the Windows launcher self-healing and readiness-safe

**Files:**
- Modify: `run_local.bat`
- Modify: `apps/accounts/tests_demo_readiness.py`

- [ ] **Step 1: Add a launcher contract regression test**

Assert that `run_local.bat` invokes `check_demo_accounts`, conditionally invokes `seed_demo`, preserves the documented `APP_URL`, and retains the readiness polling and `KT_SKIP_BROWSER` switch.

- [ ] **Step 2: Run the contract test and verify it fails against the current launcher**

Run the focused test. Expected: failure identifying the missing readiness check/conditional seed logic.

- [ ] **Step 3: Implement the minimal batch change**

After migrations, call `check_demo_accounts`; if it returns non-zero, call `seed_demo`, then call `check_demo_accounts` again and stop on failure. Leave the existing port guard and bounded browser helper intact, with the URL fixed at `http://127.0.0.1:8000/`.

- [ ] **Step 4: Run the focused tests and verify they pass**

Run `\.venv\Scripts\python.exe manage.py test apps.accounts.tests_demo_readiness -v 2`. Expected: all focused tests pass.

### Task 3: Verify the real double-click path

**Files:**
- No source changes.

- [ ] **Step 1: Run Django checks**

Run `\.venv\Scripts\python.exe manage.py check` and `\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run`. Expected: no issues and no pending migrations.

- [ ] **Step 2: Run the full Django suite**

Run `\.venv\Scripts\python.exe manage.py test`. Expected: all tests pass.

- [ ] **Step 3: Exercise startup without opening a browser**

Set `$env:KT_SKIP_BROWSER='1'`, run `\.\run_local.bat`, and verify `Invoke-WebRequest http://127.0.0.1:8000/login/` returns HTTP 200 and the three documented credentials authenticate. Stop only the exact launcher process, then clear the environment variable.

- [ ] **Step 4: Review and commit**

Run `git diff --check` and inspect the focused diff. Commit with `fix: make double-click launcher seed demo accounts`.
