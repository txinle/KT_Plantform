import { fileURLToPath } from 'node:url'
import { defineConfig } from '@playwright/test'
import { requireIsolatedPostgres } from './scripts/e2e-database.mjs'

export default defineConfig({
  testDir: './e2e', testMatch: 'submission-repair.spec.ts', workers: 1,
  outputDir: '.e2e-results/submission-repair', reporter: 'list',
  projects: [{ name: 'chromium', use: { browserName: 'chromium' } }, { name: 'firefox', use: { browserName: 'firefox' } }],
  use: { baseURL: 'http://127.0.0.1:8126', actionTimeout: 15000, navigationTimeout: 20000, viewport: { width: 1440, height: 900 }, trace: 'retain-on-failure' },
  webServer: {
    command: 'node scripts/run-e2e-backend.mjs', cwd: fileURLToPath(new URL('.', import.meta.url)),
    url: 'http://127.0.0.1:8126/health/', reuseExistingServer: false,
    env: { ...process.env, ...requireIsolatedPostgres(process.env), E2E_GATEWAY_PORT: '8126', E2E_BACKEND_PORT: '18126',
      CLASS_CENTER_ENABLED: '1', STUDENT_LEARNING_ANALYTICS_ENABLED: '1', CAPTURE_V2_ENABLED: '1', CAPTURE_V2_COURSE_IDS: '*' },
  },
})
