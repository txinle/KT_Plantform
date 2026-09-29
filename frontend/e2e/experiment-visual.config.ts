import { defineConfig } from '@playwright/test'
import { requireIsolatedPostgres } from '../scripts/e2e-database.mjs'

const vitePort = Number(process.env.E2E_VITE_PORT || '5173')
const viteUrl = `http://127.0.0.1:${vitePort}`
const environment = {
  ...process.env,
  ...requireIsolatedPostgres(process.env),
  DJANGO_DEBUG: '1',
  VITE_DEV_SERVER_URL: viteUrl,
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
      command: 'node ../scripts/run-e2e-backend.mjs',
      url: 'http://127.0.0.1:8000/health/',
      env: environment,
      reuseExistingServer: false,
    },
  ],
})
