<script setup lang="ts">
import { computed, nextTick, onMounted, onBeforeUnmount, ref, watch } from 'vue'
import Icon from '@/design-system/Icon.vue'
import KcNodeCard from './KcNodeCard.vue'

type Node = { kc_code: string; parent_kc_code: string | null; order: number }
type Mapping = { root_kc_code: string; nodes: Node[] }
const props = defineProps<{ modelValue: Mapping; concepts: { kc_code: string; name: string }[]; error?: string }>()
const emit = defineEmits<{ 'update:modelValue': [mapping: Mapping] }>()
const canvas = ref<HTMLElement>()
const expanded = ref(false)
const editing = ref(false)
const dialog = ref<HTMLDialogElement>()
watch(() => props.error, error => { if (error) expanded.value = true }, { immediate: true })
const nodes = computed(() => props.modelValue.nodes)
const root = computed(() => props.modelValue.root_kc_code)
const usedCodes = computed(() => nodes.value.map(n => n.kc_code))
const canAdd = computed(() => props.concepts.some(c => !usedCodes.value.includes(c.kc_code)))
const name = (code: string) => props.concepts.find(c => c.kc_code === code)?.name || code
const children = (code: string) => nodes.value.filter(n => n.parent_kc_code === code).slice().sort((a, b) => a.order - b.order)
const stackSize = computed(() => children(root.value).reduce((sum, branch) => sum + Math.max(1, children(branch.kc_code).length), 0))
const connectors = ref<{ key: string; path: string; x1: number; y1: number; x2: number; y2: number }[]>([])
let observer: ResizeObserver | undefined
let disposed = false
function measureConnections() {
  if (!canvas.value) return
  const area = canvas.value.getBoundingClientRect()
  if (!area.width) return
  const boxes = new Map(Array.from(canvas.value.querySelectorAll<HTMLElement>('[data-code]')).map(element => [element.dataset.code, element.getBoundingClientRect()]))
  const mobile = editing.value && window.innerWidth < 1024
  const single = stackSize.value === 1
  connectors.value = nodes.value.flatMap(node => {
    if (!node.parent_kc_code) return []
    const parent = boxes.get(node.parent_kc_code), child = boxes.get(node.kc_code)
    if (!parent || !child) return []
    let x1: number, y1: number, x2: number, y2: number, path: string
    if (mobile) {
      x1 = parent.left - area.left + (single ? parent.width / 2 : 12)
      y1 = parent.bottom - area.top
      x2 = child.left - area.left + (single ? child.width / 2 : 0)
      y2 = child.top - area.top + (single ? 0 : child.height / 2)
      path = single ? `M ${x1} ${y1} L ${x2} ${y2}` : `M ${x1} ${y1} V ${y2 - 6} Q ${x1} ${y2} ${x1 + 6} ${y2} H ${x2}`
    } else {
      x1 = parent.right - area.left; y1 = parent.top + parent.height / 2 - area.top
      x2 = child.left - area.left; y2 = child.top + child.height / 2 - area.top
      const mid = (x1 + x2) / 2, direction = Math.sign(y2 - y1), radius = Math.min(8, Math.abs(y2 - y1) / 2)
      path = `M ${x1} ${y1} H ${mid - radius} Q ${mid} ${y1} ${mid} ${y1 + direction * radius} V ${y2 - direction * radius} Q ${mid} ${y2} ${mid + radius} ${y2} H ${x2}`
    }
    return [{ key: node.kc_code, path, x1, y1, x2, y2 }]
  })
}
async function observeCards() {
  await nextTick()
  if (disposed || !canvas.value) return
  observer?.disconnect()
  observer?.observe(canvas.value)
  canvas.value.querySelectorAll('[data-code]').forEach(element => observer?.observe(element))
  measureConnections()
}
onMounted(() => {
  if (typeof ResizeObserver !== 'undefined') observer = new ResizeObserver(measureConnections)
  window.addEventListener('resize', measureConnections)
  void observeCards()
})
watch([() => props.modelValue, () => props.concepts], observeCards, { deep: true, flush: 'post' })
onBeforeUnmount(() => { disposed = true; observer?.disconnect(); window.removeEventListener('resize', measureConnections) })
function descendants(code: string, found = new Set<string>()): Set<string> {
  if (found.has(code)) return found
  found.add(code)
  children(code).forEach(child => descendants(child.kc_code, found))
  return found
}
function depth(code: string, seen = new Set<string>()): number {
  if (seen.has(code)) return 4
  seen.add(code)
  const node = nodes.value.find(n => n.kc_code === code)
  return node?.parent_kc_code ? 1 + depth(node.parent_kc_code, seen) : 1
}
function parents(node: Node) {
  const excluded = descendants(node.kc_code)
  const height = Math.max(...[...excluded].map(code => depth(code))) - depth(node.kc_code)
  return nodes.value.filter(n => !excluded.has(n.kc_code) && depth(n.kc_code) + 1 + height <= 3).map(n => ({ kc_code: n.kc_code, name: name(n.kc_code) }))
}
function write(next: Node[], nextRoot = root.value) { emit('update:modelValue', { root_kc_code: nextRoot, nodes: next }) }
function replaceCode(oldCode: string, code: string) {
  if (oldCode === code) return
  if (!code) { write([], ''); return }
  if (usedCodes.value.includes(code)) return
  if (!oldCode) { write([{ kc_code: code, parent_kc_code: null, order: 1 }], code); return }
  write(nodes.value.map(n => ({ ...n, kc_code: n.kc_code === oldCode ? code : n.kc_code, parent_kc_code: n.parent_kc_code === oldCode ? code : n.parent_kc_code })), oldCode === root.value ? code : root.value)
}
function add(parent: string) {
  const concept = props.concepts.find(c => !usedCodes.value.includes(c.kc_code))
  if (!concept || depth(parent) >= 3) return
  write([...nodes.value, { kc_code: concept.kc_code, parent_kc_code: parent, order: Math.max(0, ...children(parent).map(n => n.order)) + 1 }])
}
function remove(code: string) { const removed = descendants(code); write(nodes.value.filter(n => !removed.has(n.kc_code))) }
function update(node: Node, patch: Partial<Node>) { write(nodes.value.map(n => n.kc_code === node.kc_code ? { ...n, ...patch } : n)) }
function describe(code: string, seen = new Set<string>()): string {
  if (seen.has(code)) return name(code)
  const nextSeen = new Set(seen).add(code)
  const branch = children(code)
  return name(code) + (branch.length ? ` → ${branch.length > 1 ? '{ ' : ''}${branch.map(n => describe(n.kc_code, nextSeen)).join('；')}${branch.length > 1 ? ' }' : ''}` : '')
}
async function openTree(edit = false) {
  editing.value = edit
  await nextTick()
  dialog.value?.showModal()
  void observeCards()
}
watch(editing, observeCards)

