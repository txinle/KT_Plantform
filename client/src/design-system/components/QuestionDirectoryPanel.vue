<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'

let media: MediaQueryList | undefined = window.matchMedia?.('(max-width: 1023px)')
const mobile = ref(media?.matches ?? false)
const expanded = ref(false)
function resize() { mobile.value = media?.matches ?? false }
function toggle(event: Event) {
  if (mobile.value) expanded.value = (event.target as HTMLDetailsElement).open
}
onMounted(() => {
  media = window.matchMedia?.('(max-width: 1023px)')
  resize()
  media?.addEventListener('change', resize)
})
onBeforeUnmount(() => media?.removeEventListener('change', resize))
</script>

<template>
  <aside class="kt-card kt-result-question-directory" aria-label="题目目录">
    <details :open="!mobile || expanded" @toggle="toggle">
      <summary :tabindex="mobile ? 0 : -1" @click="!mobile && $event.preventDefault()" @keydown="!mobile && ['Enter', ' '].includes($event.key) && $event.preventDefault()">题目目录</summary>
      <slot />
    </details>
  </aside>
</template>

<style scoped>
summary { min-height: 44px; display: flex; align-items: center; justify-content: space-between; gap: 12px; font-weight: 650; cursor: pointer; }
summary::after { content: '+'; font-size: 20px; }
details[open] > summary::after { content: '−'; }
@media (min-width: 1024px) {
  summary { list-style: none; pointer-events: none; }
  summary::after { display: none; }
}
</style>
