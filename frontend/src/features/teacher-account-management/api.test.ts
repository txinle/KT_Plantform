import { describe, expect, it, vi } from 'vitest'

import { createTeacherAccountManagementApi } from './api'

describe('teacher account-management API', () => {
  it('keeps global account and course-class operations on their approved same-origin endpoints', async () => {
    const client = { get: vi.fn(), mutate: vi.fn(), mutateForm: vi.fn() }
    client.get.mockResolvedValue({})
    client.mutate.mockResolvedValue({})
    client.mutateForm.mockResolvedValue({})
    const api = createTeacherAccountManagementApi(client)
    const upload = new FormData()
    upload.append('file', new File(['xlsx'], 'students.xlsx'))

    await api.classes()
    await api.createClass({ name: '账户班', academic_year: '2026' })
    await api.students({ query: '张', classId: 4 })
    await api.createStudent({ student_number: 'S-1', display_name: '张同学', class_group: 4, password: 'password-1' })
    await api.previewLegacyImport(upload)
    await api.confirmLegacyImport()
    await api.resetLegacyStudentPassword(9, 'password-2')
    await api.management(7, 11)
    await api.updateManagement(7, 11, { name: '一班', academic_year: '2026', is_active: true, state_token: 'state-1' })
    await api.deleteManagement(7, 11)
    await api.manualEnrollment(7, 11, { student_number: 'S-2', display_name: '李同学' })
    await api.previewCopy(7, 11, 12)
    await api.confirmCopy(7, 11)
    await api.previewCourseImport(7, 11, upload)
    await api.confirmCourseImport(7, 11)
    await api.moveStudent(7, 11, 9, 12)
    await api.removeStudent(7, 11, 9)
    await api.resetCourseStudentPassword(7, 11, 9, 'password-3')
    await api.visibility(7, 11)
    await api.saveVisibility(7, 11, {
      show_scores: true,
      show_correct_rate: true,
      show_item_results: true,
      show_class_average: true,
      rank_mode: 'self',
      show_kc_rates: true,
      show_trend: true,
    })

    expect(client.get).toHaveBeenNthCalledWith(1, '/api/v1/teacher/accounts/classes/')
    expect(client.mutate).toHaveBeenNthCalledWith(1, '/api/v1/teacher/accounts/classes/', {
      method: 'POST', body: { name: '账户班', academic_year: '2026' },
    })
    expect(client.get).toHaveBeenNthCalledWith(2, '/api/v1/teacher/accounts/students/?q=%E5%BC%A0&class_id=4')
    expect(client.mutate).toHaveBeenNthCalledWith(2, '/api/v1/teacher/accounts/students/', {
      method: 'POST', body: { student_number: 'S-1', display_name: '张同学', class_group: 4, password: 'password-1' },
    })
    expect(client.mutateForm).toHaveBeenNthCalledWith(1, '/api/v1/teacher/accounts/students/import/preview/', {
      method: 'POST', body: upload,
    })
    expect(client.mutate).toHaveBeenNthCalledWith(3, '/api/v1/teacher/accounts/students/import/confirm/', {
      method: 'POST', body: {},
    })
    expect(client.mutate).toHaveBeenNthCalledWith(4, '/api/v1/teacher/accounts/students/9/reset-password/', {
      method: 'POST', body: { password: 'password-2' },
    })
    expect(client.get).toHaveBeenNthCalledWith(3, '/api/v1/teacher/accounts/courses/7/classes/11/manage/')
    expect(client.mutate).toHaveBeenNthCalledWith(5, '/api/v1/teacher/accounts/courses/7/classes/11/manage/', {
      method: 'PATCH', body: { name: '一班', academic_year: '2026', is_active: true, state_token: 'state-1' },
    })
    expect(client.mutate).toHaveBeenNthCalledWith(6, '/api/v1/teacher/accounts/courses/7/classes/11/delete/', {
      method: 'POST', body: { confirmation: 'delete' },
    })
    expect(client.mutate).toHaveBeenNthCalledWith(7, '/api/v1/teacher/accounts/courses/7/classes/11/students/manual/', {
      method: 'POST', body: { student_number: 'S-2', display_name: '李同学' },
    })
    expect(client.mutate).toHaveBeenNthCalledWith(8, '/api/v1/teacher/accounts/courses/7/classes/11/students/copy/preview/', {
      method: 'POST', body: { source_class: 12 },
    })
    expect(client.mutate).toHaveBeenNthCalledWith(9, '/api/v1/teacher/accounts/courses/7/classes/11/students/copy/confirm/', {
      method: 'POST', body: {},
    })
    expect(client.mutateForm).toHaveBeenNthCalledWith(2, '/api/v1/teacher/accounts/courses/7/classes/11/students/import/preview/', {
      method: 'POST', body: upload,
    })
    expect(client.mutate).toHaveBeenNthCalledWith(10, '/api/v1/teacher/accounts/courses/7/classes/11/students/import/confirm/', {
      method: 'POST', body: {},
    })
    expect(client.mutate).toHaveBeenNthCalledWith(11, '/api/v1/teacher/accounts/courses/7/classes/11/students/9/move/', {
      method: 'POST', body: { class_group: 12 },
    })
    expect(client.mutate).toHaveBeenNthCalledWith(12, '/api/v1/teacher/accounts/courses/7/classes/11/students/9/remove/', {
      method: 'POST', body: { confirmation: 'delete' },
    })
    expect(client.mutate).toHaveBeenNthCalledWith(13, '/api/v1/teacher/accounts/courses/7/classes/11/students/9/reset-password/', {
      method: 'POST', body: { password: 'password-3' },
    })
    expect(client.get).toHaveBeenNthCalledWith(4, '/api/v1/teacher/accounts/courses/7/classes/11/student-visibility/')
    expect(client.mutate).toHaveBeenNthCalledWith(14, '/api/v1/teacher/accounts/courses/7/classes/11/student-visibility/', {
      method: 'PATCH', body: {
        show_scores: true,
        show_correct_rate: true,
        show_item_results: true,
        show_class_average: true,
        rank_mode: 'self',
        show_kc_rates: true,
        show_trend: true,
      },
    })
  })
})
