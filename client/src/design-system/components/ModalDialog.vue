<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'
const props = defineProps<{ open: boolean; title: string; wide?: boolean }>()
const emit = defineEmits<{ close: [] }>()
const dialog = ref<HTMLDialogElement>()
let opener: HTMLElement | null = null
function restoreFocus() { if (opener?.isConnected) opener.focus() }
watch(() => props.open, async open => {
  if (open) opener = document.activeElement instanceof HTMLElement ? document.activeElement : null
  await nextTick()
  if (open && props.open) dialog.value?.showModal()
  else if (!props.open && dialog.value?.open) { dialog.value.close(); restoreFocus() }
}, { immediate: true })
onBeforeUnmount(() => { dialog.value?.close(); if (props.open) restoreFocus() })
</script>

<template>
  <dialog ref="dialog" class="kt-site-dialog" :class="{ 'kt-site-dialog--wide': wide }" :aria-label="title" role="dialog" aria-modal="true" @cancel.prevent="emit('close')" @close="!dialog?.open && emit('close')">
    <header class="kt-picker-header"><h2>{{ title }}</h2><button class="kt-picker-close" type="button" aria-label="关闭选择弹窗" @click="emit('close')">×</button></header>
    <div class="kt-picker-body"><slot /></div>
    <footer class="kt-picker-footer"><slot name="footer" /></footer>
  </dialog>
</template>

<style scoped>
.kt-site-dialog{box-sizing:border-box;border:1px solid #e3e9f0;border-radius:12px;padding:0;width:min(650px,calc(100vw - 32px));max-height:85dvh;color:#26364c;background:#fff;box-shadow:0 18px 70px #132a4a26;margin:auto;font:inherit}
.kt-site-dialog[open]{display:flex;flex-direction:column}
.kt-site-dialog::backdrop{background:#15263d60}
.kt-site-dialog--wide{width:min(1060px,calc(100vw - 32px));max-width:none}
.kt-picker-header{padding:22px 24px;display:flex;align-items:center;justify-content:space-between;gap:16px;border-bottom:1px solid #e3e9f0;flex:none}
.kt-picker-header h2{margin:0;font-size:20px;font-weight:650}
.kt-picker-close{border:0;background:transparent;color:#66788f;font-size:24px;line-height:1;min-width:44px;min-height:44px;cursor:pointer}
.kt-picker-body{padding:22px 24px;max-height:62vh;overflow:auto;min-height:0}
.kt-picker-footer{padding:16px 24px;border-top:1px solid #e3e9f0;display:flex;gap:12px;justify-content:flex-end;flex:none;flex-wrap:wrap}
.kt-picker-body :deep(.kt-picker-note){margin:0 0 18px;color:#718096;font-size:14px;line-height:1.7}
.kt-picker-body :deep(.kt-picker-choice){display:flex;align-items:center;gap:14px;border:1px solid #e3e9f0;padding:16px;border-radius:7px;margin-bottom:10px;cursor:pointer}
.kt-picker-body :deep(.kt-picker-choice:has(input:checked)){border-color:#9cb7e0;background:#f7faff}
.kt-picker-body :deep(.kt-picker-choice input){flex:none;accent-color:#285dbb;width:20px;height:20px}
.kt-picker-body :deep(.kt-picker-choice>span:not(.kt-picker-symbol)){display:grid;gap:5px;min-width:0;overflow-wrap:anywhere}
.kt-picker-body :deep(.kt-picker-choice strong){font-size:16px;font-weight:600}
.kt-picker-body :deep(.kt-picker-choice small){color:#637388;font-size:14px}
.kt-picker-body :deep(.kt-picker-choice-count){margin-left:auto;white-space:nowrap}
.kt-picker-body :deep(.kt-picker-symbol){width:36px;height:36px;border-radius:7px;background:#eef3fb;color:#5274a1;display:grid;place-items:center;flex:none}
.kt-picker-body :deep(.kt-picker-search){display:grid;gap:6px;font-size:13px;flex:1;margin-bottom:16px}
.kt-picker-body :deep(.kt-picker-search input){box-sizing:border-box;width:100%;min-width:0;border:1px solid #d9e1ec;border-radius:6px;padding:10px 12px;font:inherit;color:inherit;background:#fff}
.kt-picker-body :deep(.kt-picker-toolbar){display:flex;gap:20px;align-items:center}
.kt-picker-body :deep(.kt-picker-toolbar>span){font-size:14px;white-space:nowrap}
.kt-picker-body :deep(.kt-picker-question){border:1px solid #e3e9f0;border-radius:7px;margin-bottom:10px;overflow:hidden}
.kt-picker-body :deep(.kt-picker-question.selected){border-color:#9cb7e0;background:#f7faff}
.kt-picker-body :deep(.kt-picker-question>.kt-picker-choice){border:0;margin:0;border-radius:0}
.kt-picker-body :deep(.kt-picker-question>details){padding:0 16px 16px}
.kt-picker-body :deep(.kt-picker-question>details>summary){font-size:13px;color:#285dbb;cursor:pointer}
.kt-picker-body :deep(.kt-picker-error){color:#a43737;font-size:14px;line-height:1.7}
@media(max-width:600px){.kt-picker-header,.kt-picker-body{padding:16px}.kt-picker-footer{padding:12px}.kt-picker-header h2{font-size:18px}.kt-picker-body :deep(.kt-picker-toolbar){display:block}.kt-picker-body :deep(.kt-picker-toolbar>span){display:block;margin-bottom:14px}.kt-picker-body :deep(.kt-picker-choice){gap:10px;padding:12px}.kt-picker-body :deep(.kt-picker-choice-count){white-space:normal;max-width:65px;flex:none}}
</style>
