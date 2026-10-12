import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const auth = vi.hoisted(() => ({
  acceptResearchConsent: vi.fn(),
  getResearchConsent: vi.fn(),
  logout: vi.fn(),
}))

vi.mock('./auth', () => auth)

import ResearchConsentDialog from './ResearchConsentDialog.vue'

const document = {
  version: '2026-09-14',
  title: '研究参与说明与知情同意',
  paragraphs: [
    '你好！我们正在开展认知诊断与学习过程相关的学术研究，邀请你完成相关习题。',
    '在最终数据处理时，将去除姓名、学号等敏感信息。',
  ],
  researcher_name: '唐鑫乐',
  researcher_email: 'txinle@163.com',
  checkbox_label: '我已阅读并理解上述说明，自愿参与本研究。（勾选并确认同意后方可进入答题）',
  exit_label: '退出',
  accept_label: '同意并继续',
}

describe('ResearchConsentDialog', () => {
  beforeEach(() => {
    auth.getResearchConsent.mockReset().mockResolvedValue(document)
    auth.acceptResearchConsent.mockReset().mockResolvedValue(undefined)
    auth.logout.mockReset().mockResolvedValue(undefined)
  })

  it('renders the authoritative copy without an estimated duration or placeholders', async () => {
    const wrapper = mount(ResearchConsentDialog)
    await flushPromises()

    expect(wrapper.text()).toContain('研究参与说明与知情同意')
    expect(wrapper.text()).toContain('唐鑫乐')
    expect(wrapper.text()).toContain('txinle@163.com')
    expect(wrapper.text()).toContain('去除姓名、学号等敏感信息')
    expect(wrapper.text()).not.toContain('预计耗时')
    expect(wrapper.text()).not.toContain('导师XXX')
  })

  it('requires the checkbox and waits for server acceptance before continuing', async () => {
    let resolveAcceptance: (() => void) | undefined
    auth.acceptResearchConsent.mockReturnValueOnce(
      new Promise<void>((resolve) => { resolveAcceptance = resolve }),
    )
    const wrapper = mount(ResearchConsentDialog)
    await flushPromises()

    const submit = wrapper.get('[data-consent-accept]')
    expect(submit.attributes('disabled')).toBeDefined()
    await wrapper.get('input[type="checkbox"]').setValue(true)
    expect(submit.attributes('disabled')).toBeUndefined()

    await submit.trigger('click')
    expect(auth.acceptResearchConsent).toHaveBeenCalledWith(document.version)
    expect(wrapper.emitted('accepted')).toBeUndefined()
    resolveAcceptance?.()
    await flushPromises()
    expect(wrapper.emitted('accepted')).toHaveLength(1)
  })

  it('keeps the dialog available for retry after acceptance fails', async () => {
    auth.acceptResearchConsent.mockRejectedValueOnce(new Error('network unavailable'))
    const wrapper = mount(ResearchConsentDialog)
    await flushPromises()
    await wrapper.get('input[type="checkbox"]').setValue(true)
    await wrapper.get('[data-consent-accept]').trigger('click')
    await flushPromises()

    expect(wrapper.get('[role="alert"]').text()).toContain('保存失败')
    expect(wrapper.find('[data-consent-dialog]').exists()).toBe(true)
    expect(wrapper.get('[data-consent-accept]').attributes('disabled')).toBeUndefined()
  })

  it('does not close from Escape or a backdrop click', async () => {
    const wrapper = mount(ResearchConsentDialog)
    await flushPromises()

    await wrapper.get('[data-consent-dialog]').trigger('keydown', { key: 'Escape' })
    await wrapper.get('main').trigger('click')

    expect(wrapper.find('[data-consent-dialog]').exists()).toBe(true)
    expect(auth.logout).not.toHaveBeenCalled()
  })

  it('logs out from both the close and exit controls', async () => {
    const closeWrapper = mount(ResearchConsentDialog, {
      props: { navigateToLogin: vi.fn() },
    })
    await flushPromises()
    await closeWrapper.get('[data-consent-close]').trigger('click')
    expect(auth.logout).toHaveBeenCalledOnce()

    auth.logout.mockClear()
    const exitWrapper = mount(ResearchConsentDialog, {
      props: { navigateToLogin: vi.fn() },
    })
    await flushPromises()
    await exitWrapper.get('[data-consent-exit]').trigger('click')

    expect(auth.logout).toHaveBeenCalledOnce()
  })
})
