<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import Alert from '@/design-system/components/Alert.vue'
import EmptyState from '@/design-system/components/EmptyState.vue'
import MobileCardList from '@/design-system/components/MobileCardList.vue'
import PageHeader from '@/design-system/components/PageHeader.vue'
import Paginator from '@/design-system/components/Paginator.vue'
import StatusBadge from '@/design-system/components/StatusBadge.vue'
import DeletionDialog from '@/features/teacher-deletion/DeletionDialog.vue'
import type { DeletionTarget } from '@/features/teacher-deletion/api'

import { getTeacherClassApi, type TeacherClassCollection } from './api'

const route = useRoute()
const router = useRouter()
const api = getTeacherClassApi()
const courseId = Number(route.params.courseId)
const collection = ref<TeacherClassCollection>()
const loading = ref(true)
const error = ref('')
const search = ref(String(route.query.q || ''))
const currentPage = computed(() => Number(route.query.page || 1))
const canCreate = computed(() => collection.value?.capabilities.can_create === true)
const createForm = ref({ name: '', academic_year: '' })
const creating = ref(false)
const deletionTarget = ref<DeletionTarget>()
const canManage = computed(() => collection.value?.capabilities.can_manage === true)

async function load(page = currentPage.value, query = search.value) {
  loading.value = true
  error.value = ''
  try {
    collection.value = await api.list(courseId, { page, query })
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '班级列表加载失败。'
  } finally {
    loading.value = false
  }
}

async function applySearch() {
  const query = search.value.trim()
  await router.push({ query: query ? { q: query } : {} })
  await load(1, query)
}

async function navigate(page: number) {
  const query = search.value.trim()
  await router.push({ query: { ...(query ? { q: query } : {}), ...(page > 1 ? { page: String(page) } : {}) } })
  await load(page, query)
}

async function createClass() {
  if (!canCreate.value || creating.value) return
  creating.value = true
  error.value = ''
  try {
    const created = await api.create(courseId, createForm.value)
    await router.push(created.class_group.detail_url)
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '班级创建失败。'
  } finally {
    creating.value = false
  }
}

async function afterDeletion() {
  deletionTarget.value = undefined
  const page = currentPage.value
  await load(page)
  if (!collection.value?.items.length && page > 1) {
    await navigate(Math.max(1, collection.value?.page.total_pages || page - 1))
  }
}

onMounted(() => load())
</script>

<template>
  <PageHeader title="班级">
    <template #kicker>教学组织</template>
    <p>查看课程班级、管理成员并进入教学分析。</p>
  </PageHeader>

  <Alert v-if="error" tone="danger">{{ error }}</Alert>
  <p v-if="loading" role="status">正在加载班级…</p>
  <div v-else class="kt-class-list-layout">
    <section aria-labelledby="class-list-title">
      <form class="kt-card kt-class-search" role="search" @submit.prevent="applySearch">
        <label for="class-search">班级名称或年级</label>
        <div class="kt-inline-controls">
          <input id="class-search" v-model="search" type="search" placeholder="输入班级名称或年级">
          <button class="kt-button kt-button--secondary" type="submit">搜索班级</button>
        </div>
      </form>

      <EmptyState v-if="!collection?.items.length" title="没有匹配的班级">
        <p>调整搜索条件，或在右侧创建课程班级。</p>
      </EmptyState>
      <section v-else class="kt-card kt-table-card" aria-labelledby="class-list-title">
        <div class="kt-section-heading">
          <h2 id="class-list-title">课程班级</h2>
          <span>{{ collection.page.total_items }} 个</span>
        </div>
        <div class="kt-table-scroll kt-desktop-only">
          <table class="kt-table">
            <thead><tr><th>年级</th><th>班级</th><th>学生</th><th>状态</th><th><span class="kt-visually-hidden">操作</span></th></tr></thead>
            <tbody>
              <tr v-for="classGroup in collection.items" :key="classGroup.id">
                <td>{{ classGroup.academic_year || '—' }}</td>
                <td><RouterLink :to="classGroup.detail_url"><strong>{{ classGroup.name }}</strong></RouterLink></td>
                <td>{{ classGroup.active_student_count }} 人</td>
                <td><StatusBadge :tone="classGroup.is_active ? 'success' : 'neutral'" :label="classGroup.is_active ? '可用' : '已停用'" /></td>
                <td><div class="kt-class-row-actions"><RouterLink :to="classGroup.detail_url">进入班级中心</RouterLink><button v-if="canManage" class="kt-text-button kt-text-button--danger" type="button" :data-delete-class="classGroup.id" @click="deletionTarget = { kind: 'class', classId: classGroup.id }">删除</button></div></td>
              </tr>
            </tbody>
          </table>
        </div>
        <MobileCardList class="kt-mobile-only">
          <li v-for="classGroup in collection.items" :key="classGroup.id">
            <div class="kt-section-heading">
              <RouterLink :to="classGroup.detail_url"><strong>{{ classGroup.name }}</strong></RouterLink>
              <StatusBadge :tone="classGroup.is_active ? 'success' : 'neutral'" :label="classGroup.is_active ? '可用' : '已停用'" />
            </div>
            <p>{{ classGroup.academic_year || '未填写年级' }} · {{ classGroup.active_student_count }} 人</p>
            <button v-if="canManage" class="kt-text-button kt-text-button--danger" type="button" :data-delete-class="classGroup.id" @click="deletionTarget = { kind: 'class', classId: classGroup.id }">删除班级</button>
          </li>
        </MobileCardList>
        <Paginator :page="collection.page.number" :total-pages="collection.page.total_pages" @navigate="navigate" />
      </section>
    </section>

    <aside v-if="canCreate" class="kt-card kt-class-create" aria-labelledby="create-class-title">
      <h2 id="create-class-title">创建班级</h2>
      <form class="kt-class-create-form" @submit.prevent="createClass">
        <label for="new-class-name">班级</label>
        <input id="new-class-name" v-model="createForm.name" name="name" maxlength="120" required>
        <label for="new-class-year">年级</label>
        <input id="new-class-year" v-model="createForm.academic_year" name="academic_year" maxlength="32" inputmode="numeric" placeholder="如：2026">
        <button class="kt-button" type="submit" :disabled="creating">{{ creating ? '创建中…' : '创建班级' }}</button>
      </form>
    </aside>
  </div>
  <DeletionDialog v-if="deletionTarget" :course-id="courseId" :target="deletionTarget" @cancelled="deletionTarget = undefined" @completed="afterDeletion" @batch-completed="afterDeletion" />
</template>
