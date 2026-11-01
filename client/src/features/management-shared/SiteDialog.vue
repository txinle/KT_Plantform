<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch, useId } from 'vue'
const props = defineProps<{ open: boolean; title: string; subtitle?: string; wide?: boolean }>()
const emit = defineEmits<{ close: [] }>()
const titleId = useId()
const dialog = ref<HTMLDialogElement>()
let opener: HTMLElement | null = null
watch(() => props.open, async open => {
  if (open) opener = document.activeElement instanceof HTMLElement ? document.activeElement : null
  await nextTick()
  if (open && props.open) dialog.value?.showModal()
  else if (!props.open && dialog.value?.open) { dialog.value.close(); opener?.focus() }
}, { immediate: true })
onBeforeUnmount(() => { dialog.value?.close(); if (props.open && opener?.isConnected) opener.focus() })
</script>

<template>
  <dialog ref="dialog" class="management-dialog" :class="{ 'management-dialog-wide': wide }" :aria-labelledby="titleId" aria-modal="true" @cancel.prevent="emit('close')" @close="open && !dialog?.open && emit('close')">
    <div class="modal-head"><div><h2 :id="titleId">{{ title }}</h2><p v-if="subtitle">{{ subtitle }}</p></div><button class="close" type="button" aria-label="关闭弹窗" @click="emit('close')">×</button></div>
    <div class="modal-content"><slot /></div>
    <div v-if="$slots.footer" class="modal-footer"><slot name="footer" /></div>
  </dialog>
</template>
