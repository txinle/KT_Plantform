import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import LegacyBlankInputs from './LegacyBlankInputs.vue'

describe('historical multi-blank input', () => {
  it('restores without recording an edit and preserves missing slots', async () => {
    const wrapper = mount(LegacyBlankInputs, { props: { count: 2, value: '-0.5 6' } })
    expect(wrapper.findAll('input').map(node => node.element.value)).toEqual(['-0.5', '6'])
    expect(wrapper.emitted('input')).toBeUndefined()
    await wrapper.findAll('input')[0]!.setValue('')
    expect(wrapper.emitted('input')!.at(-1)![0]).toMatchObject({ target: { value: '；6' } })
    await wrapper.setProps({ value: '；6' })
    expect(wrapper.findAll('input').map(node => node.element.value)).toEqual(['', '6'])
  })
  it('keeps the combined value during IME and does not discard ambiguous old text', async () => {
    const wrapper = mount(LegacyBlankInputs, { props: { count: 2, value: '2；' } })
    const input = wrapper.findAll('input')[1]!
    await input.trigger('compositionstart')
    await input.setValue('发出')
    await input.trigger('compositionend')
    expect(wrapper.emitted('compositionend')!.at(-1)![0]).toMatchObject({ target: { value: '2；发出' } })
    await wrapper.setProps({ value: '无法 分割 的原文', disabled: true })
    expect(wrapper.findAll('input')[0]!.element.value).toBe('无法 分割 的原文')
    expect(wrapper.findAll('input').every(node => node.element.disabled)).toBe(true)
    expect(wrapper.text()).toContain('原答案')
  })
})
