<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'

const props = defineProps<{ open: boolean; title: string; id?: string }>()
const emit = defineEmits<{ close: [] }>()
const panel = ref<{ focus: () => void }>()

watch(
  () => props.open,
  async (open) => {
    if (!open) return
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
    >
      <button type="button" :aria-label="`关闭${title}`" @click="emit('close')">关闭</button>
      <slot />
    </aside>
  </div>
</template>
