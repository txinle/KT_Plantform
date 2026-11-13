import { flushPromises, mount } from '@vue/test-utils'
import { beforeAll, describe, expect, it, vi } from 'vitest'
import QuestionContent from './QuestionContent.vue'
import { questionContentSummary } from './question-content'

const key = `${'a'.repeat(64)}.png`
const marker = `{{image:${key}}}`
const source = 'data:image/png;base64,aGVsbG8='
beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }
  HTMLDialogElement.prototype.close = function () { this.removeAttribute('open') }
})

describe('QuestionContent', () => {
  it('keeps compact list summaries readable without exposing storage markers', () => {
    expect(questionContentSummary(`Before ${marker} after`)).toBe('Before [图片] after')
    expect(questionContentSummary('Ordinary question')).toBe('Ordinary question')
    expect(questionContentSummary('k={{b01}}; P={{b02}} W')).toBe('k=____; P=____ W')
  })
  it('preserves text order, line breaks and cloze markers without rendering HTML', () => {
    const wrapper = mount(QuestionContent, { props: { text: `<b>First</b>\n{{b01}} ${marker} last`, images: { [key]: source } } })
    expect(wrapper.find('b').exists()).toBe(false)
    expect(wrapper.text()).toContain('<b>First</b>\n{{b01}}')
    expect(wrapper.get('img').attributes('src')).toBe(source)
    expect(wrapper.get('span').element.textContent).toBe('<b>First</b>\n{{b01}}  last')
  })

  it('does not fetch unknown or external image references', () => {
    const wrapper = mount(QuestionContent, { props: { text: marker, images: { [key]: 'https://example.com/track.png' } } })
    expect(wrapper.find('img').exists()).toBe(false)
    expect(wrapper.text()).toBe(marker)
  })

  it('opens a keyboard accessible enlargement without activating the surrounding option', async () => {
    const click = vi.fn()
    const wrapper = mount({ components: { QuestionContent }, template: '<label @click="click"><input type="radio"><QuestionContent :text="text" :images="images" /></label>', setup: () => ({ click, text: marker, images: { [key]: source } }) }, { attachTo: document.body })
    await wrapper.get('button[aria-label="放大图片"]').trigger('click')
    await flushPromises()
    expect(click).not.toHaveBeenCalled()
    expect(wrapper.get<HTMLInputElement>('input').element.checked).toBe(false)
    expect(document.querySelector('dialog[open] img')?.getAttribute('src')).toBe(source)
    const close = document.querySelector('dialog button') as HTMLButtonElement
    close.click()
    await flushPromises()
    expect(document.querySelector('dialog[open]')).toBeNull()
    wrapper.unmount()
  })
})
