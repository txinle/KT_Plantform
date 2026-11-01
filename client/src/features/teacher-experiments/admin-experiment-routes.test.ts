import { createMemoryHistory, createRouter } from 'vue-router'
import { describe, expect, it } from 'vitest'
import { adminExperimentRoutes } from './admin-experiment-routes'

describe('administrator experiment page registration', () => {
  it('resolves every reused workflow within the administrator domain', () => {
    const router = createRouter({ history: createMemoryHistory(), routes: adminExperimentRoutes })
    for (const path of [
      '/manage/courses/2/experiments/', '/manage/courses/2/experiments/create/',
      '/manage/courses/2/experiments/8/', '/manage/courses/2/experiments/8/edit/',
      '/manage/courses/2/experiments/8/class-tasks/4/grading/',
      '/manage/courses/2/experiments/8/publish-plan/3/step/2/',
      '/manage/courses/2/experiments/8/student-preview/',
      '/manage/experiments/', '/manage/experiments/create/', '/manage/experiments/8/',
      '/manage/experiments/8/edit/', '/manage/experiments/8/results/',
    ]) expect(String(router.resolve(path).name)).toMatch(/^admin-/)
  })
})
