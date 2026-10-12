import { createApiClient } from '@/api/client'
import { getBootstrapContext } from '@/app/bootstrap'

export interface MetricValue {
  numerator: string
  denominator: string
  ratio: string | null
  state: 'available' | 'no_data' | 'partial' | 'conflict'
  sample_size: number | null
  percent_display: string
}

export interface TeacherAnalysisCourse {
  id: number
  name: string
  code: string
  term: string
  status: string
}

export interface TeacherAnalysisClassGroup {
  id: number
  name: string
  academic_year: string
}

interface TeacherAnalysisResponse<TDetail> {
  course: TeacherAnalysisCourse
  class_group: TeacherAnalysisClassGroup
  detail: TDetail
  actions: { back_url: string }
}

export interface StudentDetail {
  generated_at: string
  student_id: number
  student_number: string
  display_name: string
  completed_experiments: number
  eligible_experiments: number
  correct_rate: MetricValue
  score_rate: MetricValue
  average_answer_time_ms: string | null
  average_answer_time_display: string
  experiments: Array<{
    experiment_id: number
    name: string
    completed: boolean
    correct_rate: MetricValue
    score_rate: MetricValue
    rank: number | null
    rank_sample_size: number
    average_answer_time_ms: string | null
    average_answer_time_display: string
    detail_url: string
  }>
  trend_rows: Array<{
    experiment_id: number
    name: string
    student_correct_rate: MetricValue
    class_average_correct_rate: MetricValue
    x: string
    student_y: string
    class_y: string
  }>
  show_trend_chart: boolean
  kc_rows: Array<{
    name: string
    comparison: {
      student_value: MetricValue
      class_average: MetricValue
      sample_size: number
    }
  }>
}

export type StudentDetailResponse = TeacherAnalysisResponse<StudentDetail>

export interface StudentExperimentDetail {
  generated_at: string
  experiment_id: number
  experiment_name: string
  student_id: number
  student_number: string
  display_name: string
  is_current_member: boolean
  completed: boolean
  correct_rate: MetricValue
  score_rate: MetricValue
  average_answer_time_ms: string | null
  average_answer_time_display: string
  rank: number | null
  rank_sample_size: number
  questions: Array<{
    question_id: number
    code: string
    question_summary: string
    kcs: string[]
    result_label: string
    score_display: string
    first_answer: string | null
    final_answer: string
    revision_count: number | null
    answer_time_ms: string | null
    answer_time_display: string
    process_complete: boolean
    confidence_label: string
    submission_status_label: string
    submitted_at: string
    question_url: string
    process_url: string
  }>
}

export type StudentExperimentDetailResponse = TeacherAnalysisResponse<StudentExperimentDetail>

export interface QuestionDetail {
  generated_at: string
  experiment_id: number
  experiment_name: string
  question_id: number
  code: string
  text: string
  question_type: string
  kcs: string[]
  eligible_count: number
  current_member_count: number
  historical_participant_count: number
  answer_count: number
  correct_count: number
  incorrect_count: number
  correct_rate: MetricValue
  timing: {
    median_display: string
    iqr_display: string
    sample_size: number
    quality: {
      submitted_count: number
      exact_count: number
      estimated_count: number
      wall_clock_count: number
      unavailable_count: number
    }
  }
  chain_coverage: MetricValue
  option_rows: Array<{ key: string; label: string; count: number; is_correct: boolean }>
  transition_applicability: 'applicable' | 'not_applicable'
  transition_rows: Array<{ key: string; label: string; count: number }>
  participants: Array<{
    student_id: number
    student_number: string
    display_name: string
    is_current_member: boolean
    result_label: string
    final_answer: string
    process_complete: boolean
    student_experiment_url: string
    process_url: string
  }>
}

export type QuestionDetailResponse = TeacherAnalysisResponse<QuestionDetail>

