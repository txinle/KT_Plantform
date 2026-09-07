import { defineConfig } from '@playwright/test'

import layoutAudit from './playwright.layout-audit.config'

export default defineConfig(layoutAudit, {
  testMatch: 'layout-expanded-audit.spec.ts',
  outputDir: '.e2e-results/layout-expanded',
})
