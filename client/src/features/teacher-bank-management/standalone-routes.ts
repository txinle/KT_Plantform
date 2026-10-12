import type { RouteRecordRaw } from 'vue-router'

const TeacherStandaloneQuestionBanksLayout = () => import('./TeacherStandaloneQuestionBanksLayout.vue')
const TeacherStandaloneBanks = () => import('./TeacherStandaloneBanks.vue')
const TeacherStandaloneBankDetail = () => import('./TeacherStandaloneBankDetail.vue')
const TeacherStandaloneQuestionImport = () => import('./TeacherStandaloneQuestionImport.vue')
const TeacherStandaloneKCImport = () => import('./TeacherStandaloneKCImport.vue')
const TeacherStandaloneQuestionEditor = () => import('./TeacherStandaloneQuestionEditor.vue')
const TeacherStandaloneConcepts = () => import('./TeacherStandaloneConcepts.vue')

export const teacherStandaloneQuestionBankRoutes: RouteRecordRaw[] = [
  {
    path: '/teacher/banks/',
    component: TeacherStandaloneQuestionBanksLayout,
    children: [{ path: '', name: 'teacher-standalone-question-banks', component: TeacherStandaloneBanks }],
  },
  {
    path: '/teacher/banks/:bankId/',
    component: TeacherStandaloneQuestionBanksLayout,
    children: [{ path: '', name: 'teacher-standalone-question-bank-detail', component: TeacherStandaloneBankDetail }],
  },
  {
    path: '/teacher/banks/:bankId/import/',
    component: TeacherStandaloneQuestionBanksLayout,
    children: [{ path: '', name: 'teacher-standalone-question-import', component: TeacherStandaloneQuestionImport }],
  },
  {
    path: '/teacher/banks/:bankId/kc-import/',
    component: TeacherStandaloneQuestionBanksLayout,
    children: [{ path: '', name: 'teacher-standalone-question-kc-import', component: TeacherStandaloneKCImport }],
  },
  {
    path: '/teacher/banks/:bankId/questions/:questionId/edit/',
    component: TeacherStandaloneQuestionBanksLayout,
    children: [{ path: '', name: 'teacher-standalone-question-editor', component: TeacherStandaloneQuestionEditor }],
  },
  {
    path: '/teacher/concepts/',
    component: TeacherStandaloneQuestionBanksLayout,
    children: [{ path: '', name: 'teacher-standalone-concepts', component: TeacherStandaloneConcepts }],
  },
]
