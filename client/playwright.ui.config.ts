import { defineConfig } from '@playwright/test'
import capture from './playwright.capture-v2.config'

export default defineConfig({
  ...capture,
  testMatch: 'ui-layout.spec.ts',
  outputDir: '.e2e-results/ui-layout',
})
