import { defineConfig } from '@playwright/test'
import { join } from 'node:path'
import { requireIsolatedPostgres } from './scripts/e2e-database.mjs'

const root = join(import.meta.dirname, '..')
const python = join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
export default defineConfig({
  testDir: './e2e', testMatch: 'capture-v2.spec.ts', workers: 1,
  outputDir: '.e2e-results/capture-v2', reporter: 'list',
  use: { baseURL: 'http://127.0.0.1:8124', actionTimeout: 15000, navigationTimeout: 20000, viewport: { width: 1440, height: 900 }, trace: 'retain-on-failure' },
  webServer: { command: `"${python}" ../manage.py runserver 127.0.0.1:8124 --noreload`,
    url: 'http://127.0.0.1:8124/health/', reuseExistingServer: true,
    env: { ...process.env, ...requireIsolatedPostgres(process.env), VITE_DEV_SERVER_URL: '', VUE_STANDALONE_ENABLED: '1',
      VUE_STUDENT_ASSESSMENT_ENABLED: '1', VUE_STUDENT_COURSES_ENABLED: '1',
      VUE_TEACHER_EXPERIMENTS_ENABLED: '1', CLASS_CENTER_ENABLED: '1', STUDENT_LEARNING_ANALYTICS_ENABLED: '1',
      CAPTURE_V2_ENABLED: '1', CAPTURE_V2_USER_IDS: '4', CAPTURE_V2_COURSE_IDS: '1,2' } },
})
