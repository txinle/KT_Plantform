import type { RouteRecordRaw } from 'vue-router'

const TeacherAccountManagementLayout = () => import('./TeacherAccountManagementLayout.vue')
const TeacherClassesLayout = () => import('@/features/teacher-classes/TeacherClassesLayout.vue')

export const loadTeacherClassManagement = () => import('./TeacherClassManagement.vue')

export const teacherAccountManagementRoutes: RouteRecordRaw[] = [
  {
    path: '/teacher/accounts/',
    component: TeacherAccountManagementLayout,
    children: [
      {
        path: 'classes/',
        name: 'teacher-account-classes',
        component: () => import('./TeacherAccountClassList.vue'),
      },
      {
        path: 'students/',
        name: 'teacher-account-students',
        component: () => import('./TeacherAccountStudentList.vue'),
      },
      {
        path: 'students/import/',
        name: 'teacher-account-student-import',
        component: () => import('./TeacherAccountStudentImport.vue'),
      },
    ],
  },
  {
    path: '/teacher/courses/:courseId/classes/:classId/edit/',
    component: TeacherClassesLayout,
    children: [
      {
        path: '',
        name: 'teacher-course-class-management-edit',
        component: loadTeacherClassManagement,
      },
    ],
  },
  {
    path: '/teacher/courses/:courseId/classes/:classId/students/add/',
    component: TeacherClassesLayout,
    children: [
      {
        path: '',
        name: 'teacher-course-class-student-add',
        component: loadTeacherClassManagement,
        meta: { workflow: 'manual' },
      },
    ],
  },
  {
    path: '/teacher/courses/:courseId/classes/:classId/students/add/manual/',
    component: TeacherClassesLayout,
    children: [
      {
        path: '',
        name: 'teacher-course-class-student-add-manual',
        component: loadTeacherClassManagement,
        meta: { workflow: 'manual' },
      },
    ],
  },
  {
    path: '/teacher/courses/:courseId/classes/:classId/students/copy/',
    component: TeacherClassesLayout,
    children: [
      {
        path: '',
        name: 'teacher-course-class-student-copy',
        component: loadTeacherClassManagement,
        meta: { workflow: 'copy' },
      },
    ],
  },
  {
    path: '/teacher/courses/:courseId/classes/:classId/students/import/',
    component: TeacherClassesLayout,
    children: [
      {
        path: '',
        name: 'teacher-course-class-student-import',
        component: loadTeacherClassManagement,
        meta: { workflow: 'import' },
      },
    ],
  },
  {
    path: '/teacher/courses/:courseId/classes/:classId/student-visibility/',
    component: TeacherClassesLayout,
    children: [
      {
        path: '',
        name: 'teacher-course-class-student-visibility',
        component: () => import('./TeacherClassVisibilitySettings.vue'),
      },
    ],
  },
]
