import { defineConfig } from '@playwright/test'
import { existsSync } from 'node:fs'
import { join } from 'node:path'

import { requireIsolatedPostgres } from './scripts/e2e-database.mjs'

const repositoryDir = join(import.meta.dirname, '..')
const localPython = process.platform === 'win32'
  ? join(repositoryDir, '.venv', 'Scripts', 'python.exe')
  : join(repositoryDir, '.venv', 'bin', 'python')
const python = process.env.PYTHON || (existsSync(localPython) ? localPython : 'python')
const pythonCommand = JSON.stringify(python)
const vitePort = Number(process.env.E2E_VITE_PORT || '5173')
const viteUrl = `http://127.0.0.1:${vitePort}`
const defaultDjangoUrl = 'http://127.0.0.1:8000'
const databaseEnvironment = requireIsolatedPostgres(process.env)
const commonDjangoEnvironment = {
  ...process.env,
  ...databaseEnvironment,
  DJANGO_DEBUG: '1',
  VITE_DEV_SERVER_URL: viteUrl,
}

export default defineConfig({
  testDir: './e2e',
  outputDir: '.e2e-results',
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: defaultDjangoUrl,
    viewport: { width: 1440, height: 900 },
    trace: 'retain-on-failure',
  },
  projects: [
    { name: 'chromium', use: { browserName: 'chromium' } },
    { name: 'firefox', use: { browserName: 'firefox' } },
    { name: 'webkit', use: { browserName: 'webkit' } },
  ],
  webServer: [
    {
      command: `npm run dev -- --host 127.0.0.1 --port ${vitePort}`,
      url: viteUrl,
      reuseExistingServer: false,
    },
    {
      command: `${pythonCommand} ../manage.py runserver 127.0.0.1:8000 --noreload`,
      url: `${defaultDjangoUrl}/health/`,
      env: {
        ...commonDjangoEnvironment,
        VUE_STANDALONE_ENABLED: '1',
        VUE_STUDENT_ASSESSMENT_ENABLED: '1',
        VUE_STUDENT_COURSES_ENABLED: '1',
        VUE_TEACHER_COURSES_ENABLED: '1',
        VUE_TEACHER_EXPERIMENTS_ENABLED: '1',
        VUE_TEACHER_QUESTION_BANKS_ENABLED: '1',
        VUE_TEACHER_CLASSES_ENABLED: '1',
        VUE_TEACHER_DATA_CENTER_ENABLED: '1',
        CLASS_CENTER_ENABLED: '1',
        STUDENT_LEARNING_ANALYTICS_ENABLED: '1',
        CAPTURE_V2_ENABLED: '1',
        CAPTURE_V2_USER_IDS: '4',
        CAPTURE_V2_COURSE_IDS: '1,2',
      },
      reuseExistingServer: false,
    },
    {
      command: `${pythonCommand} ../manage.py runserver 127.0.0.1:8010 --noreload`,
      url: 'http://127.0.0.1:8010/health/',
      env: {
        ...commonDjangoEnvironment,
        VUE_TEACHER_EXPERIMENTS_ENABLED: '0',
      },
      reuseExistingServer: false,
    },
    {
      command: `${pythonCommand} ../manage.py runserver 127.0.0.1:8011 --noreload`,
      url: 'http://127.0.0.1:8011/health/',
      env: {
        ...commonDjangoEnvironment,
        VUE_TEACHER_EXPERIMENTS_ENABLED: '1',
      },
      reuseExistingServer: false,
    },
    {
      command: `${pythonCommand} ../manage.py runserver 127.0.0.1:8012 --noreload`,
      url: 'http://127.0.0.1:8012/health/',
      env: {
        ...commonDjangoEnvironment,
        VUE_TEACHER_EXPERIMENTS_ENABLED: '1',
        VUE_TEACHER_EXPERIMENTS_USER_IDS: '2',
      },
      reuseExistingServer: false,
    },
    {
      command: `${pythonCommand} ../manage.py runserver 127.0.0.1:8013 --noreload`,
      url: 'http://127.0.0.1:8013/health/',
      env: {
        ...commonDjangoEnvironment,
        CLASS_CENTER_ENABLED: '1',
        STUDENT_LEARNING_ANALYTICS_ENABLED: '1',
        VUE_STUDENT_COURSES_ENABLED: '0',
      },
      reuseExistingServer: false,
    },
    {
      command: `${pythonCommand} ../manage.py runserver 127.0.0.1:8014 --noreload`,
      url: 'http://127.0.0.1:8014/health/',
      env: {
        ...commonDjangoEnvironment,
        CLASS_CENTER_ENABLED: '1',
        STUDENT_LEARNING_ANALYTICS_ENABLED: '1',
        VUE_STUDENT_COURSES_ENABLED: '1',
        VUE_STUDENT_ASSESSMENT_ENABLED: '1',
      },
      reuseExistingServer: false,
    },
    {
      command: `${pythonCommand} ../manage.py runserver 127.0.0.1:8015 --noreload`,
      url: 'http://127.0.0.1:8015/health/',
      env: {
        ...commonDjangoEnvironment,
        CLASS_CENTER_ENABLED: '1',
        STUDENT_LEARNING_ANALYTICS_ENABLED: '1',
        VUE_STUDENT_COURSES_ENABLED: '1',
        VUE_STUDENT_COURSES_USER_IDS: '3',
      },
      reuseExistingServer: false,
    },
    {
      command: `${pythonCommand} ../manage.py runserver 127.0.0.1:8016 --noreload`,
      url: 'http://127.0.0.1:8016/health/',
      env: {
        ...commonDjangoEnvironment,
        CLASS_CENTER_ENABLED: '1',
        VUE_TEACHER_CLASSES_ENABLED: '1',
      },
      reuseExistingServer: false,
    },
    {
      command: `${pythonCommand} ../manage.py runserver 127.0.0.1:8017 --noreload`,
      url: 'http://127.0.0.1:8017/health/',
      env: {
        ...commonDjangoEnvironment,
        VUE_TEACHER_QUESTION_BANKS_ENABLED: '1',
      },
      reuseExistingServer: false,
    },
    {
      command: `${pythonCommand} ../manage.py runserver 127.0.0.1:8018 --noreload`,
      url: 'http://127.0.0.1:8018/health/',
      env: {
        ...commonDjangoEnvironment,
        VUE_TEACHER_DATA_CENTER_ENABLED: '1',
      },
      reuseExistingServer: false,
    },
  ],
})
