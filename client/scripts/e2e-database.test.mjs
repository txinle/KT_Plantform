import assert from 'node:assert/strict'
import test from 'node:test'

import { requireIsolatedPostgres } from './e2e-database.mjs'

const valid = {
  POSTGRES_HOST: '127.0.0.1',
  POSTGRES_PORT: '55432',
  POSTGRES_USER: 'kt_user',
  POSTGRES_PASSWORD: 'kt_password',
  POSTGRES_DB: 'kt_e2e',
}

test('requires the project PostgreSQL E2E database', () => {
  assert.deepEqual(requireIsolatedPostgres(valid), valid)
})

test('refuses a missing PostgreSQL environment instead of falling back to SQLite', () => {
  assert.throws(() => requireIsolatedPostgres({}), /PostgreSQL E2E environment/)
})

test('refuses to flush any database except kt_e2e', () => {
  assert.throws(
    () => requireIsolatedPostgres({ ...valid, POSTGRES_DB: 'kt_platform' }),
    /refusing destructive E2E setup/,
  )
})
