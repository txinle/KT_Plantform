import { defineConfig } from '@playwright/test'
import base from './experiment-visual.config'

export default defineConfig(base, {
  testMatch: 'question-readiness.spec.ts',
  outputDir: '../.e2e-results/question-readiness',
  use: { ...base.use, baseURL: 'http://127.0.0.1:8011' },
})
