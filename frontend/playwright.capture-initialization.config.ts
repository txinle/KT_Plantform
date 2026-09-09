import { defineConfig } from '@playwright/test'
import { existsSync } from 'node:fs'
import { join } from 'node:path'

import { requireIsolatedPostgres } from './scripts/e2e-database.mjs'

const root = join(import.meta.dirname, '..')
const localPython = join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
const python = process.env.PYTHON || (existsSync(localPython) ? localPython : 'python')
const externalBaseURL = process.env.E2E_BASE_URL?.replace(/\/$/, '')
const baseURL = externalBaseURL || 'http://127.0.0.1:8124'
const webkitProxyPort = process.env.E2E_WEBKIT_PROXY_PORT || (externalBaseURL ? '18081' : '8125')
const webkitBaseURL = new URL(baseURL)
webkitBaseURL.hostname = '127.0.0.1'
webkitBaseURL.port = webkitProxyPort

export default defineConfig({
  testDir: './e2e',
  testMatch: 'capture-initialization.spec.ts',
  workers: 1,
  outputDir: '.e2e-results/capture-initialization',
  reporter: 'list',
  use: {
    baseURL,
    actionTimeout: 15_000,
    navigationTimeout: 30_000,
    viewport: { width: 1440, height: 900 },
    trace: 'retain-on-failure',
  },
  projects: [
    { name: 'chromium', use: { browserName: 'chromium' } },
    { name: 'webkit', use: { browserName: 'webkit', baseURL: webkitBaseURL.href.replace(/\/$/, '') } },
  ],
  ...(externalBaseURL ? {} : {
    webServer: {
      command: `"${python}" ../manage.py runserver 127.0.0.1:8124 --noreload`,
      url: `${baseURL}/health/`,
      reuseExistingServer: false,
      env: {
        ...process.env,
        ...requireIsolatedPostgres(process.env),
        VITE_DEV_SERVER_URL: '',
        CLASS_CENTER_ENABLED: '1',
        STUDENT_LEARNING_ANALYTICS_ENABLED: '1',
        CAPTURE_V2_ENABLED: '1',
        CAPTURE_V2_COURSE_IDS: '*',
      },
    },
  }),
})
