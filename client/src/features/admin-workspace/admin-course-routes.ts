import type { RouteRecordRaw } from 'vue-router'

export const adminCourseRoutes: RouteRecordRaw[] = [
  {
    path: '/manage/courses/:courseId/settings/',
    name: 'admin-course-settings',
    component: () => import('@/features/teacher-courses/TeacherCourseSettings.vue'),
  },
]
