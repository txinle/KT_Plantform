import type { RouteRecordRaw } from 'vue-router'
const layout = () => import('./AdminExperimentsLayout.vue')

export const adminExperimentRoutes: RouteRecordRaw[] = [
  {
    path: '/manage/courses/:courseId/experiments/', component: layout,
    children: [
      { path: '', name: 'admin-course-experiments', component: () => import('./TeacherExperimentList.vue') },
      { path: 'create/', name: 'admin-course-experiment-create', component: () => import('./TeacherExperimentForm.vue') },
      { path: ':experimentId/', name: 'admin-course-experiment-detail', component: () => import('./TeacherExperimentDetail.vue') },
      { path: ':experimentId/edit/', name: 'admin-course-experiment-edit', component: () => import('./TeacherExperimentForm.vue') },
      { path: ':experimentId/class-tasks/:taskId/grading/', name: 'admin-course-experiment-grading', component: () => import('./TeacherShortAnswerGrading.vue') },
      { path: ':experimentId/publish-plan/:planId/step/:step/', name: 'admin-course-experiment-publish-plan', component: () => import('./TeacherExperimentPublishPlan.vue') },
      { path: ':experimentId/student-preview/', name: 'admin-course-experiment-student-preview', component: () => import('./TeacherStudentPreview.vue') },
    ],
  },
  {
    path: '/manage/experiments/', component: layout,
    children: [
      { path: '', name: 'admin-standalone-experiments', component: () => import('./TeacherStandaloneExperimentList.vue') },
      { path: 'create/', name: 'admin-standalone-experiment-create', component: () => import('./TeacherStandaloneExperimentForm.vue') },
      { path: ':experimentId/', name: 'admin-standalone-experiment-detail', component: () => import('./TeacherStandaloneExperimentDetail.vue') },
      { path: ':experimentId/edit/', name: 'admin-standalone-experiment-edit', component: () => import('./TeacherStandaloneExperimentForm.vue') },
      { path: ':experimentId/results/', name: 'admin-standalone-experiment-results', component: () => import('./TeacherStandaloneExperimentResults.vue') },
    ],
  },
]
