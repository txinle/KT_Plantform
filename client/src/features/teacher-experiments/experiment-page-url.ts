import { inject, type InjectionKey } from 'vue'

export type PageUrl = {
  (url: string): string
  (url: string | undefined | null): string | undefined
}
export const experimentPageUrlKey: InjectionKey<PageUrl> = Symbol('experiment-page-url')

export function experimentPageUrl(url: string, admin?: boolean): string
export function experimentPageUrl(url: string | undefined | null, admin?: boolean): string | undefined
export function experimentPageUrl(url: string | undefined | null, admin = false): string | undefined {
  if (!url || !admin) return url ?? undefined
  const [path, suffix = ''] = url.split(/(?=[?#])/, 2)
  if (/^\/teacher\/courses\/\d+\/experiments\/(?:create\/|\d+\/(?:edit\/|student-preview\/|class-tasks\/\d+\/grading\/|publish-plan\/\d+\/step\/\d+\/)?)?$/.test(path!)) return url.replace('/teacher/', '/manage/')
  if (/^\/teacher\/experiments\/(?:create\/|\d+\/(?:edit\/|results\/)?)?$/.test(path!)) return url.replace('/teacher/', '/manage/')
  const course = /^\/teacher\/courses\/(\d+)\/(.*)$/.exec(path!)
  if (course) {
    const id = course[1], section = course[2]
    if (/^data\/(?:exports\/|export\/|exports\/history\/|quality\/|advanced\/|tables\/)?$/.test(section!)) return `/manage/data/?course_id=${id}`
    if (section === 'banks/') return `/manage/resources/?course_id=${id}`
    if (section === 'classes/') return `/manage/courses/?tab=classes&course_id=${id}`
    if (!section) return `/manage/courses/?course_id=${id}`
    if (section === 'settings/') return `/manage/courses/${id}/settings/${suffix}`
  }
  if (path === '/teacher/courses/') return '/manage/courses/'
  if (path === '/teacher/') return '/manage/'
  return url
}

export function useExperimentPageUrl(): PageUrl {
  return inject(experimentPageUrlKey, experimentPageUrl)
}
