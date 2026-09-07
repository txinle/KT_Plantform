<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import Alert from '@/design-system/components/Alert.vue'
import EmptyState from '@/design-system/components/EmptyState.vue'
import PageHeader from '@/design-system/components/PageHeader.vue'
import Paginator from '@/design-system/components/Paginator.vue'

import { getTeacherBankManagementApi, type ArchivedBankCollection } from './api'

const route = useRoute()
const router = useRouter()
const api = getTeacherBankManagementApi()
const courseId = Number(route.params.courseId)
const collection = ref<ArchivedBankCollection>()
const loading = ref(true)
const error = ref('')
const deletingId = ref<number>()

async function load(page = Number(route.query.page || 1)) {
  loading.value = true
  error.value = ''
  try {
    collection.value = await api.archivedBanks(courseId, page)
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '归档题库加载失败。'
  } finally {
    loading.value = false
  }
}

async function restore(bankId: number) {
  error.value = ''
  try {
    await api.restoreBank(courseId, bankId)
    await router.push(`/teacher/courses/${courseId}/banks/`)
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '恢复题库失败。'
  }
}

async function remove(bankId: number) {
  error.value = ''
  try {
    await api.deleteBank(courseId, bankId)
    deletingId.value = undefined
    await load()
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '永久删除题库失败。'
  }
}

async function navigate(page: number) {
  const query = page === 1 ? {} : { page: String(page) }
  await router.push({ query })
  await load(page)
}

onMounted(load)
</script>

<template>
  <p v-if="loading" role="status">正在加载归档题库…</p>
  <Alert v-else-if="error && !collection" tone="danger">{{ error }}</Alert>
  <template v-else-if="collection">
    <PageHeader title="归档题库">
      <template #kicker>{{ collection.course.code }} · {{ collection.course.term }}</template>
      <p>归档题库保持历史内容只读；恢复后将以私有题库回到课程题库列表。</p>
      <template #actions><RouterLink class="kt-button kt-button--secondary" :to="`/teacher/courses/${courseId}/banks/`">返回题库</RouterLink></template>
    </PageHeader>
    <Alert v-if="error" tone="danger">{{ error }}</Alert>
    <EmptyState v-if="!collection.items.length" title="没有归档题库"><p>归档后的题库会显示在这里。</p></EmptyState>
    <section v-else class="kt-card kt-table-card" aria-label="归档题库列表">
      <div class="kt-table-scroll">
        <table class="kt-table">
          <thead><tr><th>题库</th><th>题目数</th><th>创建者</th><th>最近更新</th><th>操作</th></tr></thead><tbody>
            <tr v-for="bank in collection.items" :key="bank.id"><td><RouterLink :to="bank.detail_url"><strong>{{ bank.name }}</strong></RouterLink><small>{{ bank.description || '未填写说明' }}</small></td><td>{{ bank.question_count }}</td><td>{{ bank.created_by_name }}</td><td>{{ new Date(bank.updated_at).toLocaleString('zh-CN') }}</td><td><div v-if="bank.can_edit_metadata" class="kt-inline-actions"><button class="kt-button kt-button--secondary" type="button" @click="restore(bank.id)">恢复为私有</button><button class="kt-text-button kt-text-button--danger" type="button" @click="deletingId = bank.id">永久删除</button></div></td></tr>
          </tbody>
        </table>
      </div>
      <Paginator :page="collection.page.number" :total-pages="collection.page.total_pages" @navigate="navigate" />
    </section>
    <section v-if="deletingId" class="kt-card kt-inline-panel kt-inline-panel--danger" role="dialog" aria-modal="true" aria-label="确认永久删除题库"><p>永久删除会移除题库及未被研究数据引用的题目；已被实验或快照引用时服务器会拒绝操作。</p><div class="kt-inline-actions"><button class="kt-button kt-button--danger" type="button" @click="remove(deletingId)">确认删除</button><button class="kt-button kt-button--secondary" type="button" @click="deletingId = undefined">取消</button></div></section>
  </template>
</template>
