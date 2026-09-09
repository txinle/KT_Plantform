<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRoute } from 'vue-router'

import { ApiError } from '@/api/client'

import { login, loginDestination } from './auth'

const route = useRoute()
const credentials = reactive({ username: '', password: '' })
const error = ref('')
const submitting = ref(false)

async function submit() {
  submitting.value = true
  error.value = ''
  try {
    const context = await login(credentials.username, credentials.password)
    window.location.assign(loginDestination(context.user, route.query.next))
  } catch (caught) {
    error.value = caught instanceof ApiError || caught instanceof Error
      ? caught.message
      : '登录失败，请稍后重试。'
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <main class="kt-login-page">
    <section class="kt-login-card kt-card kt-card--raised" aria-labelledby="login-title">
      <p class="kt-kicker">教育科研数据采集</p>
      <h1 id="login-title">登录 KT Data Lab</h1>
      <p class="kt-login-card__intro">学生使用学号，教师使用教师账号。</p>
      <ul class="kt-login-card__principles" aria-label="平台数据边界">
        <li>发布后使用不可变题目快照</li>
        <li>实名主导出仅限授权场景，导出文件均包含学生学号和姓名</li>
        <li>草稿保存与正式提交严格区分</li>
      </ul>
      <p v-if="error" class="kt-alert kt-alert--danger" role="alert">{{ error }}</p>
      <form @submit.prevent="submit">
        <div class="kt-field">
          <label for="login-username">账号或学号</label>
          <input id="login-username" v-model="credentials.username" name="username" autocomplete="username" required>
        </div>
        <div class="kt-field">
          <label for="login-password">密码</label>
          <input id="login-password" v-model="credentials.password" name="password" type="password" autocomplete="current-password" required>
        </div>
        <button class="kt-button kt-login-card__submit" type="submit" :disabled="submitting">
          {{ submitting ? '正在登录…' : '登录' }}
        </button>
      </form>
    </section>
  </main>
</template>

<style scoped>
.kt-login-page { display: grid; min-height: 100vh; place-items: center; padding: 1.5rem; }
.kt-login-card { width: min(100%, 29rem); }
.kt-login-card__intro { color: var(--kt-color-muted); }
.kt-login-card__principles { margin: 0 0 1.25rem; padding-left: 1.25rem; color: var(--kt-color-muted); }
.kt-login-card__principles li + li { margin-top: .35rem; }
.kt-login-card form { display: grid; gap: 1rem; }
.kt-login-card__submit { width: 100%; margin-top: 0.5rem; }
</style>
