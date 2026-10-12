import { defineConfig } from '@playwright/test'
import base from './experiment-visual.config'

export default defineConfig(base, {
  testMatch: 'student-identity-password.spec.ts',
  outputDir: '../.e2e-results/student-identity-password',
  use: { ...base.use, baseURL: 'http://127.0.0.1:8000' },
})
