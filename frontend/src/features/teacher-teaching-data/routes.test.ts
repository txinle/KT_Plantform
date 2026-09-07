import { createMemoryHistory, createRouter } from 'vue-router'
import { describe, expect, it } from 'vitest'

import { migratedRoutes } from '@/app/route-boundaries'

import { teacherTeachingDataRoutes } from './routes'

describe('teacher teaching-data routes', () => {
  it('uses the existing data workspace layout once at the legacy teaching URL', () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: teacherTeachingDataRoutes,
    })

    const resolved = router.resolve('/teacher/courses/7/data/teaching/?experiment_id=3&class_id=4')

    expect(resolved.name).toBe('teacher-data-teaching')
    expect(resolved.matched).toHaveLength(2)
  })

  it('wins over the data-center base route without changing the overview route', () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: migratedRoutes,
    })

    expect(router.resolve('/teacher/courses/7/data/teaching/').name).toBe('teacher-data-teaching')
    expect(router.resolve('/teacher/courses/7/data/').name).toBe('teacher-data-overview')
  })
})
