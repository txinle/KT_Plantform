<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'

import Alert from '@/design-system/components/Alert.vue'
import EmptyState from '@/design-system/components/EmptyState.vue'
import PageHeader from '@/design-system/components/PageHeader.vue'
import StatusBadge from '@/design-system/components/StatusBadge.vue'

import { getTeacherAccountManagementApi, type AccountClass } from './api'
import './teacher-account-management.css'

const api = getTeacherAccountManagementApi()
const classes = ref<AccountClass[]>([])
const loading = ref(true)
const submitting = ref(false)
const error = ref('')
const success = ref('')
const form = reactive({ name: '', academic_year: '' })

function message(caught: unknown, fallback: string) {
  return caught instanceof Error ? caught.message : fallback
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    classes.value = (await api.classes()).items
  } catch (caught) {
    error.value = message(caught, '班级列表加载失败。')
  } finally {
    loading.value = false
  }
}

async function createClass() {
  submitting.value = true
  error.value = ''
  success.value = ''
  try {
    await api.createClass({ ...form })
    form.name = ''
    form.academic_year = ''
    await load()
    success.value = '班级已创建。'
  } catch (caught) {
    error.value = message(caught, '班级未创建。')
  } finally {
    submitting.value = false
  }
}

onMounted(load)
</script>

<template>
  <section class="kt-teacher-account-management">
    <PageHeader title="班级管理">
      <template #kicker>账户管理</template>
      <p>管理未迁移到课程结构的账户班级；课程班级请从对应课程进入班级中心。</p>
      <template #actions><a class="kt-button kt-button--secondary" href="/teacher/accounts/students/">学生账号</a></template>
    </PageHeader>

    <Alert v-if="error" tone="danger">{{ error }}</Alert>
    <Alert v-if="success" tone="success">{{ success }}</Alert>
    <p v-if="loading" role="status">正在加载班级…</p>

    <div v-else class="kt-account-management-grid">
      <section class="kt-card kt-account-management-members" aria-labelledby="account-class-list-title">
        <div class="kt-section-heading"><h2 id="account-class-list-title">可访问班级</h2><span>{{ classes.length }} 个</span></div>
        <EmptyState v-if="!classes.length" title="尚未创建账户班级"><p>请在右侧创建班级后，再创建或导入学生账号。</p></EmptyState>
        <div v-else class="kt-table-scroll"><table class="kt-table"><thead><tr><th>年级</th><th>班级</th><th>学生</th><th>课程归属</th><th>状态</th></tr></thead><tbody><tr v-for="classGroup in classes" :key="classGroup.id"><td>{{ classGroup.academic_year || '未填写' }}</td><td>{{ classGroup.name }}</td><td>{{ classGroup.student_count }} 人</td><td>{{ classGroup.course_id ? '课程班级' : '账户班级' }}</td><td><StatusBadge :tone="classGroup.is_active ? 'success' : 'neutral'" :label="classGroup.is_active ? '可用' : '已停用'" /></td></tr></tbody></table></div>
      </section>

      <aside class="kt-card kt-account-management-form">
        <h2>创建账户班级</h2>
        <p>创建后可作为学生全局账号的所属班级使用。</p>
        <form data-account-class-create @submit.prevent="createClass">
          <label>班级名称<input v-model="form.name" name="name" maxlength="120" required :disabled="submitting"></label>
          <label>年级<input v-model="form.academic_year" name="academic_year" inputmode="numeric" maxlength="32" placeholder="如：2026" :disabled="submitting"></label>
          <button class="kt-button" type="submit" :disabled="submitting">创建班级</button>
        </form>
      </aside>
    </div>
  </section>
</template>
