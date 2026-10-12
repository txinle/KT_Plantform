<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'

const props = defineProps<{ open: boolean; title: string; id?: string }>()
const emit = defineEmits<{ close: [] }>()
const panel = ref<HTMLElement>()
let returnFocus: HTMLElement | null = null

function restoreFocus() {
  if (returnFocus?.isConnected) returnFocus.focus()
  returnFocus = null
}
function trapFocus(event: KeyboardEvent) {
  const controls = Array.from(panel.value?.querySelectorAll<HTMLElement>('a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex="0"]') || [])
  const first = controls[0]
  const last = controls.at(-1)
  if (event.shiftKey && (document.activeElement === first || document.activeElement === panel.value)) {
    event.preventDefault(); last?.focus()
  } else if (!event.shiftKey && (document.activeElement === last || document.activeElement === panel.value)) {
    event.preventDefault(); first?.focus()
  }
}
onBeforeUnmount(restoreFocus)

watch(
  () => props.open,
  async (open) => {
    if (!open) { restoreFocus(); return }
    returnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
    await nextTick()
    panel.value?.focus()
  },
  { immediate: true },
)
</script>

<template>
  <div
    v-if="open"
    class="kt-drawer-backdrop"
    data-drawer-backdrop
    @click.self="emit('close')"
  >
    <aside
      :id="id"
      ref="panel"
      class="kt-drawer"
      role="dialog"
      aria-modal="true"
      :aria-label="title"
      tabindex="-1"
      @keydown.esc.stop="emit('close')"
      @keydown.tab="trapFocus"
    >
      <button type="button" :aria-label="`关闭${title}`" @click="emit('close')">关闭</button>
      <slot />
    </aside>
  </div>
</template>
