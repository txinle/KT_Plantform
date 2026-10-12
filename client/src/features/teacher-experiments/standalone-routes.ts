import type { RouteRecordRaw } from 'vue-router'

const TeacherStandaloneExperimentsLayout = () => import('./TeacherStandaloneExperimentsLayout.vue')
const TeacherStandaloneExperimentDetail = () => import('./TeacherStandaloneExperimentDetail.vue')
const TeacherStandaloneExperimentForm = () => import('./TeacherStandaloneExperimentForm.vue')
const TeacherStandaloneExperimentList = () => import('./TeacherStandaloneExperimentList.vue')
const TeacherStandaloneExperimentResults = () => import('./TeacherStandaloneExperimentResults.vue')

export const teacherStandaloneExperimentRoutes: RouteRecordRaw[] = [
  {
    path: '/teacher/experiments/',
    component: TeacherStandaloneExperimentsLayout,
    children: [
      { path: '', name: 'teacher-standalone-experiments', component: TeacherStandaloneExperimentList },
      { path: 'create/', name: 'teacher-standalone-experiment-create', component: TeacherStandaloneExperimentForm },
      { path: ':experimentId/', name: 'teacher-standalone-experiment-detail', component: TeacherStandaloneExperimentDetail },
      { path: ':experimentId/edit/', name: 'teacher-standalone-experiment-edit', component: TeacherStandaloneExperimentForm },
      { path: ':experimentId/results/', name: 'teacher-standalone-experiment-results', component: TeacherStandaloneExperimentResults },
    ],
  },
]
