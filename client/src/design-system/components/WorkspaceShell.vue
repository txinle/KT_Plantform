<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, provide, ref, watch } from 'vue'
import AppHeader from './AppHeader.vue'
import InitialPasswordReminder from '@/app/InitialPasswordReminder.vue'
import WorkspaceNavigation from './WorkspaceNavigation'
import '../workspace.css'

withDefaults(defineProps<{
  userName: string
  userMeta?: string | null
  csrfToken: string
  courseName?: string
  courseMeta?: string
  mainId: string
  passwordChangeUrl?: string | null
  initialPasswordReminderPending?: boolean
}>(), {
  userMeta: null,
  courseName: undefined,
  courseMeta: undefined,
  passwordChangeUrl: null,
  initialPasswordReminderPending: false,
})
const secondary = ref<HTMLElement | null>(null)
provide('workspace-secondary-nav', secondary)
const mobile = ref(false)
const open = ref(false)
const panel = ref<HTMLElement>()
const trigger = ref<HTMLButtonElement>()
const closeButton = ref<HTMLButtonElement>()
let media: MediaQueryList | undefined
let previousOverflow = ''

async function close() {
  if (!open.value) return
  open.value = false
  await nextTick()
  trigger.value?.focus()
}
function resize() {
  mobile.value = media?.matches ?? false
  if (!mobile.value) open.value = false
}
function keydown(event: KeyboardEvent) {
  if (!mobile.value || !open.value) return
  if (event.key === 'Escape') { event.preventDefault(); void close(); return }
  if (event.key !== 'Tab') return
  const controls = Array.from(panel.value?.querySelectorAll<HTMLElement>('a[href], button:not([disabled]), [tabindex="0"]') || [])
  const first = controls[0]
  const last = controls.at(-1)
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
}
function navigationClick(event: MouseEvent) {
  if ((event.target as HTMLElement).closest('a, [role="tab"], nav button')) void close()
}
watch(open, async (value) => {
  if (value) {
    previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    await nextTick()
    closeButton.value?.focus()
  } else document.body.style.overflow = previousOverflow
})
onMounted(() => {
  media = window.matchMedia?.('(max-width: 1023px)')
  resize()
  media?.addEventListener('change', resize)
})
onBeforeUnmount(() => {
  media?.removeEventListener('change', resize)
  if (open.value) document.body.style.overflow = previousOverflow
})
</script>

<template>
  <div class="kt-workspace">
    <a class="kt-skip-link" :href="`#${mainId}`">跳到主要内容</a>
    <AppHeader :user-name="userName" :user-meta="userMeta" :csrf-token="csrfToken" :password-change-url="passwordChangeUrl" show-logout :inert="mobile && open">
      <button ref="trigger" type="button" class="kt-workspace-menu" data-workspace-menu aria-controls="workspace-sidebar" :aria-expanded="open" @click="open = true">菜单</button>
    </AppHeader>
    <InitialPasswordReminder v-if="initialPasswordReminderPending" />
    <div v-if="mobile && open" class="kt-workspace-shade" @click="close" />
    <aside id="workspace-sidebar" ref="panel" class="kt-workspace-sidebar" :class="{ 'is-open': open }" :inert="mobile && !open" :role="mobile && open ? 'dialog' : undefined" :aria-modal="mobile && open ? true : undefined" aria-label="工作区导航" @keydown="keydown" @click="navigationClick">
      <button ref="closeButton" type="button" class="kt-workspace-close" data-workspace-close @click="close">关闭菜单</button>
      <div class="kt-workspace-context">
        <span class="kt-workspace-emblem" aria-hidden="true">{{ courseName ? '课' : userName.slice(0, 1) }}</span>
        <strong>{{ courseName || userName }}</strong>
        <span v-if="courseMeta || userMeta">{{ courseMeta || userMeta }}</span>
      </div>
      <WorkspaceNavigation :register-secondary="element => secondary = element">
        <slot name="navigation" />
      </WorkspaceNavigation>
    </aside>
    <main :id="mainId" class="kt-workspace-main" tabindex="-1" :inert="mobile && open"><slot /></main>
  </div>
</template>
