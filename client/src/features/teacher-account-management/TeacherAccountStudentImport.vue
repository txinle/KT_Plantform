<script setup lang="ts">
import { ref } from 'vue'

import Alert from '@/design-system/components/Alert.vue'
import PageHeader from '@/design-system/components/PageHeader.vue'

import { getTeacherAccountManagementApi, type StudentImportPreview } from './api'
import './teacher-account-management.css'

const api = getTeacherAccountManagementApi()
const file = ref<File>()
const preview = ref<StudentImportPreview>()
const error = ref('')
const success = ref('')
const submitting = ref(false)

function message(caught: unknown, fallback: string) {
  return caught instanceof Error ? caught.message : fallback
}

function chooseFile(event: Event) {
  const input = event.target as HTMLInputElement
  file.value = input.files?.[0]
  preview.value = undefined
}

async function previewImport() {
  if (!file.value) {
    error.value = '请选择 Excel 文件。'
    return
  }
  submitting.value = true
  error.value = ''
  success.value = ''
  try {
    const body = new FormData()
    body.append('file', file.value)
    preview.value = await api.previewLegacyImport(body)
  } catch (caught) {
    error.value = message(caught, '名单校验失败。')
  } finally {
    submitting.value = false
  }
}

async function confirmImport() {
  submitting.value = true
  error.value = ''
  success.value = ''
  try {
    const result = await api.confirmLegacyImport()
    preview.value = undefined
    file.value = undefined
    success.value = `已导入 ${result.imported} 名学生。`
  } catch (caught) {
    error.value = message(caught, '学生导入失败。')
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <section class="kt-teacher-account-management">
    <PageHeader title="批量导入学生">
      <template #kicker>账户管理</template>
      <p>上传名单后先在服务器生成预览；确认后才一次性创建全局学生账号。</p>
      <template #actions><a class="kt-button kt-button--secondary" href="/teacher/accounts/students/">返回学生账号</a></template>
    </PageHeader>

    <Alert v-if="error" tone="danger">{{ error }}</Alert>
    <Alert v-if="success" tone="success">{{ success }}</Alert>

    <section class="kt-card kt-account-management-form" aria-labelledby="legacy-import-title">
      <h2 id="legacy-import-title">学生名单导入</h2>
      <p>模板列必须为：学号、姓名、班级。班级需属于当前教师授权范围，停用班级不能作为导入目标。</p>
      <a class="kt-button kt-button--secondary" href="/api/v1/teacher/accounts/students/import-template.xlsx">下载 Excel 参考模板</a>
      <form v-if="!preview" data-legacy-import-upload @submit.prevent="previewImport">
        <label>Excel 学生名单<input accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" required type="file" :disabled="submitting" @change="chooseFile"></label>
        <div class="kt-account-management-actions"><button class="kt-button" type="submit" :disabled="submitting">校验并生成预览</button><a class="kt-button kt-button--secondary" href="/teacher/accounts/students/">取消</a></div>
      </form>
      <template v-else>
        <p class="kt-account-management-notice">名单校验通过，尚未写入数据库。</p>
        <div class="kt-table-scroll"><table class="kt-table"><thead><tr><th>学号</th><th>姓名</th><th>班级</th><th>账号处理</th></tr></thead><tbody><tr v-for="row in preview.preview" :key="row.student_number"><td>{{ row.student_number }}</td><td>{{ row.display_name }}</td><td>{{ row.class_name }}</td><td>{{ row.account_action === 'create' ? '新建账号（S + 学号）' : '复用已有账号' }}</td></tr></tbody></table></div>
        <form data-legacy-import-confirm @submit.prevent="confirmImport"><p>确认后将一次性创建 {{ preview.preview.length }} 名学生账号。</p><div class="kt-account-management-actions"><button class="kt-button" type="submit" :disabled="submitting">确认导入 {{ preview.preview.length }} 名学生</button><button class="kt-button kt-button--secondary" type="button" :disabled="submitting" @click="preview = undefined">重新上传</button></div></form>
      </template>
    </section>
  </section>
</template>
