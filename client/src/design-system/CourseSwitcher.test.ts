import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import CourseSwitcher from './components/CourseSwitcher.vue'

const mocks = vi.hoisted(() => ({ get: vi.fn() }))
vi.mock('@/api/client', () => ({ createApiClient: () => ({ get: mocks.get }) }))
afterEach(() => { vi.restoreAllMocks(); vi.clearAllMocks() })

async function render(path = '/teacher/courses/7/banks/12/') {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/:pathMatch(.*)*', component: { template: '<div />' } }] })
  await router.push(path)
  mocks.get.mockResolvedValue({ items: [
    { id: 7, name: '英语', detail_url: '/teacher/courses/7/' },
    { id: 9, name: 'EDA', detail_url: '/teacher/courses/9/' },
  ] })
  const wrapper = mount(CourseSwitcher, { props: { courseName: '英语', csrfToken: 'token' }, global: { plugins: [router] } })
  await flushPromises()
  return { wrapper, router }
}

it('loads only authorized courses and switches to the equivalent module without reusing a bank id', async () => {
  const { wrapper, router } = await render()
  expect(mocks.get).toHaveBeenCalledWith('/api/v1/teacher/courses/')
  expect(wrapper.findAll('option').map(option => option.text())).toEqual(['英语', 'EDA'])
  await wrapper.get('select').setValue('9')
  await flushPromises()
  expect(router.currentRoute.value.path).toBe('/teacher/courses/9/banks/')
})

it('keeps the current editor when leaving it is cancelled', async () => {
  const { wrapper, router } = await render('/teacher/courses/7/experiments/create/')
  vi.spyOn(window, 'confirm').mockReturnValue(false)
  await wrapper.get('select').setValue('9')
  expect(router.currentRoute.value.path).toBe('/teacher/courses/7/experiments/create/')
  expect((wrapper.get('select').element as HTMLSelectElement).value).toBe('7')
})

it('retains current course and offers retry when the list cannot be loaded', async () => {
  mocks.get.mockRejectedValueOnce(new Error('课程列表暂不可用'))
  const { wrapper } = await render()
  expect(wrapper.get('[role="alert"]').text()).toContain('课程列表暂不可用')
  await wrapper.get('button').trigger('click')
  await flushPromises()
  expect(wrapper.find('[role="alert"]').exists()).toBe(false)
  expect(wrapper.findAll('option')).toHaveLength(2)
})
