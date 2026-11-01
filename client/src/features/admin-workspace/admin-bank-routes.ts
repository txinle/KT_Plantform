import type { RouteRecordRaw } from 'vue-router'
const shell = () => import('./AdminBankWorkspace.vue')
export const adminBankRoutes: RouteRecordRaw[] = [
  {
    path: '/manage/resources/courses/:courseId/banks/:bankId/', component: shell, props: { active: 'bank' },
    children: [
      { path: '', name: 'admin-course-bank', component: () => import('@/features/teacher-question-banks/TeacherQuestionBankDetail.vue') },
      { path: 'edit/', name: 'admin-course-bank-edit', component: () => import('@/features/teacher-bank-management/TeacherBankEditor.vue') },
      { path: 'import/', name: 'admin-course-bank-import', component: () => import('@/features/teacher-bank-management/TeacherQuestionImport.vue') },
      { path: 'kc-import/', name: 'admin-course-bank-kc-import', component: () => import('@/features/teacher-bank-management/TeacherKCImport.vue') },
      { path: 'questions/:questionId/edit/', name: 'admin-course-bank-question-edit', component: () => import('@/features/teacher-bank-management/TeacherQuestionEditor.vue') },
    ],
  },
  {
    path: '/manage/resources/banks/:bankId/', component: shell, props: { active: 'bank' },
    children: [
      { path: '', name: 'admin-standalone-bank', component: () => import('@/features/teacher-bank-management/TeacherStandaloneBankDetail.vue') },
      { path: 'import/', name: 'admin-standalone-bank-import', component: () => import('@/features/teacher-bank-management/TeacherStandaloneQuestionImport.vue') },
      { path: 'kc-import/', name: 'admin-standalone-bank-kc-import', component: () => import('@/features/teacher-bank-management/TeacherStandaloneKCImport.vue') },
      { path: 'questions/:questionId/edit/', name: 'admin-standalone-bank-question-edit', component: () => import('@/features/teacher-bank-management/TeacherStandaloneQuestionEditor.vue') },
    ],
  },
]