</script>

<template>
  <section :class="['kt-card kt-kc-editor', { 'is-compact': stackSize > 1, 'is-dense': stackSize >= 4 }]" aria-labelledby="kc-mapping-title">
    <button type="button" class="kt-editor-section-toggle" data-kc-toggle :aria-expanded="expanded" aria-controls="kc-mapping-actions" @click="expanded = !expanded"><h2 id="kc-mapping-title">课程 KC 映射</h2><span class="kt-editor-section-summary" /><span>{{ expanded ? '收起' : '点击展开' }}</span><Icon name="chevron-down" :size="16" :class="{ 'is-expanded': expanded }" /></button>
    <div class="kt-kc-editor-summary"><strong>当前结构：</strong><span>{{ root ? describe(root) : '未映射' }}</span><button type="button" class="kt-button kt-button--secondary" data-preview @click="openTree()">查看结构图</button></div>
    <div v-show="expanded" id="kc-mapping-actions" class="kt-editor-section-body"><p>从课程 KC 总库逐级建立题目结构。编辑后需保存题目。</p><div class="kt-inline-actions"><button type="button" class="kt-button kt-button--secondary" data-edit-mapping @click="openTree(true)">编辑关联</button><button type="button" class="kt-button kt-button--secondary kt-kc-clear" data-clear @click="write([], '')">清空关联</button></div></div>
    <p v-if="error" role="alert" class="kt-field__error">{{ error }}</p>
    <dialog ref="dialog" class="kt-kc-dialog" :class="{ 'is-editing': editing }" aria-labelledby="kc-dialog-title" @close="editing = false">
      <header><div><h2 id="kc-dialog-title">{{ editing ? '编辑 KC 关联' : '结构图预览' }}</h2><p>{{ editing ? '修改保留在当前表单，保存题目后生效。' : '查看当前题目的 KC 层级关系' }}</p></div><button type="button" class="kt-button kt-button--secondary" aria-label="关闭结构图" @click="dialog?.close()">×</button></header>
      <div class="kt-kc-dialog-scroll">
        <div ref="canvas" class="kt-kc-editor-canvas" :class="{ 'is-single-path': children(root).length === 1 && children(children(root)[0]!.kc_code).length <= 1 }" tabindex="-1" aria-label="课程 KC 结构图">
          <svg class="kt-kc-connectors" aria-hidden="true"><g v-for="line in connectors" :key="line.key"><path :d="line.path" /><circle :cx="line.x1" :cy="line.y1" r="3" /><circle :cx="line.x2" :cy="line.y2" r="3" /></g></svg>
          <KcNodeCard :read-only="!editing" :code="root" :name="name(root)" :level="1" :order="1" :parent="null" :parents="[]" :concepts="concepts" :used-codes="usedCodes" :can-add="canAdd" @change="replaceCode(root, $event)" @add="add(root)" />
          <div class="kt-kc-editor-branches">
            <div v-for="branch in children(root)" :key="branch.kc_code" class="kt-kc-editor-branch">
              <KcNodeCard :read-only="!editing" :code="branch.kc_code" :name="name(branch.kc_code)" :level="2" :order="branch.order" :parent="branch.parent_kc_code" :parents="parents(branch)" :concepts="concepts" :used-codes="usedCodes" :can-add="canAdd" @change="replaceCode(branch.kc_code, $event)" @add="add(branch.kc_code)" @remove="remove(branch.kc_code)" @parent="update(branch, { parent_kc_code: $event })" @order="update(branch, { order: $event })" />
              <div class="kt-kc-editor-leaves">
                <KcNodeCard v-for="leaf in children(branch.kc_code)" :key="leaf.kc_code" :read-only="!editing" :code="leaf.kc_code" :name="name(leaf.kc_code)" :level="3" :order="leaf.order" :parent="leaf.parent_kc_code" :parents="parents(leaf)" :concepts="concepts" :used-codes="usedCodes" :can-add="false" @change="replaceCode(leaf.kc_code, $event)" @remove="remove(leaf.kc_code)" @parent="update(leaf, { parent_kc_code: $event })" @order="update(leaf, { order: $event })" />
              </div>
            </div>
            <p v-if="!children(root).length" class="kt-kc-editor-empty">{{ editing ? (root ? '点击“添加二级 KC”建立分支。' : '请选择一级主 KC。') : (root ? '暂无子级 KC。' : '当前题目尚未关联 KC。') }}</p>
          </div>
        </div>
      </div>
      <p v-if="!editing" class="kt-kc-pan-hint">左右滑动查看完整结构。</p>
      <footer><button v-if="!editing" type="button" class="kt-button kt-button--secondary" @click="editing = true">编辑关联</button><button type="button" class="kt-button kt-button--secondary" data-modal-close @click="dialog?.close()">{{ editing ? '完成编辑' : '关闭' }}</button></footer>
    </dialog>
  </section>
