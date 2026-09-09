import { defineConfig } from '@playwright/test'
import { existsSync } from 'node:fs'
import { join } from 'node:path'
import { requireIsolatedPostgres } from './scripts/e2e-database.mjs'

const root = join(import.meta.dirname, '..')
const localPython = join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
const python = process.env.PYTHON || (existsSync(localPython) ? localPython : 'python')
export default defineConfig({
  testDir: './e2e', testMatch: 'submission-repair.spec.ts', workers: 1,
  outputDir: '.e2e-results/submission-repair', reporter: 'list',
  projects: [{ name: 'chromium', use: { browserName: 'chromium' } }, { name: 'firefox', use: { browserName: 'firefox' } }],
  use: { baseURL: 'http://127.0.0.1:8126', actionTimeout: 15000, navigationTimeout: 20000, viewport: { width: 1440, height: 900 }, trace: 'retain-on-failure' },
  webServer: {
    command: `"${python}" ../manage.py runserver 127.0.0.1:8126 --noreload`,
    url: 'http://127.0.0.1:8126/health/', reuseExistingServer: false,
    env: { ...process.env, ...requireIsolatedPostgres(process.env), VITE_DEV_SERVER_URL: '',
      CLASS_CENTER_ENABLED: '1', STUDENT_LEARNING_ANALYTICS_ENABLED: '1', CAPTURE_V2_ENABLED: '1', CAPTURE_V2_COURSE_IDS: '*' },
  },
})
