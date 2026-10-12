import { getCsrfToken } from '@/app/bootstrap'
import { createApiClient } from './client'

export async function downloadFile(path: string, options: { method?: 'GET' | 'POST'; body?: FormData } = {}) {
  const file = await createApiClient({ getCsrfToken }).download(path, options)
  const url = URL.createObjectURL(file.blob)
  const link = document.createElement('a')
  link.href = url
  link.download = file.filename
  document.body.append(link)
  link.click()
  link.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}
