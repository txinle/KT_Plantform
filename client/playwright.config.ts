import { fileURLToPath } from 'node:url'
import { defineConfig } from '@playwright/test'

import { requireIsolatedPostgres } from './scripts/e2e-database.mjs'

const gatewayUrl = 'http://127.0.0.1:8000'
const databaseEnvironment = requireIsolatedPostgres(process.env)
const commonDjangoEnvironment = {
  ...process.env,
  ...databaseEnvironment,
  DJANGO_DEBUG: '1',
  E2E_GATEWAY_PORT: '8000',
  E2E_PUBLICATION_WORKER: '1',
  CLASS_CENTER_ENABLED: '1',
  STUDENT_LEARNING_ANALYTICS_ENABLED: '1',
  CAPTURE_V2_ENABLED: '1',
  CAPTURE_V2_COURSE_IDS: '*',
  ASSESSMENT_PREPARED_ADMISSION_MODE: 'postgres_transaction',
  ASSESSMENT_ADMISSION_ENABLED: '1',
  ASSESSMENT_ADMISSION_GLOBAL_ENABLED: '1',
  ASSESSMENT_ADMISSION_CAPACITY: '4',
}

export default defineConfig({
  testDir: './e2e',
  testMatch: '**/*.spec.ts',
  testIgnore: ['capture-v2.spec.ts', 'capture-initialization.spec.ts', 'submission-repair.spec.ts'],
  outputDir: '.e2e-results',
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: gatewayUrl,
    viewport: { width: 1440, height: 900 },
    trace: 'retain-on-failure',
  },
  projects: [
    { name: 'chromium', use: { browserName: 'chromium' } },
    { name: 'firefox', use: { browserName: 'firefox' } },
    { name: 'webkit', use: { browserName: 'webkit' } },
  ],
  webServer: [
    {
      command: 'node scripts/run-e2e-backend.mjs', cwd: fileURLToPath(new URL('.', import.meta.url)),
      url: `${gatewayUrl}/health/`,
      env: commonDjangoEnvironment,
      reuseExistingServer: false,
    },
  ],
})
