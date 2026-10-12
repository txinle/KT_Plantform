import { createMemoryHistory, createRouter } from 'vue-router'
import { describe, expect, it } from 'vitest'

import { migratedRoutes } from '@/app/route-boundaries'
import { teacherAnalysisRoutes } from './routes'


describe('teacher analysis routes', () => {
  it('keeps each class drilldown on its established business URL', () => {
    expect(teacherAnalysisRoutes.map((route) => route.path)).toEqual([
      '/teacher/courses/:courseId/classes/:classId/students/:studentId/analysis/',
      '/teacher/courses/:courseId/classes/:classId/students/:studentId/experiments/:experimentId/',
      '/teacher/courses/:courseId/classes/:classId/experiments/:experimentId/questions/:questionId/',
      '/teacher/courses/:courseId/classes/:classId/experiments/:experimentId/questions/:questionId/students/:studentId/process/',
      '/teacher/courses/:courseId/experiments/:experimentId/results/',
      '/teacher/courses/:courseId/experiments/:experimentId/sessions/:sessionId/',
    ])
    expect(teacherAnalysisRoutes.every((route) => route.children?.[0]?.path === '')).toBe(true)
  })

  it('keeps class-center and experiment-detail base routes regardless of route-array order', () => {
    const baseRoutes = migratedRoutes.filter((route) => !teacherAnalysisRoutes.includes(route))

    for (const routes of [
      [...teacherAnalysisRoutes, ...baseRoutes],
      [...baseRoutes, ...teacherAnalysisRoutes],
    ]) {
      const router = createRouter({ history: createMemoryHistory(), routes })

      expect(router.resolve('/teacher/courses/7/classes/11/').name).toBe('teacher-class-center')
      expect(router.resolve('/teacher/courses/7/experiments/41/').name).toBe('teacher-experiment-detail')
      const drilldown = router.resolve('/teacher/courses/7/classes/11/students/23/analysis/')
      expect(drilldown.name).toBe('teacher-class-student-analysis')
      expect(drilldown.matched).toHaveLength(2)
      expect(router.resolve('/teacher/courses/7/experiments/41/results/').name).toBe(
        'teacher-experiment-results-compatibility',
      )
    }
  })
})
