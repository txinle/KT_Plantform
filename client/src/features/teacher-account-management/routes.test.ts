import { createMemoryHistory, createRouter } from 'vue-router'
import { describe, expect, it } from 'vitest'

import { migratedRoutes } from '@/app/route-boundaries'

import { teacherAccountManagementRoutes } from './routes'

describe('teacher account-management routes', () => {
  it('keeps global account pages beneath their workspace layout', () => {
    const accounts = teacherAccountManagementRoutes.find((route) => route.path === '/teacher/accounts/')

    expect(accounts?.children?.map((route) => route.name)).toEqual([
      'teacher-account-classes',
      'teacher-account-students',
      'teacher-account-student-import',
    ])
  })

  it('uses the existing class workspace layout once for management deep links', () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: teacherAccountManagementRoutes,
    })

    const resolved = router.resolve('/teacher/courses/7/classes/11/students/import/')

    expect(resolved.name).toBe('teacher-course-class-student-import')
    expect(resolved.matched).toHaveLength(2)
  })

  it('leaves the existing class-center route available at the class base URL', () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: migratedRoutes,
    })

    const resolved = router.resolve('/teacher/courses/7/classes/11/?section=manage')

    expect(resolved.name).toBe('teacher-class-center')
  })
})
