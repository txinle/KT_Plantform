<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import Alert from '@/design-system/components/Alert.vue'
import EmptyState from '@/design-system/components/EmptyState.vue'
import MobileCardList from '@/design-system/components/MobileCardList.vue'
import PageHeader from '@/design-system/components/PageHeader.vue'
import Paginator from '@/design-system/components/Paginator.vue'
import StatusBadge from '@/design-system/components/StatusBadge.vue'

import { getTeacherExperimentApi, type TeacherExperimentCollection } from './api'

const route = useRoute()
const router = useRouter()
const api = getTeacherExperimentApi()
const courseId = Number(route.params.courseId)
const collection = ref<TeacherExperimentCollection>()
const loading = ref(true)
const error = ref('')
const currentPage = computed(() => Number(route.query.page || 1))

function statusTone(status: string) {
  if (status === 'draft') return 'warning' as const
  if (status === 'archived') return 'neutral' as const
  return 'success' as const
}

async function load(page = currentPage.value) {
  loading.value = true
  error.value = ''
  try {
    collection.value = await api.list(courseId, page)
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '实验列表加载失败。'
  } finally {
    loading.value = false
  }
}

async function navigate(page: number) {
  await router.push({ query: page === 1 ? {} : { page: String(page) } })
  await load(page)
}

onMounted(() => load())
</script>

<template>
  <PageHeader title="实验">
    <template #kicker>教学实验</template>
    <p>管理实验定义、班级排期和发布后的任务进度。</p>
    <template #actions>
      <RouterLink class="kt-button" :to="`/teacher/courses/${courseId}/experiments/create/`">
        创建实验
      </RouterLink>
    </template>
  </PageHeader>

  <Alert v-if="error" tone="danger">{{ error }}</Alert>
  <p v-if="loading" role="status">正在加载实验…</p>
  <EmptyState v-else-if="!collection?.items.length" title="尚未创建实验">
    <p>创建实验后，再选择班级和排期进行发布。</p>
  </EmptyState>
  <section v-else class="kt-card kt-table-card" aria-labelledby="experiment-list-title">
    <div class="kt-section-heading">
      <h2 id="experiment-list-title">全部实验</h2>
      <span>{{ collection.page.total_items }} 个</span>
    </div>
    <div class="kt-table-scroll kt-desktop-only">
      <table class="kt-table">
        <thead><tr><th>实验</th><th>题库</th><th>选题</th><th>状态</th><th>更新时间</th></tr></thead>
        <tbody>
          <tr v-for="experiment in collection.items" :key="experiment.id">
            <td>
              <RouterLink :to="`/teacher/courses/${courseId}/experiments/${experiment.id}/`">
                <strong>{{ experiment.name }}</strong>
              </RouterLink>
              <span class="kt-cell-note">{{ experiment.description || '无说明' }}</span>
            </td>
            <td>{{ experiment.question_bank.name }}</td>
            <td>{{ experiment.sampling_mode_label || experiment.selection_mode_label }}</td>
            <td><StatusBadge :tone="statusTone(experiment.status)" :label="experiment.status_label" /></td>
            <td>{{ new Date(experiment.updated_at).toLocaleString('zh-CN') }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <MobileCardList class="kt-mobile-only">
      <li v-for="experiment in collection.items" :key="experiment.id" class="kt-mobile-experiment-card">
        <div class="kt-section-heading">
          <RouterLink :to="`/teacher/courses/${courseId}/experiments/${experiment.id}/`">
            <strong>{{ experiment.name }}</strong>
          </RouterLink>
          <StatusBadge :tone="statusTone(experiment.status)" :label="experiment.status_label" />
        </div>
        <p>{{ experiment.description || '无说明' }}</p>
        <dl>
          <div><dt>题库</dt><dd>{{ experiment.question_bank.name }}</dd></div>
          <div><dt>选题</dt><dd>{{ experiment.sampling_mode_label || experiment.selection_mode_label }}</dd></div>
        </dl>
      </li>
    </MobileCardList>
    <Paginator
      :page="collection.page.number"
      :total-pages="collection.page.total_pages"
      @navigate="navigate"
    />
  </section>
</template>
