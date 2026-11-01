import { createApiClient } from '@/api/client'
import { getBootstrapContext } from '@/app/bootstrap'

export interface CollaborationPerson { id: number; display_name: string; username: string; is_active?: boolean }
export interface CollaborationMember extends CollaborationPerson { permissions: string[] }
export interface CourseCollaboration {
  redirect_url?: string | null
  course: { id: number; name: string; code: string; term: string; status: string }
  owner: CollaborationPerson
  members: CollaborationMember[]
  permission_options: Array<{ key: string; label: string }>
  granted_permissions: string[]
  capabilities: { can_manage_members: boolean; can_transfer: boolean; can_update: boolean; can_archive: boolean }
  state_token: string
}
export interface CollaborationMutation {
  action: 'add' | 'update' | 'remove' | 'transfer'
  teacher_id: number
  permissions?: string[]
  state_token: string
  keep_previous?: boolean
  reason?: string
}
const client = () => createApiClient({ getCsrfToken: () => getBootstrapContext().csrf_token })
export const collaborationApi = {
  get: (id: number) => client().get<CourseCollaboration>(`/api/v1/teacher/courses/${id}/collaboration/`),
  search: (id: number, query: string) => client().get<{ items: CollaborationPerson[] }>(`/api/v1/teacher/courses/${id}/collaboration/teachers/?q=${encodeURIComponent(query)}`),
  mutate: (id: number, body: CollaborationMutation) => client().mutate<CourseCollaboration>(`/api/v1/teacher/courses/${id}/collaboration/`, { method: 'POST', body }),
}
