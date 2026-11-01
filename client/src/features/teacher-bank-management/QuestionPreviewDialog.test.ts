import { flushPromises, mount } from '@vue/test-utils'
import { beforeAll, expect, it, vi } from 'vitest'
const mocks = vi.hoisted(() => ({ get: vi.fn() }))
vi.mock('./group-api', () => ({ getQuestionGroupApi: () => ({ get: mocks.get }) }))
import QuestionPreviewDialog from './QuestionPreviewDialog.vue'
beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }
  HTMLDialogElement.prototype.close = function () { this.removeAttribute('open') }
})
it('previews a complete group in a native modal and supports Escape', async () => {
  mocks.get.mockResolvedValue({ id: 2, kind: 'reading', title: 'Shared reading', material: 'The whole article', instructions: '', members: [] })
  const wrapper = mount(QuestionPreviewDialog, { props: { courseId: 7, bankId: 3, id: 2, group: true } })
  await flushPromises()
  const dialog = wrapper.get('dialog[aria-label="完整题目预览"]')
  expect(dialog.attributes()).toHaveProperty('open')
  expect(dialog.text()).toContain('The whole article')
  await dialog.trigger('cancel')
  expect(wrapper.emitted('close')).toHaveLength(1)
  wrapper.unmount()
})
