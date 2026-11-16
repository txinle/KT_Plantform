import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import DataPageHeader from './DataPageHeader.vue'

describe('DataPageHeader', () => {
  it('keeps the page title and actions visible with explanations collapsed', () => {
    const wrapper = mount(DataPageHeader, {
      props: { title: '导出研究数据' },
      slots: { default: '<p>详细使用说明</p>', actions: '<button>刷新结果</button>' },
    })
    expect(wrapper.get('h1').text()).toBe('导出研究数据')
    expect(wrapper.get<HTMLDetailsElement>('details').element.open).toBe(false)
    expect(wrapper.get('summary').text()).toBe('使用说明')
    expect(wrapper.get('button').text()).toBe('刷新结果')
  })
})
