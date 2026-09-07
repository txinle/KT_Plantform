import type { RouteRecordRaw } from 'vue-router'
import { teacherStandaloneExperimentRoutes } from '@/features/teacher-experiments/standalone-routes'
import { teacherTeachingDataRoutes } from '@/features/teacher-teaching-data/routes'
import { teacherStandaloneQuestionBankRoutes } from '@/features/teacher-bank-management/standalone-routes'
import { teacherAnalysisRoutes } from '@/features/teacher-analysis/routes'
import { teacherBankManagementRoutes } from '@/features/teacher-bank-management/routes'
import { teacherAccountManagementRoutes } from '@/features/teacher-account-management/routes'
const TeacherCourseList = () => import('@/features/teacher-courses/TeacherCourseList.vue')
const TeacherCourseCreate = () => import('@/features/teacher-courses/TeacherCourseCreate.vue')
const TeacherCourseOverview = () => import('@/features/teacher-courses/TeacherCourseOverview.vue')
const TeacherCourseSettings = () => import('@/features/teacher-courses/TeacherCourseSettings.vue')

const TeacherExperimentDetail = () => import('@/features/teacher-experiments/TeacherExperimentDetail.vue')
const TeacherExperimentForm = () => import('@/features/teacher-experiments/TeacherExperimentForm.vue')
const TeacherExperimentList = () => import('@/features/teacher-experiments/TeacherExperimentList.vue')
const TeacherExperimentPublishPlan = () => import('@/features/teacher-experiments/TeacherExperimentPublishPlan.vue')
const TeacherExperimentsLayout = () => import('@/features/teacher-experiments/TeacherExperimentsLayout.vue')
const TeacherQuestionBankDetail = () => import('@/features/teacher-question-banks/TeacherQuestionBankDetail.vue')
const TeacherQuestionBankList = () => import('@/features/teacher-question-banks/TeacherQuestionBankList.vue')
const TeacherQuestionBanksLayout = () => import('@/features/teacher-question-banks/TeacherQuestionBanksLayout.vue')
const TeacherDataAdvanced = () => import('@/features/teacher-data-center/TeacherDataAdvanced.vue')
const TeacherDataCenterLayout = () => import('@/features/teacher-data-center/TeacherDataCenterLayout.vue')
const TeacherDataExportHistory = () => import('@/features/teacher-data-center/TeacherDataExportHistory.vue')
const TeacherDataExports = () => import('@/features/teacher-data-center/TeacherDataExports.vue')
const TeacherDataOverview = () => import('@/features/teacher-data-center/TeacherDataOverview.vue')
const TeacherDataQuality = () => import('@/features/teacher-data-center/TeacherDataQuality.vue')
const StudentCourseList = () => import('@/features/student-courses/StudentCourseList.vue')
const StudentCoursesLayout = () => import('@/features/student-courses/StudentCoursesLayout.vue')
const StudentTaskList = () => import('@/features/student-courses/StudentTaskList.vue')
const StudentLearning = () => import('@/features/student-learning/StudentLearning.vue')
const StudentLearningQuestion = () => import('@/features/student-learning/StudentLearningQuestion.vue')
const StudentResult = () => import('@/features/student-results/StudentResult.vue')
const StudentAssessment = () => import('@/assessment-runtime/StudentAssessment.vue')
const TeacherClassCenter = () => import('@/features/teacher-classes/TeacherClassCenter.vue')
const TeacherClassList = () => import('@/features/teacher-classes/TeacherClassList.vue')
const TeacherClassesLayout = () => import('@/features/teacher-classes/TeacherClassesLayout.vue')

export const migratedRoutes: RouteRecordRaw[] = [
  ...teacherStandaloneExperimentRoutes,
  ...teacherTeachingDataRoutes,
  ...teacherAccountManagementRoutes,
  ...teacherBankManagementRoutes,
  ...teacherStandaloneQuestionBankRoutes,
  ...teacherAnalysisRoutes,
  { path: '/student/sessions/:sessionId/complete/', name: 'student-session-complete', component: () => import('@/features/student-results/StudentSessionComplete.vue') },
  { path: '/teacher/courses/', name: 'teacher-courses', component: TeacherCourseList },
  { path: '/teacher/courses/create/', name: 'teacher-course-create', component: TeacherCourseCreate },
  { path: '/teacher/courses/:courseId/', name: 'teacher-course-overview', component: TeacherCourseOverview },
  { path: '/teacher/courses/:courseId/settings/', name: 'teacher-course-settings', component: TeacherCourseSettings },
  {
    path: '/teacher/courses/:courseId/banks/',
    component: TeacherQuestionBanksLayout,
    children: [
      { path: '', name: 'teacher-question-banks', component: TeacherQuestionBankList },
      { path: ':bankId/', name: 'teacher-question-bank-detail', component: TeacherQuestionBankDetail },
    ],
  },
  {
    path: '/teacher/courses/:courseId/classes/',
    component: TeacherClassesLayout,
    children: [
      { path: '', name: 'teacher-classes', component: TeacherClassList },
      { path: ':classId/', name: 'teacher-class-center', component: TeacherClassCenter },
    ],
  },
  {
    path: '/teacher/courses/:courseId/experiments/',
    component: TeacherExperimentsLayout,
    children: [
      { path: '', name: 'teacher-experiments', component: TeacherExperimentList },
      { path: 'create/', name: 'teacher-experiment-create', component: TeacherExperimentForm },
      { path: ':experimentId/', name: 'teacher-experiment-detail', component: TeacherExperimentDetail },
      { path: ':experimentId/edit/', name: 'teacher-experiment-edit', component: TeacherExperimentForm },
      {
        path: ':experimentId/publish-plan/:planId/step/:step/',
        name: 'teacher-experiment-publish-plan',
        component: TeacherExperimentPublishPlan,
      },
    ],
  },
  {
    path: '/teacher/courses/:courseId/data/',
    component: TeacherDataCenterLayout,
    children: [
      { path: '', name: 'teacher-data-overview', component: TeacherDataOverview },
      { path: 'export/', name: 'teacher-data-export-compat', component: TeacherDataExports },
      { path: 'exports/', name: 'teacher-data-exports', component: TeacherDataExports },
      { path: 'quality/', name: 'teacher-data-quality', component: TeacherDataQuality },
      { path: 'exports/history/', name: 'teacher-data-export-history', component: TeacherDataExportHistory },
      { path: 'tables/', name: 'teacher-data-tables', component: TeacherDataAdvanced },
      { path: 'advanced/', name: 'teacher-data-advanced', component: TeacherDataAdvanced },
    ],
  },
  {
    path: '/student/',
    component: StudentCoursesLayout,
    children: [
      { path: '', name: 'student-courses', component: StudentCourseList },
    ],
  },
  {
    path: '/student/courses/:courseId/',
    component: StudentCoursesLayout,
    children: [
      { path: '', name: 'student-tasks', component: StudentTaskList },
      { path: 'learning/', name: 'student-learning', component: StudentLearning },
      { path: 'learning/experiments/:experimentId/', name: 'student-result', component: StudentResult },
      { path: 'learning/experiments/:experimentId/questions/:questionId/', name: 'student-learning-question', component: StudentLearningQuestion },
    ],
  },
  {
    path: '/student/sessions/:sessionId/questions/:assignmentId/',
    name: 'student-assessment',
    component: StudentAssessment,
  },
]
