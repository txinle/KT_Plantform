import { useRoute } from 'vue-router'

export function useBankPageUrl() {
  const route = useRoute()
  function bankPageUrl(url: string): string
  function bankPageUrl(url: string | null | undefined): string | undefined
  function bankPageUrl(url: string | null | undefined): string | undefined {
    if (url == null) return undefined
    if (!route.path?.startsWith('/manage/resources/')) return url
    if (/^\/teacher\/courses\/(?:\d+\/)?(?:[?#]|$)/.test(url)) {
      return url.replace(/^\/teacher\/courses\/(?:\d+\/)?/, '/manage/courses/')
    }
    const catalog = /^\/teacher\/(?:courses\/\d+\/)?banks\/(?:(shared|archived)\/)?(?:[?#]|$)/.exec(url)
    if (catalog) {
      const parsed = new URL(url, 'https://navigation.invalid')
      if (parsed.searchParams.get('tab') === 'course-kcs') parsed.searchParams.set('tab', 'concepts')
      if (catalog[1]) parsed.searchParams.set('visibility', catalog[1])
      return `/manage/resources/${parsed.search}${parsed.hash}`
    }
    if (/^\/teacher\/courses\/\d+\/banks\/\d+\//.test(url)) {
      return url.replace(/^\/teacher\//, '/manage/resources/')
    }
    if (/^\/teacher\/banks\/\d+\//.test(url)) {
      return url.replace(/^\/teacher\//, '/manage/resources/')
    }
    return url
  }
  return bankPageUrl
}
