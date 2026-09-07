import type { RouteRecordRaw } from 'vue-router'

const TeacherAnalysisLayout = () => import('./TeacherAnalysisLayout.vue')
const TeacherAttemptProcess = () => import('./TeacherAttemptProcess.vue')
const TeacherExperimentCompatibilityLayout = () => import('./TeacherExperimentCompatibilityLayout.vue')
const TeacherExperimentResultsCompatibility = () => import('./TeacherExperimentResultsCompatibility.vue')
const TeacherExperimentSessionCompatibility = () => import('./TeacherExperimentSessionCompatibility.vue')
const TeacherQuestionDetail = () => import('./TeacherQuestionDetail.vue')
const TeacherStudentDetail = () => import('./TeacherStudentDetail.vue')
const TeacherStudentExperimentDetail = () => import('./TeacherStudentExperimentDetail.vue')

export const teacherAnalysisRoutes: RouteRecordRaw[] = [
  {
    path: '/teacher/courses/:courseId/classes/:classId/students/:studentId/analysis/',
    component: TeacherAnalysisLayout,
    children: [
      {
        path: '',
        name: 'teacher-class-student-analysis',
        component: TeacherStudentDetail,
      },
    ],
  },
  {
    path: '/teacher/courses/:courseId/classes/:classId/students/:studentId/experiments/:experimentId/',
    component: TeacherAnalysisLayout,
    children: [
      {
        path: '',
        name: 'teacher-class-student-experiment-analysis',
        component: TeacherStudentExperimentDetail,
      },
    ],
  },
  {
    path: '/teacher/courses/:courseId/classes/:classId/experiments/:experimentId/questions/:questionId/',
    component: TeacherAnalysisLayout,
    children: [
      {
        path: '',
        name: 'teacher-class-question-analysis',
        component: TeacherQuestionDetail,
      },
    ],
  },
  {
    path: '/teacher/courses/:courseId/classes/:classId/experiments/:experimentId/questions/:questionId/students/:studentId/process/',
    component: TeacherAnalysisLayout,
    children: [
      {
        path: '',
        name: 'teacher-class-question-process',
        component: TeacherAttemptProcess,
      },
    ],
  },
  {
    path: '/teacher/courses/:courseId/experiments/:experimentId/results/',
    component: TeacherExperimentCompatibilityLayout,
    children: [
      {
        path: '',
        name: 'teacher-experiment-results-compatibility',
        component: TeacherExperimentResultsCompatibility,
      },
    ],
  },
  {
    path: '/teacher/courses/:courseId/experiments/:experimentId/sessions/:sessionId/',
    component: TeacherExperimentCompatibilityLayout,
    children: [
      {
        path: '',
        name: 'teacher-experiment-session-compatibility',
        component: TeacherExperimentSessionCompatibility,
      },
    ],
  },
]
