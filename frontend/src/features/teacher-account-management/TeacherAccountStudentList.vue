<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import Alert from '@/design-system/components/Alert.vue'
import EmptyState from '@/design-system/components/EmptyState.vue'
import PageHeader from '@/design-system/components/PageHeader.vue'

import { getTeacherAccountManagementApi, type AccountStudentCollection } from './api'
import './teacher-account-management.css'

const route = useRoute()
const router = useRouter()
const api = getTeacherAccountManagementApi()
const collection = ref<AccountStudentCollection>()
const loading = ref(true)
const submitting = ref(false)
const error = ref('')
const success = ref('')
const query = ref(String(route.query.q || ''))
const selectedClass = ref(String(route.query.class_id || ''))
const form = reactive({ student_number: '', display_name: '', class_group: '', password: '' })
const resetStudent = ref<{ id: number; display_name: string }>()
const replacementPassword = ref('')

function message(caught: unknown, fallback: string) {
  return caught instanceof Error ? caught.message : fallback
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    collection.value = await api.students({
      query: query.value.trim() || undefined,
      classId: Number(selectedClass.value) || undefined,
    })
  } catch (caught) {
    error.value = message(caught, '学生账号列表加载失败。')
  } finally {
    loading.value = false
  }
}

async function applyFilters() {
  const nextQuery = query.value.trim()
  const nextClassId = Number(selectedClass.value) || undefined
  await router.push({
    query: {
      ...(nextQuery ? { q: nextQuery } : {}),
      ...(nextClassId ? { class_id: String(nextClassId) } : {}),
    },
  })
  await load()
}

async function createStudent() {
  const classGroupId = Number(form.class_group)
  if (!classGroupId) {
    error.value = '请选择学生所属班级。'
    return
  }
  submitting.value = true
  error.value = ''
  success.value = ''
  try {
    await api.createStudent({
      student_number: form.student_number,
      display_name: form.display_name,
      class_group: classGroupId,
      password: form.password,
    })
    form.student_number = ''
    form.display_name = ''
    form.class_group = ''
    form.password = ''
    await load()
    success.value = '学生账号已创建。'
  } catch (caught) {
    error.value = message(caught, '学生账号未创建。')
  } finally {
    submitting.value = false
  }
}

async function resetPassword() {
  if (!resetStudent.value) return
  submitting.value = true
  error.value = ''
  success.value = ''
  try {
    await api.resetLegacyStudentPassword(resetStudent.value.id, replacementPassword.value)
    success.value = `${resetStudent.value.display_name} 的密码已重置。`
    resetStudent.value = undefined
    replacementPassword.value = ''
  } catch (caught) {
    error.value = message(caught, '密码重置失败。')
  } finally {
    submitting.value = false
  }
}

onMounted(load)
</script>

<template>
  <section class="kt-teacher-account-management">
    <PageHeader title="学生账号">
      <template #kicker>账户管理</template>
      <p>按教师授权范围查看、创建和重置学生的全局登录账号。</p>
      <template #actions><a class="kt-button kt-button--secondary" href="/teacher/accounts/classes/">班级管理</a><a class="kt-button" href="/teacher/accounts/students/import/">批量导入学生</a></template>
    </PageHeader>

    <Alert v-if="error" tone="danger">{{ error }}</Alert>
    <Alert v-if="success" tone="success">{{ success }}</Alert>
    <p v-if="loading" role="status">正在加载学生账号…</p>

    <template v-else-if="collection">
      <div class="kt-account-management-grid">
        <section>
          <form class="kt-card kt-account-management-form" role="search" @submit.prevent="applyFilters">
            <h2>筛选学生</h2>
            <label>姓名或学号<input v-model="query" type="search" placeholder="输入姓名或学号"></label>
            <label>所属班级<select v-model="selectedClass"><option value="">全部可访问班级</option><option v-for="classGroup in collection.class_options" :key="classGroup.id" :value="classGroup.id">{{ classGroup.display_name }}</option></select></label>
            <div class="kt-account-management-actions"><button class="kt-button kt-button--secondary" type="submit">应用筛选</button><button class="kt-button kt-button--secondary" type="button" @click="query = ''; selectedClass = ''; applyFilters()">清除筛选</button></div>
          </form>

          <section class="kt-card kt-account-management-members" aria-labelledby="account-student-list-title">
            <div class="kt-section-heading"><h2 id="account-student-list-title">学生账号</h2><span>{{ collection.items.length }} 人</span></div>
            <EmptyState v-if="!collection.items.length" title="没有符合条件的学生"><p>调整筛选条件，或在右侧创建学生账号。</p></EmptyState>
            <div v-else class="kt-table-scroll"><table class="kt-table"><thead><tr><th>学号</th><th>姓名</th><th>所属班级</th><th>密码操作</th></tr></thead><tbody><tr v-for="student in collection.items" :key="student.id"><td>{{ student.student_number }}</td><td>{{ student.display_name }}</td><td>{{ student.class_group?.display_name || '未归属班级' }}</td><td><button class="kt-button kt-button--secondary" type="button" @click="resetStudent = student">重置密码</button></td></tr></tbody></table></div>
          </section>
        </section>

        <aside class="kt-card kt-account-management-form">
          <h2>创建学生账号</h2>
          <p>此处创建的是全局账号；初始密码须由教师明确输入。</p>
          <form data-account-student-create @submit.prevent="createStudent">
            <label>学号<input v-model="form.student_number" name="student_number" maxlength="64" required :disabled="submitting"></label>
            <label>姓名<input v-model="form.display_name" name="display_name" maxlength="80" required :disabled="submitting"></label>
            <label>所属班级<select v-model="form.class_group" name="class_group" required :disabled="submitting"><option value="">请选择班级</option><option v-for="classGroup in collection.class_options" :key="classGroup.id" :value="classGroup.id">{{ classGroup.display_name }}</option></select></label>
            <label>初始密码<input v-model="form.password" name="password" minlength="8" required type="password" autocomplete="new-password" :disabled="submitting"></label>
            <button class="kt-button" type="submit" :disabled="submitting">创建学生账号</button>
          </form>
        </aside>
      </div>

      <section v-if="resetStudent" class="kt-card kt-account-management-confirm" aria-labelledby="legacy-reset-password-title">
        <h2 id="legacy-reset-password-title">重置 {{ resetStudent.display_name }} 的密码</h2>
        <form @submit.prevent="resetPassword"><label>新密码<input v-model="replacementPassword" minlength="8" required type="password" autocomplete="new-password"></label><div class="kt-account-management-actions"><button class="kt-button" type="submit" :disabled="submitting">确认重置</button><button class="kt-button kt-button--secondary" type="button" :disabled="submitting" @click="resetStudent = undefined">取消</button></div></form>
      </section>
    </template>
  </section>
</template>
