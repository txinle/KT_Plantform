import type { RouteRecordRaw } from 'vue-router'

const TeacherDataCenterLayout = () => import('@/features/teacher-data-center/TeacherDataCenterLayout.vue')
const TeacherTeachingData = () => import('./TeacherTeachingData.vue')

export const teacherTeachingDataRoutes: RouteRecordRaw[] = [
  {
    path: '/teacher/courses/:courseId/data/teaching/',
    component: TeacherDataCenterLayout,
    children: [
      { path: '', name: 'teacher-data-teaching', component: TeacherTeachingData },
    ],
  },
]
