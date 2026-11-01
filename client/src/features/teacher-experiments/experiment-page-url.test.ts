import { describe, expect, it } from 'vitest'
import { experimentPageUrl } from './experiment-page-url'

describe('administrator experiment navigation', () => {
  it('keeps experiment flows in the administrator workspace', () => {
    for (const tail of ['', 'create/', '8/', '8/edit/', '8/publish-plan/3/step/2/', '8/class-tasks/4/grading/', '8/student-preview/']) {
      expect(experimentPageUrl(`/teacher/courses/2/experiments/${tail}`, true)).toBe(`/manage/courses/2/experiments/${tail}`)
    }
    expect(experimentPageUrl('/teacher/experiments/8/results/?status=completed', true)).toBe('/manage/experiments/8/results/?status=completed')
    expect(experimentPageUrl('/teacher/courses/2/data/exports/', true)).toBe('/manage/data/?course_id=2')
    expect(experimentPageUrl('/teacher/courses/2/banks/', true)).toBe('/manage/resources/?course_id=2')
    expect(experimentPageUrl('/teacher/courses/2/classes/', true)).toBe('/manage/courses/?tab=classes&course_id=2')
  })
  it('preserves API, downloads, and every teacher-context link', () => {
    for (const url of ['/api/v1/teacher/courses/2/experiments/', '/teacher/experiments/8/export/kt/', '/teacher/courses/2/data/export-jobs/x/download/']) {
      expect(experimentPageUrl(url, true)).toBe(url)
    }
    expect(experimentPageUrl('/teacher/courses/2/experiments/8/', false)).toBe('/teacher/courses/2/experiments/8/')
  })
})
