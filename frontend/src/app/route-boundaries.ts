import type { RouteRecordRaw } from 'vue-router'

import TeacherExperimentDetail from '@/features/teacher-experiments/TeacherExperimentDetail.vue'
import TeacherExperimentForm from '@/features/teacher-experiments/TeacherExperimentForm.vue'
import TeacherExperimentList from '@/features/teacher-experiments/TeacherExperimentList.vue'
import TeacherExperimentPublishPlan from '@/features/teacher-experiments/TeacherExperimentPublishPlan.vue'
import TeacherExperimentsLayout from '@/features/teacher-experiments/TeacherExperimentsLayout.vue'
import TeacherQuestionBankDetail from '@/features/teacher-question-banks/TeacherQuestionBankDetail.vue'
import TeacherQuestionBankList from '@/features/teacher-question-banks/TeacherQuestionBankList.vue'
import TeacherQuestionBanksLayout from '@/features/teacher-question-banks/TeacherQuestionBanksLayout.vue'
import TeacherDataAdvanced from '@/features/teacher-data-center/TeacherDataAdvanced.vue'
import TeacherDataCenterLayout from '@/features/teacher-data-center/TeacherDataCenterLayout.vue'
import TeacherDataExportHistory from '@/features/teacher-data-center/TeacherDataExportHistory.vue'
import TeacherDataExports from '@/features/teacher-data-center/TeacherDataExports.vue'
import TeacherDataOverview from '@/features/teacher-data-center/TeacherDataOverview.vue'
import TeacherDataQuality from '@/features/teacher-data-center/TeacherDataQuality.vue'
import StudentCourseList from '@/features/student-courses/StudentCourseList.vue'
import StudentCoursesLayout from '@/features/student-courses/StudentCoursesLayout.vue'
import StudentTaskList from '@/features/student-courses/StudentTaskList.vue'
import StudentLearning from '@/features/student-learning/StudentLearning.vue'
import StudentLearningQuestion from '@/features/student-learning/StudentLearningQuestion.vue'
import StudentResult from '@/features/student-results/StudentResult.vue'
import StudentAssessment from '@/assessment-runtime/StudentAssessment.vue'
import TeacherClassCenter from '@/features/teacher-classes/TeacherClassCenter.vue'
import TeacherClassList from '@/features/teacher-classes/TeacherClassList.vue'
import TeacherClassesLayout from '@/features/teacher-classes/TeacherClassesLayout.vue'

export const migratedRoutes: RouteRecordRaw[] = [
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
