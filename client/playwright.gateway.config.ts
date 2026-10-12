import { defineConfig } from '@playwright/test'
import capture from './playwright.capture-v2.config'

export default defineConfig(capture, {
  testMatch: 'frontend-gateway.spec.ts',
  outputDir: '.e2e-results/frontend-gateway',
  projects: [{ name: 'chromium', use: { browserName: 'chromium' } }],
})
