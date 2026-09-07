import { createMemoryHistory, createRouter } from 'vue-router'
import { describe, expect, it } from 'vitest'

import { teacherStandaloneExperimentRoutes } from './standalone-routes'

describe('standalone experiment routes', () => {
  it('keeps every independent experiment page on its established global teacher URL', () => {
    expect(teacherStandaloneExperimentRoutes).toHaveLength(1)
    expect(teacherStandaloneExperimentRoutes[0].path).toBe('/teacher/experiments/')
    expect(teacherStandaloneExperimentRoutes[0].children?.map((route) => route.path)).toEqual([
      '',
      'create/',
      ':experimentId/',
      ':experimentId/edit/',
      ':experimentId/results/',
    ])

    const router = createRouter({
      history: createMemoryHistory(),
      routes: teacherStandaloneExperimentRoutes,
    })
    expect(router.resolve('/teacher/experiments/').name).toBe('teacher-standalone-experiments')
    expect(router.resolve('/teacher/experiments/create/').name).toBe('teacher-standalone-experiment-create')
    expect(router.resolve('/teacher/experiments/17/edit/').name).toBe('teacher-standalone-experiment-edit')
    expect(router.resolve('/teacher/experiments/17/results/').name).toBe('teacher-standalone-experiment-results')
  })
})
