import { defineConfig } from '@playwright/test'
import capture from './playwright.capture-v2.config'

export default defineConfig({
  ...capture,
  testMatch: 'ui-layout.spec.ts',
  outputDir: '.e2e-results/ui-layout',
  webServer: {
    ...capture.webServer as object,
    reuseExistingServer: false,
    env: {
      ...(capture.webServer as { env: Record<string, string> }).env,
      VUE_TEACHER_COURSES_ENABLED: '1',
      VUE_TEACHER_QUESTION_BANKS_ENABLED: '1',
      VUE_TEACHER_CLASSES_ENABLED: '1',
      VUE_TEACHER_DATA_CENTER_ENABLED: '1',
    },
  },
})
