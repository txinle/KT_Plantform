import type { RouteRecordRaw } from 'vue-router'

const TeacherQuestionBanksLayout = () => import('@/features/teacher-question-banks/TeacherQuestionBanksLayout.vue')
const TeacherArchivedBanks = () => import('./TeacherArchivedBanks.vue')
const TeacherBankEditor = () => import('./TeacherBankEditor.vue')
const TeacherKCImport = () => import('./TeacherKCImport.vue')
const TeacherQuestionEditor = () => import('./TeacherQuestionEditor.vue')
const TeacherQuestionImport = () => import('./TeacherQuestionImport.vue')
const TeacherSharedBanks = () => import('./TeacherSharedBanks.vue')
const TeacherSharedCreators = () => import('./TeacherSharedCreators.vue')
const TeacherSharedSources = () => import('./TeacherSharedSources.vue')

export const teacherBankManagementRoutes: RouteRecordRaw[] = [
  {
    path: '/teacher/courses/:courseId/banks/archived/',
    component: TeacherQuestionBanksLayout,
    children: [{ path: '', name: 'teacher-question-bank-archived', component: TeacherArchivedBanks }],
  },
  {
    path: '/teacher/courses/:courseId/banks/shared/',
    component: TeacherQuestionBanksLayout,
    children: [{ path: '', name: 'teacher-question-bank-shared-sources', component: TeacherSharedSources }],
  },
  {
    path: '/teacher/courses/:courseId/banks/shared/:sourceCourseId/',
    component: TeacherQuestionBanksLayout,
    children: [{ path: '', name: 'teacher-question-bank-shared-creators', component: TeacherSharedCreators }],
  },
  {
    path: '/teacher/courses/:courseId/banks/shared/:sourceCourseId/creators/:creatorId/',
    component: TeacherQuestionBanksLayout,
    children: [{ path: '', name: 'teacher-question-bank-shared-banks', component: TeacherSharedBanks }],
  },
  {
    path: '/teacher/courses/:courseId/banks/:bankId/edit/',
    component: TeacherQuestionBanksLayout,
    children: [{ path: '', name: 'teacher-question-bank-edit', component: TeacherBankEditor }],
  },
  {
    path: '/teacher/courses/:courseId/banks/:bankId/import/',
    component: TeacherQuestionBanksLayout,
    children: [{ path: '', name: 'teacher-question-import', component: TeacherQuestionImport }],
  },
  {
    path: '/teacher/courses/:courseId/banks/:bankId/kc-import/',
    component: TeacherQuestionBanksLayout,
    children: [{ path: '', name: 'teacher-question-kc-import', component: TeacherKCImport }],
  },
  {
    path: '/teacher/courses/:courseId/banks/:bankId/questions/:questionId/edit/',
    component: TeacherQuestionBanksLayout,
    children: [{ path: '', name: 'teacher-question-edit', component: TeacherQuestionEditor }],
  },
]
