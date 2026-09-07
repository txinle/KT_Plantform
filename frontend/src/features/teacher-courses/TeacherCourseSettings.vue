<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import { ApiError } from '@/api/client'
import { getBootstrapContext } from '@/app/bootstrap'
import WorkspaceShell from '@/design-system/components/WorkspaceShell.vue'

import type { TeacherCourseSettingsResponse } from './api'
import { getTeacherCourseApi } from './api'
import './teacher-courses.css'

const bootstrap = getBootstrapContext()
const route = useRoute()
const courseId = Number(route.params.courseId)
const settings = ref<TeacherCourseSettingsResponse>()
const form = reactive({ name: '', code: '', term: '', description: '', state_token: '', teachers: [] as number[] })
const fieldErrors = ref<Record<string, unknown>>({})
const error = ref('')
const saved = ref('')
const saving = ref(false)

function hydrate(response: TeacherCourseSettingsResponse) {
  settings.value = response
  Object.assign(form, {
    name: response.form.name,
    code: response.form.code,
    term: response.form.term,
    description: response.form.description,
    state_token: response.form.state_token,
    teachers: [...response.form.teachers],
  })
}

function errorText(value: unknown) {
  return Array.isArray(value) ? value.join(' ') : String(value || '')
}

onMounted(async () => {
  try {
    hydrate(await getTeacherCourseApi().settings(courseId))
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '课程设置加载失败。'
  }
})

