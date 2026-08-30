import { createApiClient } from '@/api/client'
import type { components } from '@/api/generated/schema'
import { getBootstrapContext } from '@/app/bootstrap'

export type DataCenterScope = components['schemas']['DataCenterScope']
export type DataCenterOverview = components['schemas']['DataCenterOverview']
export type DataCenterExportCatalog = components['schemas']['DataCenterExportCatalog']
export type DataCenterQualityCollection = components['schemas']['DataCenterQualityCollection']
export type DataCenterExportHistoryCollection = components['schemas']['DataCenterExportHistoryCollection']
export type DataCenterAdvanced = components['schemas']['DataCenterAdvanced']

type ApiClient = ReturnType<typeof createApiClient>
type QueryValue = string | number | null | undefined | (string | null)[]
export type DataQuery = Record<string, QueryValue>

export function routeQuery(query: Record<string, unknown>): DataQuery {
  const result: DataQuery = {}
  for (const [key, value] of Object.entries(query)) {
    if (Array.isArray(value)) result[key] = value[0] as string | null | undefined
    else if (typeof value === 'string' || typeof value === 'number' || value == null) result[key] = value
  }
  return result
}

export function queryString(input: DataQuery) {
  const params = new URLSearchParams()
  for (const [key, rawValue] of Object.entries(input)) {
    const value = Array.isArray(rawValue) ? rawValue[0] : rawValue
    if (value !== undefined && value !== null && value !== '') params.set(key, String(value))
  }
  const query = params.toString()
  return query ? `?${query}` : ''
}

export function createTeacherDataCenterApi(client: ApiClient) {
  return {
    scope(courseId: number, query: DataQuery = {}) {
      return client.get<DataCenterScope>(`/api/v1/teacher/courses/${courseId}/data/scope/${queryString(query)}`)
    },
    overview(courseId: number, query: DataQuery = {}) {
      return client.get<DataCenterOverview>(`/api/v1/teacher/courses/${courseId}/data/overview/${queryString(query)}`)
    },
    exports(courseId: number, query: DataQuery = {}) {
      return client.get<DataCenterExportCatalog>(`/api/v1/teacher/courses/${courseId}/data/exports/${queryString(query)}`)
    },
    quality(courseId: number, query: DataQuery = {}) {
      return client.get<DataCenterQualityCollection>(`/api/v1/teacher/courses/${courseId}/data/quality/${queryString(query)}`)
    },
    history(courseId: number, page = 1) {
      return client.get<DataCenterExportHistoryCollection>(`/api/v1/teacher/courses/${courseId}/data/exports/history/?page=${page}`)
    },
    advanced(courseId: number, query: DataQuery = {}) {
      return client.get<DataCenterAdvanced>(`/api/v1/teacher/courses/${courseId}/data/advanced/${queryString(query)}`)
    },
  }
}

export function getTeacherDataCenterApi() {
  const bootstrap = getBootstrapContext()
  return createTeacherDataCenterApi(createApiClient({
    getCsrfToken: () => bootstrap.csrf_token,
  }))
}
