import { Fragment, defineComponent, h, isVNode, type Component, type PropType, type VNode } from 'vue'
import WorkspaceNavigationIcon from './WorkspaceNavigationIcon.vue'

type NavigationIcon = 'home' | 'overview' | 'bank' | 'class' | 'experiment' | 'data' | 'settings'

function flatten(nodes: VNode[]): VNode[] {
  return nodes.flatMap(node => node.type === Fragment && Array.isArray(node.children)
    ? flatten(node.children as VNode[]) : [node])
}

function targetPath(node: VNode): string {
  const target = node.props?.href ?? node.props?.to
  if (typeof target === 'string') return target.split(/[?#]/, 1)[0] || ''
  if (target && typeof target === 'object' && 'path' in target && typeof target.path === 'string') return target.path
  return ''
}

function navigationIcon(path: string): NavigationIcon | null {
  if (path === '/teacher/courses/' || path === '/teacher/courses') return 'home'
  const match = path.match(/^\/teacher\/courses\/\d+\/?(.*)$/)
  if (!match) return null
  const section = match[1].replace(/\/$/, '').split('/')[0]
  if (!section) return 'overview'
  if (section === 'banks') return 'bank'
  if (section === 'classes') return 'class'
  if (section === 'experiments') return 'experiment'
  if (section === 'data') return 'data'
  if (section === 'settings') return 'settings'
  return null
}

function containsIcon(value: unknown): boolean {
  if (Array.isArray(value)) return value.some(containsIcon)
  if (!isVNode(value)) {
    if (value && typeof value === 'object' && 'default' in value && typeof value.default === 'function') {
      return containsIcon(value.default())
    }
    return false
  }
  if (value.type === 'svg') return true
  const className = value.props?.class
  if (typeof className === 'string' && /(^|\s)[^\s]*icon(?:\s|$)/i.test(className)) return true
  if (Array.isArray(value.children)) return value.children.some(containsIcon)
  return false
}

function withNavigationIcon(node: VNode, name: NavigationIcon): VNode {
  if (containsIcon(node.children)) return node
  const icon = h(WorkspaceNavigationIcon, { name })
  const props = { ...node.props, key: node.key }
  if (Array.isArray(node.children)) return h(node.type as Component, props, [icon, ...node.children])
  if (typeof node.children === 'string') return h(node.type as Component, props, [icon, node.children])
  if (node.children && typeof node.children === 'object' && 'default' in node.children) {
    const slots = node.children as { default?: () => VNode[]; [key: string]: unknown }
    return h(node.type as Component, props, { ...slots, default: () => [icon, ...flatten(slots.default?.() || [])] })
  }
  return h(node.type as Component, props, [icon])
}

export default defineComponent({
  props: {
    registerSecondary: { type: Function as PropType<(element: HTMLElement | null) => void>, required: true },
  },
  setup(props, { slots }) {
    return () => {
      const rawLinks = flatten(slots.default?.() || [])
      const teacherCourseNavigation = rawLinks.some(link => /^\/teacher\/courses\/\d+\//.test(targetPath(link)))
      const links = teacherCourseNavigation
        ? rawLinks.map(link => {
          const icon = navigationIcon(targetPath(link))
          return icon ? withNavigationIcon(link, icon) : link
        })
        : rawLinks
      const current = links.findIndex(link => link.props?.['aria-current'] === 'page')
      links.splice(current < 0 ? links.length : current + 1, 0, h('div', {
        id: 'workspace-secondary-nav',
        key: 'secondary-navigation',
        class: 'kt-workspace-secondary',
        ref: element => props.registerSecondary(element as HTMLElement | null),
      }))
      return h('nav', {
        class: ['kt-workspace-navigation', teacherCourseNavigation && 'kt-workspace-navigation--teacher-course'],
        'aria-label': '课程导航',
      }, links)
    }
  },
})
