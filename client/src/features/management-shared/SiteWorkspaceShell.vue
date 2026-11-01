<script setup lang="ts">
import { computed, ref } from 'vue'
import { getBootstrapContext } from '@/app/bootstrap'
import { logout } from '@/app/auth'
import SiteRegistrationFooter from '@/app/SiteRegistrationFooter.vue'
import './site-workspace.css'

const props = withDefaults(defineProps<{
  mode: 'admin' | 'course'
  active: string
  courseId?: number
  courseName?: string
  courseMeta?: string
  permissions?: string[]
  showAdmin?: boolean
  mainId?: string
}>(), { courseName: '课程', courseMeta: '', permissions: () => [], showAdmin: undefined, mainId: 'management-main', courseId: undefined })
const bootstrap = getBootstrapContext()
const pending = ref(false)
const error = ref('')
const icons: Record<string, string> = {
  overview: 'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',
  users: 'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2 M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8 M18 8a3 3 0 0 1 0 6 M22 21v-2a4 4 0 0 0-3-3.8',
  course: 'M4 4h16v16H4z M8 4v16 M11 8h6 M11 12h6',
  bank: 'M4 3h12l4 4v14H4z M8 10h8 M8 14h8 M8 18h5',
  experiment: 'M9 3h6 M10 3v7L4 20h16l-6-10V3 M8 14h8',
  data: 'M4 20h16 M6 16V9 M12 16V4 M18 16v-5',
  audit: 'M5 3h14v18H5z M8 8h8 M8 12h8 M8 16h5',
  settings: 'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8 M12 2v3 M12 19v3 M2 12h3 M19 12h3 M5 5l2 2 M17 17l2 2 M5 19l2-2 M17 7l2-2',
}
const navigation = computed(() => {
  if (props.mode === 'admin') return [
    { key: 'overview', label: '管理概览', url: '/manage/' },
    { key: 'users', label: '账号管理', url: '/manage/users/' },
    { key: 'course', label: '课程与班级', url: '/manage/courses/' },
    { key: 'bank', label: '教学资源', url: '/manage/resources/' },
    { key: 'data', label: '实验与数据', url: '/manage/data/' },
    { key: 'audit', label: '操作记录', url: '/manage/audit/' },
  ]
  const base = `/teacher/courses/${props.courseId}/`
  return [
    { key: 'overview', label: '课程概览', url: base, permission: '' },
    { key: 'bank', label: '题库', url: `${base}banks/`, permission: 'bank' },
    { key: 'users', label: '班级', url: `${base}classes/`, permission: 'class' },
    { key: 'experiment', label: '实验', url: `${base}experiments/`, permission: 'experiment' },
    { key: 'data', label: '数据', url: `${base}data/`, permission: 'data' },
    { key: 'export', label: '数据导出', url: `${base}data/exports/`, permission: 'export' },
    { key: 'settings', label: '课程设置', url: `${base}settings/`, permission: '' },
  ].filter(item => !item.permission || props.permissions.includes(item.permission))
})
async function signOut() {
  if (pending.value) return
  pending.value = true
  error.value = ''
  try { await logout(); window.location.assign('/login/') }
  catch (caught) { error.value = caught instanceof Error ? caught.message : '退出失败，请重试。' }
  finally { pending.value = false }
}
</script>

<template>
  <div class="management-workspace">
    <a class="management-skip" :href="`#${mainId}`">跳到主要内容</a>
    <header class="topbar">
      <a class="brand" :href="bootstrap.user.home_url" aria-label="KT Data Lab 首页"><b>KT</b><strong>KT Data Lab</strong></a>
      <div class="context">{{ mode === 'admin' ? '平台管理' : '当前课程' }} <span>/</span> {{ mode === 'admin' ? '管理员工作台' : courseName }}</div>
      <div class="identity"><span class="avatar">{{ bootstrap.user.display_name.slice(0, 1) }}</span><div><strong>{{ bootstrap.user.display_name }}</strong><small>{{ bootstrap.user.username }} · {{ mode === 'admin' ? '平台管理权限' : '课程工作区' }}</small></div></div>
      <a class="password-change" data-password-change-link href="/password/change/">修改密码</a>
      <button class="link sign-out" :disabled="pending" @click="signOut">{{ pending ? '退出中…' : '退出' }}</button>
    </header>
    <aside class="workspace-sidebar">
      <div class="aside-title"><strong>{{ mode === 'admin' ? '管理员工作台' : courseName }}</strong><small>{{ mode === 'admin' ? '全平台 · 业务管理' : courseMeta }}</small></div>
      <div class="nav-caption">{{ mode === 'admin' ? '平台管理' : '教学工作区' }}</div>
      <nav aria-label="工作区导航"><a v-for="item in navigation" :key="item.key" class="nav-item" :class="{ active: active === item.key }" :href="item.url" :aria-current="active === item.key ? 'page' : undefined"><svg viewBox="0 0 24 24" aria-hidden="true"><path :d="icons[item.key] || icons.data" stroke-linecap="round" stroke-linejoin="round" /></svg>{{ item.label }}</a></nav>
      <SiteRegistrationFooter sidebar />
    </aside>
    <main :id="mainId" class="workspace-content" tabindex="-1"><p v-if="error" class="error-block" role="alert">{{ error }}</p><slot /></main>
  </div>
</template>
