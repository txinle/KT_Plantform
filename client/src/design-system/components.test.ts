import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import Alert from './components/Alert.vue'
import AppHeader from './components/AppHeader.vue'
import Breadcrumb from './components/Breadcrumb.vue'
import Button from './components/Button.vue'
import Card from './components/Card.vue'
import DataTable from './components/DataTable.vue'
import Drawer from './components/Drawer.vue'
import EmptyState from './components/EmptyState.vue'
import FocusAssessmentHeader from './components/FocusAssessmentHeader.vue'
import FormField from './components/FormField.vue'
import LocalTabs from './components/LocalTabs.vue'
import MobileCardList from './components/MobileCardList.vue'
import PageHeader from './components/PageHeader.vue'
import Paginator from './components/Paginator.vue'
import StatusBadge from './components/StatusBadge.vue'
import Icon from './Icon.vue'

describe('design-system foundations', () => {
  it('keeps keyboard focus inside an open drawer and returns to its trigger', async () => {
    const trigger = document.createElement('button')
    document.body.append(trigger)
    trigger.focus()
    const drawer = mount(Drawer, { attachTo: document.body, props: { open: false, title: '题目导航' }, slots: { default: '<a href="/student/">返回</a>' } })
    await drawer.setProps({ open: true })
    await flushPromises()
    const last = drawer.get('a')
    ;(last.element as HTMLElement).focus()
    await last.trigger('keydown', { key: 'Tab' })
    expect(document.activeElement).toBe(drawer.get('button').element)
    await drawer.setProps({ open: false })
    expect(document.activeElement).toBe(trigger)
    drawer.unmount()
    trigger.remove()
  })

  it('keeps decorative and semantic icons distinguishable', () => {
    expect(mount(Icon, { props: { name: 'check' } }).get('svg').attributes('aria-hidden')).toBe('true')
    const semantic = mount(Icon, { props: { name: 'warning', label: '警告' } }).get('svg')
    expect(semantic.attributes()).toMatchObject({ role: 'img', 'aria-label': '警告' })
  })

  it('renders the shared navigation and page hierarchy components', () => {
    const header = mount(AppHeader, { props: { userName: '教师甲', userMeta: 'T001' } })
    expect(header.get('.kt-brand-mark').text()).toBe('KT')
    expect(header.get('.kt-wordmark__name').text()).toBe('KT Data Lab')
    expect(header.get('.kt-user-name').text()).toContain('教师甲')
    expect(header.get('.kt-user-name').text()).toContain('T001')
    const teacherHeader = mount(AppHeader, {
      props: { userName: '教师甲', userMeta: 'teacher', csrfToken: 'csrf-token', showLogout: true },
    })
    expect(teacherHeader.get('form[data-logout] input[name="csrfmiddlewaretoken"]').attributes('value')).toBe('csrf-token')
    expect(teacherHeader.get('form[data-logout] button').text()).toBe('退出')
    expect(mount(Breadcrumb, { slots: { default: '<a href="/">课程</a>' } }).get('nav').attributes('aria-label')).toBe('层级目录')
    expect(mount(LocalTabs, { slots: { default: '<a href="/">实验</a>' } }).get('nav').attributes('aria-label')).toBe('页面导航')
    const pageHeader = mount(PageHeader, {
      props: { title: '实验' },
      slots: { default: '<p>实验说明</p>', actions: '<button>保存</button>' },
    })
    expect(pageHeader.get('h1').text()).toBe('实验')
    expect(pageHeader.get('.kt-page-header__copy').text()).toContain('实验说明')
    expect(pageHeader.get('.kt-page-header__actions').text()).toBe('保存')
  })

  it('renders controls and status with non-color semantics', () => {
    expect(mount(Button, { slots: { default: '保存' } }).get('button').text()).toBe('保存')
    expect(mount(StatusBadge, { props: { tone: 'success', label: '已发布' } }).text()).toBe('已发布')
    expect(mount(Alert, { props: { tone: 'danger' }, slots: { default: '失败' } }).get('[role="alert"]').text()).toBe('失败')
  })

  it('renders content, forms, and responsive collection primitives', () => {
    expect(mount(Card, { slots: { default: '内容' } }).get('section').text()).toBe('内容')
    expect(mount(EmptyState, { props: { title: '暂无实验' } }).get('h2').text()).toBe('暂无实验')
    const field = mount(FormField, { props: { forId: 'name', label: '名称', error: '必填' }, slots: { default: '<input id="name">' } })
    expect(field.get('label').attributes('for')).toBe('name')
    expect(field.get('[role="alert"]').text()).toBe('必填')
    expect(mount(DataTable, { props: { caption: '实验列表' } }).get('caption').text()).toBe('实验列表')
    expect(mount(MobileCardList, { slots: { default: '<li>实验 A</li>' } }).get('ul').text()).toBe('实验 A')
  })

  it('keeps assessment, pagination, and drawer state explicit', async () => {
    expect(mount(FocusAssessmentHeader, { props: { current: 2, total: 10 } }).text()).toContain('第 2/10 题')
    expect(mount(Paginator, { props: { page: 1, totalPages: 1 } }).find('nav').exists()).toBe(false)
    const paginator = mount(Paginator, { props: { page: 2, totalPages: 3 } })
    await paginator.get('[data-next]').trigger('click')
    expect(paginator.emitted('navigate')).toEqual([[3]])
    expect(mount(Drawer, { props: { open: false, title: '题目导航' } }).find('[role="dialog"]').exists()).toBe(false)
    const drawer = mount(Drawer, { props: { open: true, title: '题目导航' } })
    expect(drawer.get('[role="dialog"]').attributes('aria-modal')).toBe('true')
    expect(drawer.get('[role="dialog"]').attributes('tabindex')).toBe('-1')
    await drawer.get('[role="dialog"]').trigger('keydown', { key: 'Escape' })
    await drawer.get('[data-drawer-backdrop]').trigger('click')
    expect(drawer.emitted('close')).toHaveLength(2)
  })
})
