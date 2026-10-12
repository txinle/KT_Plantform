import { createApiClient } from '@/api/client'
import { getBootstrapContext } from '@/app/bootstrap'

type ApiClient = ReturnType<typeof createApiClient>

export type StandaloneChoice = { value: string; label: string }
export type StandaloneQuestionBank = { id: number; name: string }
export type StandaloneQuestionQuota = { question_type: string; amount: number }

export type StandaloneExperimentInput = {
  name: string
  description: string
  question_bank_id: number
  selection_mode: 'all' | 'quota'
  question_quotas: StandaloneQuestionQuota[]
  ordering_mode: 'fixed' | 'random' | 'stratified_random'
  feedback_mode: 'none' | 'correctness' | 'full'
  feedback_release_policy: 'after_personal_submission' | 'after_assignment_deadline' | 'manual_release'
  time_mode: 'none' | 'total' | 'per_question'
  total_time_limit_minutes: number | null
  per_question_time_limit_seconds: number | null
  resume_mode: 'single_session' | 'resumable'
  confidence_mode: 'off' | 'all' | 'sampled'
  confidence_sampling_unit: 'student' | 'session' | 'attempt'
  confidence_probability: number | null
  confidence_randomization_seed: string
  confidence_assignment_version: string
  confidence_scale_version: string
  confidence_prompt_version: string
  heartbeat_interval_seconds: number
}

export type StandaloneExperiment = {
  id: number
  name: string
  description: string
  question_bank: StandaloneQuestionBank
  status: string
  status_label: string
  selection_mode: 'all' | 'quota'
  selection_mode_label: string
  ordering_mode: string
  ordering_mode_label: string
  feedback_mode: string
  feedback_mode_label: string
  feedback_release_policy: string
  feedback_release_policy_label: string
  time_mode: string
  time_mode_label: string
  total_time_limit_minutes: number | null
  per_question_time_limit_seconds: number | null
  resume_mode: string
  resume_mode_label: string
  confidence_mode: string
  confidence_mode_label: string
  confidence_sampling_unit: string
  confidence_sampling_unit_label: string
  confidence_probability: string | number | null
  confidence_randomization_seed: string
  confidence_assignment_version: string
  confidence_scale_version: string
  confidence_prompt_version: string
  heartbeat_interval_seconds: number
  question_count: number
  student_count: number
  created_at: string
  updated_at: string
}

export type StandaloneExperimentListItem = Pick<
  StandaloneExperiment,
  'id' | 'name' | 'description' | 'status' | 'status_label' | 'updated_at'
> & {
  question_bank: StandaloneQuestionBank
  ordering_mode_label: string
  student_count: number
  detail_url: string
}

export type StandaloneChoices = {
  selection_modes: StandaloneChoice[]
  ordering_modes: StandaloneChoice[]
  feedback_modes: StandaloneChoice[]
  feedback_release_policies: StandaloneChoice[]
  time_modes: StandaloneChoice[]
  resume_modes: StandaloneChoice[]
  confidence_modes: StandaloneChoice[]
  confidence_sampling_units: StandaloneChoice[]
  question_types: StandaloneChoice[]
}

export type CanonicalResolution = {
  kind: 'canonical'
  canonical_url: string
}

export type StandaloneCollection = {
  kind: 'standalone'
  items: StandaloneExperimentListItem[]
  question_banks: StandaloneQuestionBank[]
  choices: StandaloneChoices
}

export type StandaloneStudent = {
  id: number
  student_number: string | null
  display_name: string
  class_name: string
  selected: boolean
}

export type StandaloneDetail = {
  kind: 'standalone'
  experiment: StandaloneExperiment
  selection: {
    quota_rows: Array<{ question_type: string; label: string; amount: number }>
    current_candidate_count: number
    current_inventory_rows: Array<{ label: string; need: number; available: number }>
    version_rows: Array<{
      version_number: number
      published_at: string
      candidate_count: number
      inventory_rows: Array<{ label: string; need: number; available: number }>
    }>
  }
  students: StandaloneStudent[]
  available_students: StandaloneStudent[]
  question_banks: StandaloneQuestionBank[]
  actions: {
    detail_url: string
    edit_url: string
    results_url: string
    assign_url: string
    publish_url: string
    sync_questions_url: string
    delete_url: string
    can_edit: boolean
    can_assign: boolean
    can_publish: boolean
    can_sync_questions: boolean
  }
  choices: StandaloneChoices
}

export type StandaloneResults = {
  kind: 'standalone'
  experiment: StandaloneExperiment
  sessions: Array<{
    id: number
    student_number: string | null
    student_name: string
    status: string
    status_label: string
    started_at: string | null
    submission_count: number
  }>
  session_status_choices: StandaloneChoice[]
  selected_status: string
  export_field_groups: Array<{
    label: string
    fields: Array<{ key: string; label: string }>
  }>
  actions: {
    identified_export_url: string
    identified_navigation_export_url: string
    kt_export_url: string
    navigation_export_url: string
    identity_export_url: string
    detail_url: string
  }
}

export function createStandaloneExperimentApi(client: ApiClient) {
  const base = '/api/v1/teacher/standalone-experiments/'
  return {
    collection() {
      return client.get<StandaloneCollection | CanonicalResolution>(base)
    },
    detail(experimentId: number, destination: 'detail' | 'edit' = 'detail') {
      return client.get<StandaloneDetail | CanonicalResolution>(
        `${base}${experimentId}/?destination=${destination}`,
      )
    },
    create(input: StandaloneExperimentInput) {
      return client.mutate<StandaloneDetail | CanonicalResolution>(base, {
        method: 'POST',
        body: input,
      })
    },
    update(experimentId: number, input: Partial<StandaloneExperimentInput>) {
      return client.mutate<StandaloneDetail>(`${base}${experimentId}/`, {
        method: 'PATCH',
        body: input,
      })
    },
    assignStudents(experimentId: number, studentIds: number[]) {
      return client.mutate<StandaloneDetail>(`${base}${experimentId}/students/`, {
        method: 'POST',
        body: { student_ids: studentIds },
      })
    },
    publish(experimentId: number) {
      return client.mutate<StandaloneDetail>(`${base}${experimentId}/publish/`, {
        method: 'POST',
      })
    },
    syncQuestions(experimentId: number) {
      return client.mutate<StandaloneDetail>(`${base}${experimentId}/sync-questions/`, {
        method: 'POST',
      })
    },
    remove(experimentId: number) {
      return client.mutate<void>(`${base}${experimentId}/`, {
        method: 'DELETE',
        body: { confirmation: 'delete' },
      })
    },
    results(experimentId: number, status = '') {
      const query = status ? `?status=${encodeURIComponent(status)}` : ''
      return client.get<StandaloneResults | CanonicalResolution>(`${base}${experimentId}/results/${query}`)
    },
  }
}

export function getStandaloneExperimentApi() {
  const bootstrap = getBootstrapContext()
  return createStandaloneExperimentApi(
    createApiClient({ getCsrfToken: () => bootstrap.csrf_token }),
  )
}
