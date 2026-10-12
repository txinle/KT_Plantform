import { defineConfig } from '@playwright/test'
import base from './question-readiness.config'

export default defineConfig(base, {
  testMatch: 'course-kc-lifecycle.spec.ts',
  outputDir: '../.e2e-results/course-kc-lifecycle',
})
