<script setup lang="ts">
import { reactive, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'

import { ApiError } from '@/api/client'
import { getBootstrapContext } from '@/app/bootstrap'
import WorkspaceShell from '@/design-system/components/WorkspaceShell.vue'

import { getTeacherCourseApi } from './api'
import './teacher-courses.css'

const bootstrap = getBootstrapContext()
const router = useRouter()
const form = reactive({ name: '', code: '', term: '', description: '' })
const fieldErrors = ref<Record<string, unknown>>({})
const error = ref('')
const saving = ref(false)

function errorText(value: unknown) {
  return Array.isArray(value) ? value.join(' ') : String(value || '')
}

async function submit() {
  saving.value = true
  error.value = ''
  fieldErrors.value = {}
  try {
    const response = await getTeacherCourseApi().create({ ...form })
    await router.push(response.course.detail_url)
  } catch (caught) {
    if (caught instanceof ApiError) fieldErrors.value = caught.fields
    error.value = caught instanceof Error ? caught.message : '创建课程失败。'
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
      main-id="teacher-course-create-main"
    >
      <template #navigation><RouterLink to="/teacher/courses/">我的课程</RouterLink></template>
      <section class="kt-teacher-courses-form-page">
        <header class="kt-teacher-courses-heading"><div><p>课程工作区</p><h1>新建课程</h1><span>名称、课程代码和学期共同标识一次开课。</span></div></header>
        <form class="kt-card kt-teacher-course-form" data-course-create @submit.prevent="submit">
          <p v-if="error" class="kt-alert kt-alert--danger" role="alert">{{ errorText(fieldErrors.__all__ || fieldErrors.non_field_errors) || error }}</p>
          <label>课程名称<input v-model="form.name" name="name" maxlength="160" required><small v-if="fieldErrors.name">{{ errorText(fieldErrors.name) }}</small></label>
          <label>课程代码<input v-model="form.code" name="code" maxlength="64" required><small v-if="fieldErrors.code">{{ errorText(fieldErrors.code) }}</small></label>
          <label>学期<input v-model="form.term" name="term" maxlength="64" required><small v-if="fieldErrors.term">{{ errorText(fieldErrors.term) }}</small></label>
          <label>课程说明<textarea v-model="form.description" name="description" rows="5" /><small v-if="fieldErrors.description">{{ errorText(fieldErrors.description) }}</small></label>
          <div class="kt-teacher-course-form__actions"><button class="kt-button" type="submit" :disabled="saving">{{ saving ? '正在创建…' : '创建课程' }}</button><RouterLink class="kt-button kt-button--secondary" to="/teacher/courses/">取消</RouterLink></div>
        </form>
      </section>
    </WorkspaceShell>
  </div>
</template>
