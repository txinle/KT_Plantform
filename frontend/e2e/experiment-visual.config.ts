import { defineConfig } from '@playwright/test'
import { join } from 'node:path'

import { requireIsolatedPostgres } from '../scripts/e2e-database.mjs'

const repositoryDir = join(import.meta.dirname, '..', '..')
const python = join(repositoryDir, '.venv', 'Scripts', 'python.exe')
const vitePort = Number(process.env.E2E_VITE_PORT || '5173')
const viteUrl = `http://127.0.0.1:${vitePort}`
const environment = {
  ...process.env,
  ...requireIsolatedPostgres(process.env),
  DJANGO_DEBUG: '1',
  VITE_DEV_SERVER_URL: viteUrl,
  VUE_TEACHER_EXPERIMENTS_ENABLED: '1',
}

export default defineConfig({
  testDir: '.',
  testMatch: 'experiment-visual.spec.ts',
  outputDir: '../.e2e-results/experiment-visual',
  workers: 1,
  reporter: [['list']],
  use: {
    viewport: { width: 1586, height: 992 },
    trace: 'retain-on-failure',
  },
  projects: [{ name: 'chromium', use: { browserName: 'chromium' } }],
  webServer: [
    {
      command: `npm run dev -- --host 127.0.0.1 --port ${vitePort}`,
      url: viteUrl,
      reuseExistingServer: false,
    },
    {
      command: `"${python}" manage.py runserver 127.0.0.1:8011 --noreload`,
      cwd: repositoryDir,
      url: 'http://127.0.0.1:8011/health/',
      env: environment,
      reuseExistingServer: false,
    },
  ],
})
