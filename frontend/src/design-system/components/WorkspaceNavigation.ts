import { Fragment, defineComponent, h, type PropType, type VNode } from 'vue'

function flatten(nodes: VNode[]): VNode[] {
  return nodes.flatMap(node => node.type === Fragment && Array.isArray(node.children)
    ? flatten(node.children as VNode[]) : [node])
}

export default defineComponent({
  props: {
    registerSecondary: { type: Function as PropType<(element: HTMLElement | null) => void>, required: true },
  },
  setup(props, { slots }) {
    return () => {
      const links = flatten(slots.default?.() || [])
      const current = links.findIndex(link => link.props?.['aria-current'] === 'page')
      links.splice(current < 0 ? links.length : current + 1, 0, h('div', {
        id: 'workspace-secondary-nav',
        key: 'secondary-navigation',
        class: 'kt-workspace-secondary',
        ref: element => props.registerSecondary(element as HTMLElement | null),
      }))
      return h('nav', { class: 'kt-workspace-navigation', 'aria-label': '课程导航' }, links)
    }
  },
})
