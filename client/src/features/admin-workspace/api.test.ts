import { describe, expect, it, vi } from 'vitest'
import { createAdminApi } from './api'

describe('admin API', () => {
  it('uses authenticated API mutations and preserves server error propagation', async () => {
    const client = { get: vi.fn().mockResolvedValue({ items: [] }), mutate: vi.fn().mockRejectedValue(new Error('权限已变化')) }
    const api = createAdminApi(client)
    await api.get('users/')
    expect(client.get).toHaveBeenCalledWith('/api/v1/manage/users/')
    await expect(api.post('users/3/status/', { is_active: false })).rejects.toThrow('权限已变化')
    expect(client.mutate).toHaveBeenCalledWith('/api/v1/manage/users/3/status/', { method: 'POST', body: { is_active: false } })
  })
})
