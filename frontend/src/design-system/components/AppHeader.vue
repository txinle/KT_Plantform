<script setup lang="ts">
import { ref } from 'vue'
import { logout } from '@/app/auth'

const pending = ref(false)
const error = ref('')
async function signOut() {
  if (pending.value) return
  pending.value = true
  error.value = ''
  try {
    await logout()
    window.location.assign('/login/')
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '退出失败，请重试。'
  } finally {
    pending.value = false
  }
}
withDefaults(defineProps<{
  userName: string
  userMeta?: string | null
  csrfToken?: string
  showLogout?: boolean
  passwordChangeUrl?: string | null
}>(), {
  userMeta: null,
  csrfToken: '',
  showLogout: false,
  passwordChangeUrl: null,
})
</script>

<template>
  <header class="kt-app-header">
    <div class="kt-shell kt-app-header__inner">
      <a class="kt-wordmark" href="/" aria-label="KT Data Lab 首页">
        <span class="kt-brand-mark" aria-hidden="true">KT</span>
        <span class="kt-wordmark__name">KT Data Lab</span>
      </a>
      <nav class="kt-global-nav" aria-label="全局导航"><slot /></nav>
      <div class="kt-account-actions">
        <span class="kt-user-name">
          <strong :title="userName">{{ userName }}</strong>
          <small v-if="userMeta" :title="userMeta">· {{ userMeta }}</small>
        </span>
        <a
          v-if="passwordChangeUrl"
          class="kt-logout-button kt-account-link"
          data-password-change-link
          :href="passwordChangeUrl"
        >修改密码</a>
        <form v-if="showLogout" method="post" action="/logout/" @submit.prevent="signOut">
          <input type="hidden" name="csrfmiddlewaretoken" :value="csrfToken">
          <button class="kt-logout-button" type="submit" :disabled="pending">{{ pending ? '正在退出…' : '退出' }}</button>
        </form>
        <span v-if="error" role="alert">{{ error }}</span>
      </div>
    </div>
  </header>
</template>
