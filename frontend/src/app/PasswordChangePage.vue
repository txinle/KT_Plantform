<script setup lang="ts">
import { reactive, ref } from 'vue'

import { ApiError } from '@/api/client'

import { changePassword, logout, redirectPasswordChangeAuthenticationRequired } from './auth'

const passwords = reactive({ new_password1: '', new_password2: '' })
const errors = ref<string[]>([])
const submitting = ref(false)
const loggingOut = ref(false)

function errorMessages(caught: unknown, fallback: string): string[] {
  if (caught instanceof ApiError) {
    const messages = ['new_password1', 'new_password2', 'non_field_errors'].flatMap((key) => {
      const value = caught.fields[key]
      if (typeof value === 'string') return [value]
      return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
    })
    if (messages.length) return [...new Set(messages)]
  }
  return [caught instanceof Error ? caught.message : fallback]
}

async function submit() {
  submitting.value = true
  errors.value = []
  try {
    const context = await changePassword(passwords.new_password1, passwords.new_password2)
    window.location.assign(context.user.home_url)
  } catch (caught) {
    if (redirectPasswordChangeAuthenticationRequired(caught)) return
    errors.value = errorMessages(caught, '密码修改失败，请稍后重试。')
  } finally {
    submitting.value = false
  }
}

async function signOut() {
  loggingOut.value = true
  errors.value = []
  try {
    await logout()
    window.location.assign('/login/')
  } catch (caught) {
    errors.value = errorMessages(caught, '退出登录失败，请稍后重试。')
    loggingOut.value = false
  }
}
</script>

<template>
  <main class="kt-password-change-page">
    <section class="kt-password-change-card kt-card kt-card--raised" role="dialog" aria-modal="true" aria-labelledby="password-change-title" aria-describedby="password-change-description">
      <p class="kt-kicker">账号安全</p>
      <h1 id="password-change-title">修改初始密码</h1>
      <p id="password-change-description" class="kt-password-change-card__intro">首次登录需要设置仅由你本人掌握的新密码，完成后才能进入课程。</p>
      <div v-if="errors.length" class="kt-alert kt-alert--danger" role="alert">
        <ul>
          <li v-for="item in errors" :key="item">{{ item }}</li>
        </ul>
      </div>
      <form @submit.prevent="submit">
        <div class="kt-field">
          <label for="new-password-1">新密码</label>
          <input id="new-password-1" v-model="passwords.new_password1" name="new_password1" type="password" minlength="8" autocomplete="new-password" required :disabled="submitting || loggingOut">
          <small>至少 8 个字符。</small>
        </div>
        <div class="kt-field">
          <label for="new-password-2">再次输入新密码</label>
          <input id="new-password-2" v-model="passwords.new_password2" name="new_password2" type="password" minlength="8" autocomplete="new-password" required :disabled="submitting || loggingOut">
        </div>
        <button class="kt-button kt-password-change-card__submit" type="submit" :disabled="submitting || loggingOut">
          {{ submitting ? '正在保存…' : '保存新密码并继续' }}
        </button>
      </form>
      <button class="kt-button kt-button--secondary kt-password-change-card__logout" data-password-change-logout type="button" :disabled="submitting || loggingOut" @click="signOut">
        {{ loggingOut ? '正在退出…' : '退出登录' }}
      </button>
    </section>
  </main>
</template>

<style scoped>
.kt-password-change-page {
  display: grid;
  min-height: 100vh;
  place-items: center;
  padding: 1.5rem;
  background: #f3f6fa;
}

.kt-password-change-card {
  width: min(100%, 31rem);
  padding: 2rem;
}

.kt-password-change-card h1,
.kt-password-change-card__intro {
  margin-top: 0;
}

.kt-password-change-card__intro,
.kt-password-change-card small {
  color: var(--kt-color-muted);
}

.kt-password-change-card form {
  display: grid;
  gap: 1rem;
}

.kt-password-change-card__submit,
.kt-password-change-card__logout {
  width: 100%;
}

.kt-password-change-card__submit {
  margin-top: .5rem;
}

.kt-password-change-card__logout {
  margin-top: .75rem;
}

@media (max-width: 420px) {
  .kt-password-change-page {
    padding: 1rem;
  }

  .kt-password-change-card {
    padding: 1.5rem;
  }
}
</style>
