import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, expect, it } from 'vitest'
import SiteDialog from './SiteDialog.vue'

beforeEach(() => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }
  HTMLDialogElement.prototype.close = function () { this.removeAttribute('open'); this.dispatchEvent(new Event('close')) }
})

it('can hide a parent dialog for a child without requesting dismissal', async () => {
  const wrapper = mount(SiteDialog, { props: { open: true, title: '题库' } })
  await flushPromises()
  await wrapper.setProps({ open: false })
  await flushPromises()
  expect(wrapper.emitted('close')).toBeUndefined()
  await wrapper.setProps({ open: true })
  await flushPromises()
  await wrapper.get('button[aria-label="关闭弹窗"]').trigger('click')
  expect(wrapper.emitted('close')).toHaveLength(1)
})
