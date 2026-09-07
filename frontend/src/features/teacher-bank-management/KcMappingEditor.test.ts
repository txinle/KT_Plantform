import { mount } from '@vue/test-utils'
import { beforeAll, describe, expect, it } from 'vitest'
import KcMappingEditor from './KcMappingEditor.vue'

const initial = () => ({ root_kc_code: 'KC1', nodes: [
  { kc_code: 'KC1', parent_kc_code: null, order: 1 },
  { kc_code: 'KC2', parent_kc_code: 'KC1', order: 2 },
  { kc_code: 'KC3', parent_kc_code: 'KC2', order: 1 },
  { kc_code: 'KC4', parent_kc_code: 'KC1', order: 1 },
] })
beforeAll(() => {
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value() { this.setAttribute('open', '') } })
  Object.defineProperty(HTMLDialogElement.prototype, 'close', { configurable: true, value() { this.removeAttribute('open'); this.dispatchEvent(new Event('close')) } })
})
async function setup(edit = true) {
  const model = initial()
  const wrapper = mount(KcMappingEditor, { props: {
    modelValue: model,
    concepts: Array.from({ length: 6 }, (_, i) => ({ kc_code: `KC${i + 1}`, name: `知识点${i + 1}` })),
    'onUpdate:modelValue': value => { void wrapper.setProps({ modelValue: value }) },
  } })
  if (edit) { await wrapper.get('[data-kc-toggle]').trigger('click'); await wrapper.get('[data-edit-mapping]').trigger('click') }
  return { wrapper, model }
}
describe('KC node editing preserves the existing mapping contract', () => {
  it('keeps a summary visible and previews without changing data', async () => {
    const { wrapper } = await setup(false)
    expect(wrapper.get('[data-kc-toggle]').attributes('aria-expanded')).toBe('false')
    expect(wrapper.get('dialog').attributes('open')).toBeUndefined()
    expect(wrapper.get('.kt-kc-editor-summary').text()).toContain('知识点1')
    await wrapper.get('[data-preview]').trigger('click')
    expect(wrapper.get('dialog').attributes('open')).toBeDefined()
    expect(wrapper.find('#kc-root').exists()).toBe(false)
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
    await wrapper.get('[data-modal-close]').trigger('click')
    expect(wrapper.get('dialog').attributes('open')).toBeUndefined()
  })
  it('reveals mapping actions when validation fails', async () => {
    const { wrapper } = await setup(false)
    await wrapper.setProps({ error: '不能重复关联 KC' })
    expect(wrapper.get('[data-kc-toggle]').attributes('aria-expanded')).toBe('true')
    expect(wrapper.get('[role="alert"]').text()).toContain('不能重复')
  })
  it('automatically compacts parallel rows without changing mapping data', async () => {
    const { wrapper } = await setup()
    expect(wrapper.classes()).toContain('is-compact')
    const model = { root_kc_code: 'KC1', nodes: [
      { kc_code: 'KC1', parent_kc_code: null, order: 1 },
      ...['KC2', 'KC3', 'KC4', 'KC5'].map((kc_code, index) => ({ kc_code, parent_kc_code: 'KC1', order: index + 1 })),
    ] }
    await wrapper.setProps({ modelValue: model })
    expect(wrapper.classes()).toContain('is-dense')
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
    await wrapper.setProps({ modelValue: { root_kc_code: 'KC1', nodes: model.nodes.slice(0, 2) } })
    expect(wrapper.classes()).not.toContain('is-compact')
  })
  it('does not rewrite the initial mapping to match visual order', async () => {
    const { wrapper, model } = await setup()
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
    expect(wrapper.props('modelValue')).toEqual(model)
    expect(wrapper.findAll('[data-level="2"]').map(n => n.attributes('data-code'))).toEqual(['KC4', 'KC2'])
  })
  it('changes root and middle codes with their child references intact', async () => {
    const { wrapper } = await setup()
    await wrapper.get('#kc-root').setValue('KC5')
    expect(wrapper.props('modelValue').nodes.filter(n => ['KC2', 'KC4'].includes(n.kc_code)).every(n => n.parent_kc_code === 'KC5')).toBe(true)
    await wrapper.get('[data-code="KC2"] select[data-kc-choice]').setValue('KC6')
    expect(wrapper.props('modelValue').nodes.find(n => n.kc_code === 'KC3')?.parent_kc_code).toBe('KC6')
    expect(wrapper.props('modelValue').nodes.find(n => n.kc_code === 'KC4')?.parent_kc_code).toBe('KC5')
  })
  it('removes only the chosen branch and its descendants', async () => {
    const { wrapper } = await setup()
    await wrapper.get('[data-code="KC2"] [data-remove]').trigger('click')
    expect(wrapper.props('modelValue').nodes.map(n => n.kc_code)).toEqual(['KC1', 'KC4'])
  })
  it('adds children at the selected parent and preserves explicit order changes', async () => {
    const { wrapper } = await setup()
    await wrapper.get('[data-code="KC4"] [data-add]').trigger('click')
    expect(wrapper.props('modelValue').nodes.find(n => n.kc_code === 'KC5')).toEqual({ kc_code: 'KC5', parent_kc_code: 'KC4', order: 1 })
    await wrapper.get('[data-code="KC3"] [data-order]').setValue(7)
    await wrapper.get('[data-code="KC3"] [data-parent]').setValue('KC4')
    expect(wrapper.props('modelValue').nodes.find(n => n.kc_code === 'KC3')).toEqual({ kc_code: 'KC3', parent_kc_code: 'KC4', order: 7 })
  })
  it('clears the mapping and can start again without duplicate KC options', async () => {
    const { wrapper } = await setup()
    expect(wrapper.get('[data-code="KC2"] option[value="KC1"]').attributes('disabled')).toBeDefined()
    await wrapper.get('[data-clear]').trigger('click')
    expect(wrapper.props('modelValue')).toEqual({ root_kc_code: '', nodes: [] })
    await wrapper.get('#kc-root').setValue('KC1')
    await wrapper.get('[data-code="KC1"] [data-add]').trigger('click')
    expect(wrapper.props('modelValue').nodes).toHaveLength(2)
  })
})
