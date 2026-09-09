import { createApiClient } from '@/api/client'
import { getBootstrapContext } from '@/app/bootstrap'

export interface AccountClassOption {
  id: number
  name: string
  academic_year: string
  display_name: string
  is_active: boolean
}

export interface AccountClass extends Omit<AccountClassOption, 'display_name'> {
  course_id: number | null
  student_count: number
}

export interface AccountStudent {
  id: number
  student_number: string
  display_name: string
  username: string
  class_group: AccountClassOption | null
}

export interface AccountStudentCollection {
  items: AccountStudent[]
  class_options: AccountClassOption[]
  query: string
  selected_class_id: number | null
}

export interface CourseClassManagement {
  course: {
    id: number
    name: string
    code: string
    term: string
    status: 'active' | 'archived'
  }
  class_group: {
    id: number
    name: string
    academic_year: string
    is_active: boolean
    state_token: string
    detail_url: string
  }
  enrollments: Array<{
    enrollment_id: number
    student: AccountStudent
    identity_state_token: string
    affects_other_courses: boolean
  }>
  target_classes: AccountClassOption[]
  source_classes: Array<{
    id: number
    course_id: number
    course_name: string
    course_code: string
    course_term: string
    name: string
    academic_year: string
    display_name: string
  }>
  capabilities: {
    can_update: boolean
    can_delete: boolean
    can_add_students: boolean
    can_edit_students: boolean
    can_move_students: boolean
    can_remove_students: boolean
    can_reset_passwords: boolean
    can_update_visibility: boolean
  }
  actions: {
    detail_url: string
    classes_url: string
    add_students_url: string
    visibility_url: string
    import_template_url: string
  }
}

export interface StudentVisibilityPolicy {
  show_scores: boolean
  show_correct_rate: boolean
  show_item_results: boolean
  show_class_average: boolean
  rank_mode: 'none' | 'self'
  show_kc_rates: boolean
  show_trend: boolean
  is_read_only: boolean
}

export interface StudentImportPreview {
  preview: Array<{
    student_number: string
    display_name: string
    class_id: number
    class_name: string
    account_action: 'create' | 'reuse'
  }>
}

interface TeacherAccountManagementApiClient {
  get<T>(path: string): Promise<T>
  mutate<T>(path: string, mutation: { method: 'POST' | 'PATCH'; body: unknown }): Promise<T>
  mutateForm<T>(path: string, mutation: { method: 'POST'; body: FormData }): Promise<T>
}

function queryString(values: Record<string, string | number | undefined>) {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(values)) {
    if (value !== undefined && value !== '') params.set(key, String(value))
  }
  const encoded = params.toString()
  return encoded ? `?${encoded}` : ''
}

function classBase(courseId: number, classId: number) {
  return `/api/v1/teacher/accounts/courses/${courseId}/classes/${classId}`
}

export function createTeacherAccountManagementApi(client: TeacherAccountManagementApiClient) {
  return {
    classes: () => client.get<{ items: AccountClass[] }>('/api/v1/teacher/accounts/classes/'),
    createClass: (input: { name: string; academic_year: string }) => client.mutate<{ class_group: AccountClass }>('/api/v1/teacher/accounts/classes/', { method: 'POST', body: input }),
    students: ({ query, classId }: { query?: string; classId?: number } = {}) => client.get<AccountStudentCollection>(`/api/v1/teacher/accounts/students/${queryString({ q: query, class_id: classId })}`),
    createStudent: (input: { student_number: string; display_name: string; class_group: number }) => client.mutate<{ student: AccountStudent }>('/api/v1/teacher/accounts/students/', { method: 'POST', body: input }),
    previewLegacyImport: (body: FormData) => client.mutateForm<StudentImportPreview>('/api/v1/teacher/accounts/students/import/preview/', { method: 'POST', body }),
    confirmLegacyImport: () => client.mutate<{ imported: number }>('/api/v1/teacher/accounts/students/import/confirm/', { method: 'POST', body: {} }),
    resetLegacyStudentPassword: (studentId: number) => client.mutate<{ student: AccountStudent }>(`/api/v1/teacher/accounts/students/${studentId}/reset-password/`, { method: 'POST', body: { confirmation: 'reset' } }),
    management: (courseId: number, classId: number) => client.get<CourseClassManagement>(`${classBase(courseId, classId)}/manage/`),
    updateManagement: (courseId: number, classId: number, input: { name: string; academic_year: string; is_active: boolean; state_token: string }) => client.mutate<CourseClassManagement>(`${classBase(courseId, classId)}/manage/`, { method: 'PATCH', body: input }),
    manualEnrollment: (courseId: number, classId: number, input: { student_number: string; display_name: string; confirmation_token?: string }) => client.mutate<{ student: AccountStudent; created_account: boolean }>(`${classBase(courseId, classId)}/students/manual/`, { method: 'POST', body: input }),
    updateStudentIdentity: (courseId: number, classId: number, studentId: number, input: { student_number: string; display_name: string; state_token: string }) => client.mutate<{ student: AccountStudent; identity_state_token: string; affects_other_courses: boolean }>(`${classBase(courseId, classId)}/students/${studentId}/identity/`, { method: 'PATCH', body: input }),
    previewCopy: (courseId: number, classId: number, source_class: number) => client.mutate<{
      source_class: CourseClassManagement['source_classes'][number]
      preview: Array<{ student: AccountStudent; skip: boolean }>
    }>(`${classBase(courseId, classId)}/students/copy/preview/`, { method: 'POST', body: { source_class } }),
    confirmCopy: (courseId: number, classId: number) => client.mutate<{ copied: number; skipped: number }>(`${classBase(courseId, classId)}/students/copy/confirm/`, { method: 'POST', body: {} }),
    previewCourseImport: (courseId: number, classId: number, body: FormData) => client.mutateForm<StudentImportPreview>(`${classBase(courseId, classId)}/students/import/preview/`, { method: 'POST', body }),
    confirmCourseImport: (courseId: number, classId: number) => client.mutate<{ created_accounts: number; enrollments: number }>(`${classBase(courseId, classId)}/students/import/confirm/`, { method: 'POST', body: {} }),
    moveStudent: (courseId: number, classId: number, studentId: number, class_group: number) => client.mutate<{ class_group: AccountClassOption }>(`${classBase(courseId, classId)}/students/${studentId}/move/`, { method: 'POST', body: { class_group } }),
    removeStudent: (courseId: number, classId: number, studentId: number) => client.mutate<{ removed: boolean }>(`${classBase(courseId, classId)}/students/${studentId}/remove/`, { method: 'POST', body: { confirmation: 'delete' } }),
    resetCourseStudentPassword: (courseId: number, classId: number, studentId: number) => client.mutate<{ student: AccountStudent }>(`${classBase(courseId, classId)}/students/${studentId}/reset-password/`, { method: 'POST', body: { confirmation: 'reset' } }),
    visibility: (courseId: number, classId: number) => client.get<StudentVisibilityPolicy>(`${classBase(courseId, classId)}/student-visibility/`),
    saveVisibility: (courseId: number, classId: number, input: Omit<StudentVisibilityPolicy, 'is_read_only'>) => client.mutate<StudentVisibilityPolicy>(`${classBase(courseId, classId)}/student-visibility/`, { method: 'PATCH', body: input }),
  }
}

let api: ReturnType<typeof createTeacherAccountManagementApi> | undefined

export function getTeacherAccountManagementApi() {
  if (api) return api
  const bootstrap = getBootstrapContext()
  api = createTeacherAccountManagementApi(
    createApiClient({ getCsrfToken: () => bootstrap.csrf_token }),
  )
  return api
}