export interface AttemptProcessDetail {
  generated_at: string
  experiment_id: number
  experiment_name: string
  question_id: number
  question_code: string
  question_text: string
  question_options: string[]
  correct_answer: string
  student_id: number
  student_number: string
  display_name: string
  is_current_member: boolean
  result_label: string
  score_display: string
  confidence_label: string
  answer_time_display: string
  process: {
    question_type: string
    completeness: 'complete' | 'incomplete'
    completeness_label: string
    steps: Array<{
      label: string
      occurred_at: string | null
      value: string | null
      value_before: string | null
      value_after: string | null
      length_before: number | null
      length_after: number | null
      time_source: string
      interval_ms: number | null
    }>
    first_value: string | null
    final_value: string | null
    revision_count: number | null
  }
  coverage_status_title: string
  coverage_status_description: string
  coverage_time_title: string
  coverage_time_description: string
  coverage_reason_labels: string[]
  has_cross_instance_order_uncertainty: boolean
}

export type AttemptProcessResponse = TeacherAnalysisResponse<AttemptProcessDetail>

export interface ExperimentResultsResolution {
  kind: 'canonical' | 'class_tasks' | 'sessions'
  canonical_url: string | null
  course: TeacherAnalysisCourse
  experiment: { id: number; name: string; status: string }
  tasks: Array<{
    academic_year: string
    class_name: string
    class_snapshot_name: string
    analysis_url: string
  }>
  sessions: Array<{
    student_number: string
    display_name: string
    status: string
    status_label: string
    started_at: string
    submission_count: number
    detail_url: string
  }>
  session_status_choices: Array<{ value: string; label: string }>
  selected_status: string
  actions: { back_url: string; clear_filter_url: string }
}

export interface ExperimentSessionResolution {
  kind: 'canonical' | 'legacy_report'
  canonical_url: string | null
  course: TeacherAnalysisCourse
  experiment: { id: number; name: string; status: string }
  report: {
    student_name: string
    student_number: string
    class_snapshot_name: string
    version_number: number
    session_status: string
    question_count: number
    questions: Array<{
      assigned_sequence_index: number
      stable_code: string
      question_type: string
      question_type_label: string
      text: string
      options: Array<{ key: string; text: string; display: string }>
      student_answer: string
      correct_answer: string
      answer_status: string
    }>
  } | null
  actions: { back_url: string; word_url: string | null }
}

type ApiClient = ReturnType<typeof createApiClient>

function prefix(courseId: number, classId: number) {
  return `/api/v1/teacher/courses/${courseId}/classes/${classId}`
}

export function createTeacherAnalysisApi(client: ApiClient) {
  return {
    studentDetail(courseId: number, classId: number, studentId: number) {
      return client.get<StudentDetailResponse>(
        `${prefix(courseId, classId)}/students/${studentId}/analysis/`,
      )
    },
    studentExperimentDetail(courseId: number, classId: number, studentId: number, experimentId: number) {
      return client.get<StudentExperimentDetailResponse>(
        `${prefix(courseId, classId)}/students/${studentId}/experiments/${experimentId}/`,
      )
    },
    questionDetail(courseId: number, classId: number, experimentId: number, questionId: number) {
      return client.get<QuestionDetailResponse>(
        `${prefix(courseId, classId)}/experiments/${experimentId}/questions/${questionId}/`,
      )
    },
    attemptProcess(courseId: number, classId: number, experimentId: number, questionId: number, studentId: number) {
      return client.get<AttemptProcessResponse>(
        `${prefix(courseId, classId)}/experiments/${experimentId}/questions/${questionId}/students/${studentId}/process/`,
      )
    },
    experimentResultsResolution(courseId: number, experimentId: number, status = '') {
      const query = status ? `?${new URLSearchParams({ status }).toString()}` : ''
      return client.get<ExperimentResultsResolution>(
        `/api/v1/teacher/courses/${courseId}/experiments/${experimentId}/results/resolve/${query}`,
      )
    },
    experimentSessionResolution(courseId: number, experimentId: number, sessionId: number) {
      return client.get<ExperimentSessionResolution>(
        `/api/v1/teacher/courses/${courseId}/experiments/${experimentId}/sessions/${sessionId}/resolve/`,
      )
    },
  }
}

export function getTeacherAnalysisApi() {
  const bootstrap = getBootstrapContext()
  return createTeacherAnalysisApi(
    createApiClient({ getCsrfToken: () => bootstrap.csrf_token }),
  )
}