</template>

<style scoped>
.kt-kc-editor { --node-pad: 12px; --node-gap: 10px; --node-font: 15px; --node-control: 36px; --tree-gap: 20px; display: grid; gap: 16px; min-width: 0; }
.kt-kc-editor.is-compact { --node-pad: 10px; --node-gap: 8px; --node-font: 14px; --node-control: 34px; --tree-gap: 16px; }
.kt-kc-editor.is-dense { --node-pad: 8px; --node-gap: 6px; --node-control: 32px; --tree-gap: 12px; }
.kt-kc-clear { color: var(--kt-danger); border-color: var(--kt-danger); }
.kt-kc-editor-canvas { position: relative; display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 2fr); gap: 40px; align-items: center; outline-offset: 8px; }
.kt-kc-connectors { position: absolute; z-index: 1; inset: 0; width: 100%; height: 100%; overflow: visible; pointer-events: none; color: var(--kt-action-600); }
.kt-kc-connectors path { fill: none; stroke: currentColor; stroke-width: 1.5px; }
.kt-kc-connectors circle { fill: currentColor; stroke: var(--kt-surface); stroke-width: 1px; }
.kt-kc-editor-branches, .kt-kc-editor-leaves { display: grid; gap: var(--tree-gap); min-width: 0; }
.kt-kc-editor-branch { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 40px; align-items: center; }
:deep(.kt-kc-editor-node) { position: relative; display: grid; gap: var(--node-gap); min-width: 0; padding: 0 var(--node-pad) var(--node-pad); border: 1px solid color-mix(in srgb, var(--kt-action-600) 32%, white); border-radius: 10px; background: var(--kt-surface); }
:deep(.kt-kc-editor-node__heading) { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 6px; min-height: 32px; margin: 0 calc(-1 * var(--node-pad)); padding: 4px var(--node-pad); border-radius: 9px 9px 0 0; background: var(--kt-selection-bg, #f0efff); color: var(--kt-muted); font-size: 14px; }
:deep(.kt-kc-editor-node__name) { overflow-wrap: anywhere; font-size: var(--node-font); line-height: 1.45; }
:deep(.kt-kc-editor-node select), :deep(.kt-kc-editor-node input) { min-width: 0; max-width: 100%; min-height: var(--node-control); padding: 6px 8px; font-size: 14px; line-height: 1.4; }
:deep(.kt-kc-editor-node button), :deep(.kt-kc-editor-node summary) { min-height: var(--node-control); font-size: 14px; line-height: 1.4; }
:deep(.kt-kc-editor-node [data-remove]) { display: inline-flex; align-items: center; gap: 4px; min-height: 28px; border: 0; background: transparent; padding: 4px 0; color: var(--kt-danger); cursor: pointer; }
:deep(.kt-kc-editor-add) { border: 0; border-radius: 6px; padding: 6px 10px; text-align: left; color: var(--kt-action-600); background: var(--kt-selection-bg, #f0efff); font-weight: 600; }
:deep(.kt-kc-editor-add:disabled) { opacity: .5; }
:deep(.kt-kc-editor-details) { border-top: 1px solid var(--kt-border); padding-top: 4px; }
:deep(.kt-kc-editor-details summary) { display: list-item; padding: 6px 0; color: var(--kt-muted); cursor: pointer; }
:deep(.kt-kc-editor-details label) { display: grid; gap: 6px; margin-bottom: 8px; font-size: 14px; }
.kt-kc-editor-summary { display: flex; flex-wrap: wrap; gap: 8px; padding: 12px; border-radius: 8px; background: var(--kt-selection-bg, #f0efff); font-size: 14px; overflow-wrap: anywhere; }
.kt-kc-editor-summary span { min-width: 0; flex: 1; }
.kt-kc-editor-summary { align-items: center; }
.kt-kc-editor-empty { color: var(--kt-muted); font-size: 14px; }
@media (max-width: 1023px) {
  .kt-kc-editor, .kt-kc-editor.is-compact, .kt-kc-editor.is-dense { --node-control: 44px; --node-pad: 12px; }
  .is-editing .kt-kc-editor-canvas, .is-editing .kt-kc-editor-branch { grid-template-columns: minmax(0, 1fr); gap: 20px; }
  .is-editing .kt-kc-editor-branches, .is-editing .kt-kc-editor-leaves { padding-left: 24px; }
  .is-editing .is-single-path .kt-kc-editor-branches, .is-editing .is-single-path .kt-kc-editor-leaves { padding: 0; }
  :deep(.kt-kc-editor-node [data-remove]) { min-height: 44px; }
}
.kt-kc-dialog { width: min(1100px, calc(100vw - 32px)); max-width: none; max-height: calc(100dvh - 32px); box-sizing: border-box; padding: 20px; border: 1px solid var(--kt-border); border-radius: 10px; background: var(--kt-surface); color: var(--kt-ink); box-shadow: 0 16px 48px #24242933; }
.kt-kc-dialog::backdrop { background: #24242980; }
.kt-kc-dialog header { display: flex; justify-content: space-between; align-items: start; gap: 16px; margin-bottom: 20px; }
.kt-kc-dialog h2 { margin: 0; font-size: 18px; }
.kt-kc-dialog p { margin: 6px 0 0; color: var(--kt-muted); font-size: 14px; }
.kt-kc-dialog-scroll { overflow: auto; max-height: 62dvh; padding: 4px; }
.kt-kc-dialog:not(.is-editing) .kt-kc-editor-canvas { min-width: 680px; }
.kt-kc-dialog footer { display: flex; justify-content: center; gap: 12px; border-top: 1px solid var(--kt-border); margin-top: 20px; padding-top: 16px; }
:deep(.kt-kc-editor-node.is-readonly) { padding-bottom: 12px; min-height: 80px; background: var(--kt-selection-bg, #f0efff); }
.kt-kc-pan-hint { display: none; }
@media (max-width: 767px) { .kt-kc-dialog { padding: 16px; } .kt-kc-pan-hint { display: block; } }
</style>
