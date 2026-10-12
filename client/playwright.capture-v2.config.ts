import { fileURLToPath } from 'node:url'
import { defineConfig } from '@playwright/test'
import { requireIsolatedPostgres } from './scripts/e2e-database.mjs'

export default defineConfig({
  testDir: './e2e', testMatch: 'capture-v2.spec.ts', workers: 1,
  outputDir: '.e2e-results/capture-v2', reporter: 'list',
  use: { baseURL: 'http://127.0.0.1:8124', actionTimeout: 15000, navigationTimeout: 20000, viewport: { width: 1440, height: 900 }, trace: 'retain-on-failure' },
  webServer: { command: 'node scripts/run-e2e-backend.mjs', cwd: fileURLToPath(new URL('.', import.meta.url)),
    url: 'http://127.0.0.1:8124/health/', reuseExistingServer: false,
    env: { ...process.env, ...requireIsolatedPostgres(process.env), E2E_GATEWAY_PORT: '8124',
      CLASS_CENTER_ENABLED: '1', STUDENT_LEARNING_ANALYTICS_ENABLED: '1',
      CAPTURE_V2_ENABLED: '1', CAPTURE_V2_COURSE_IDS: '*',
      ATOMIC_SUBMISSION_ENABLED: '1', ATOMIC_SUBMISSION_COURSE_IDS: '*',
      SAVE_AND_LEAVE_ENABLED: '1', SAVE_AND_LEAVE_COURSE_IDS: '*' } },
})
