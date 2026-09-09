import { defineConfig } from '@playwright/test'
import { existsSync } from 'node:fs'
import { join } from 'node:path'

import { requireIsolatedPostgres } from './scripts/e2e-database.mjs'

const repositoryDir = join(import.meta.dirname, '..')
const localPython = process.platform === 'win32'
  ? join(repositoryDir, '.venv', 'Scripts', 'python.exe')
  : join(repositoryDir, '.venv', 'bin', 'python')
const python = process.env.PYTHON || (existsSync(localPython) ? localPython : 'python')
const pythonCommand = JSON.stringify(python)
const vitePort = Number(process.env.E2E_VITE_PORT || '5173')
const viteUrl = `http://127.0.0.1:${vitePort}`
const djangoUrl = 'http://127.0.0.1:8000'
const databaseEnvironment = requireIsolatedPostgres(process.env)
const commonDjangoEnvironment = {
  ...process.env,
  ...databaseEnvironment,
  DJANGO_DEBUG: '1',
  VITE_DEV_SERVER_URL: viteUrl,
  CLASS_CENTER_ENABLED: '1',
  STUDENT_LEARNING_ANALYTICS_ENABLED: '1',
  CAPTURE_V2_ENABLED: '1',
  CAPTURE_V2_USER_IDS: '4',
  CAPTURE_V2_COURSE_IDS: '1,2',
}

export default defineConfig({
  testDir: './e2e',
  testIgnore: ['capture-v2.spec.ts', 'capture-initialization.spec.ts', 'submission-repair.spec.ts'],
  outputDir: '.e2e-results',
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: djangoUrl,
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
      command: `npm run dev -- --host 127.0.0.1 --port ${vitePort}`,
      url: viteUrl,
      reuseExistingServer: false,
    },
    {
      command: `${pythonCommand} ../manage.py runserver 127.0.0.1:8000 --noreload`,
      url: `${djangoUrl}/login/`,
      env: commonDjangoEnvironment,
      reuseExistingServer: false,
    },
  ],
})