async function submit() {
  if (!settings.value?.capabilities.can_update) return
  saving.value = true
  error.value = ''
  saved.value = ''
  fieldErrors.value = {}
  try {
    hydrate(await getTeacherCourseApi().saveSettings(courseId, { ...form, teachers: [...form.teachers] }))
    saved.value = '课程设置已保存。'
  } catch (caught) {
    if (caught instanceof ApiError) fieldErrors.value = caught.fields
    error.value = caught instanceof Error ? caught.message : '课程设置保存失败。'
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="kt-teacher-courses">
    <WorkspaceShell
      :user-name="bootstrap.user.display_name"
      :user-meta="bootstrap.user.username"
      :csrf-token="bootstrap.csrf_token"
      :course-name="settings?.course.name || '课程'"
      :course-meta="settings ? `${settings.course.code} · ${settings.course.term}` : undefined"
      main-id="teacher-course-settings-main"
    >
      <template #navigation>
        <a href="/teacher/courses/">我的课程</a>
        <a :href="`/teacher/courses/${courseId}/`">概览</a>
        <a :href="`/teacher/courses/${courseId}/banks/`">题库</a>
        <a :href="`/teacher/courses/${courseId}/classes/`">班级</a>
        <a :href="`/teacher/courses/${courseId}/experiments/`">实验</a>
        <a :href="`/teacher/courses/${courseId}/data/`">数据</a>
        <RouterLink :to="`/teacher/courses/${courseId}/settings/`" aria-current="page">设置</RouterLink>
      </template>
      <p v-if="!settings && !error" role="status">正在加载课程设置…</p>
      <p v-else-if="error && !settings" class="kt-alert kt-alert--danger" role="alert">{{ error }}</p>
      <template v-else-if="settings">
        <header class="kt-teacher-courses-heading"><div><p>{{ settings.course.code }} · {{ settings.course.term }}</p><h1>课程设置</h1><span>负责人和协作教师可共同维护课程资源、协作成员与归档状态；变更由服务器记录并同步给其他管理者。</span></div></header>
        <div class="kt-teacher-course-settings-grid">
          <form class="kt-card kt-teacher-course-form" data-course-settings @submit.prevent="submit">
            <p v-if="error" class="kt-alert kt-alert--danger" role="alert">{{ errorText(fieldErrors.__all__ || fieldErrors.non_field_errors) || error }}</p>
            <p v-if="saved" class="kt-alert kt-alert--success" role="status">{{ saved }}</p>
            <input v-model="form.state_token" type="hidden" name="state_token">
            <label>课程名称<input v-model="form.name" name="name" maxlength="160" required :disabled="!settings.capabilities.can_update"><small v-if="fieldErrors.name">{{ errorText(fieldErrors.name) }}</small></label>
            <label>课程代码<input v-model="form.code" name="code" maxlength="64" required :disabled="!settings.capabilities.can_update"><small v-if="fieldErrors.code">{{ errorText(fieldErrors.code) }}</small></label>
            <label>学期<input v-model="form.term" name="term" maxlength="64" required :disabled="!settings.capabilities.can_update"><small v-if="fieldErrors.term">{{ errorText(fieldErrors.term) }}</small></label>
            <label>课程说明<textarea v-model="form.description" name="description" rows="5" :disabled="!settings.capabilities.can_update" /><small v-if="fieldErrors.description">{{ errorText(fieldErrors.description) }}</small></label>
            <fieldset :disabled="!settings.capabilities.can_update"><legend>协作教师</legend><label v-for="teacher in settings.form.teacher_options" :key="teacher.id" class="kt-teacher-course-checkbox"><input v-model="form.teachers" type="checkbox" :value="teacher.id">{{ teacher.display_name }} <small>{{ teacher.username }}</small></label><small v-if="fieldErrors.teachers">{{ errorText(fieldErrors.teachers) }}</small></fieldset>
            <div class="kt-teacher-course-form__actions"><button class="kt-button" type="submit" :disabled="saving || !settings.capabilities.can_update">{{ saving ? '正在保存…' : '保存设置' }}</button><a class="kt-button kt-button--secondary" :href="`/teacher/courses/${courseId}/`">返回课程</a></div>
          </form>
          <aside class="kt-card kt-teacher-course-danger"><h2>课程归档</h2><p>归档后课程只读，学生不能开始新的实验会话。</p><form v-if="settings.capabilities.can_archive" method="post" :action="settings.archive_url"><input type="hidden" name="csrfmiddlewaretoken" :value="bootstrap.csrf_token"><button class="kt-button kt-button--danger" type="submit">归档课程</button></form><span v-else class="kt-status kt-status--neutral">{{ settings.course.status_label }}</span></aside>
        </div>
        <section class="kt-card kt-teacher-course-governance"><h2>身份与数据治理</h2><p>课程导出包含学生学号和姓名等直接身份数据。课程协作教师只能在授权范围内生成和下载，后续共享前需自行完成去标识化处理。</p><h3>受控数据清除</h3><p>普通实验页面不提供硬删除。先生成影响预览，执行后仅保留最小审计记录。</p><form method="post" :action="settings.purge_request_create_url"><input type="hidden" name="csrfmiddlewaretoken" :value="bootstrap.csrf_token"><input type="hidden" name="confirmation" value="PURGE"><label>原因<select name="reason_code"><option value="research_withdrawal">学生退出研究</option><option value="collection_error">错误采集纠正</option><option value="retention_expired">保留期限结束</option></select></label><button class="kt-button kt-button--secondary" type="submit">生成清除预览</button></form></section>
        <section class="kt-card kt-teacher-course-purge"><h2>清除请求</h2><div v-if="settings.purge_requests.length" class="kt-table-scroll"><table><thead><tr><th>清除请求</th><th>预览数量</th><th>状态</th><th>操作</th></tr></thead><tbody><tr v-for="request in settings.purge_requests" :key="request.purge_request_id"><td>{{ request.purge_request_id }}</td><td>{{ request.preview_session_count }} 会话 · {{ request.preview_event_count }} 事件</td><td>{{ request.status_label }}</td><td><form v-if="request.execute_url" method="post" :action="request.execute_url"><input type="hidden" name="csrfmiddlewaretoken" :value="bootstrap.csrf_token"><button class="kt-button kt-button--danger" type="submit">执行清除</button></form></td></tr></tbody></table></div><p v-else>尚无清除请求。</p></section>
      </template>
    </WorkspaceShell>
  </div>
</template>
