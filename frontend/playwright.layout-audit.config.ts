import { defineConfig } from '@playwright/test'
import capture from './playwright.capture-v2.config'
export default defineConfig(capture, { testMatch: 'layout-audit.spec.ts', outputDir: '.e2e-results/layout-audit' })
