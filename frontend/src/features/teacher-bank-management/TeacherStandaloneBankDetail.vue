<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import Alert from '@/design-system/components/Alert.vue'
import EmptyState from '@/design-system/components/EmptyState.vue'
import PageHeader from '@/design-system/components/PageHeader.vue'
import StatusBadge from '@/design-system/components/StatusBadge.vue'

import {
  getTeacherStandaloneQuestionBankApi,
  isStandaloneBankRedirect,
  type StandaloneBank,
  type StandaloneQuestion,
} from './standalone-api'

const route = useRoute()
const router = useRouter()
const api = getTeacherStandaloneQuestionBankApi()
const bankId = Number(route.params.bankId)
const data = ref<{
  bank: StandaloneBank
  can_edit_metadata: boolean
  can_edit_content: boolean
  questions: StandaloneQuestion[]
}>()
const error = ref('')
const success = ref('')
const updatingVisibility = ref(false)
const rechecking = ref(false)
const canEditContent = computed(() => Boolean(data.value?.can_edit_content))

function statusTone(status: string) {
  if (status === 'active') return 'success' as const
  if (status === 'draft') return 'warning' as const
  return 'neutral' as const
}

async function load() {
  error.value = ''
  try {
    const response = await api.detail(bankId)
    if (isStandaloneBankRedirect(response)) {
      await router.replace(response.redirect_url)
      return
    }
    data.value = response
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '独立题库详情加载失败。'
  }
}

async function updateVisibility(event: Event) {
  const visibility = (event.currentTarget as HTMLSelectElement).value
  if (!data.value || visibility === data.value.bank.visibility) return
  updatingVisibility.value = true
  error.value = ''
  try {
    data.value.bank = await api.updateVisibility(bankId, visibility)
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '题库可见性更新失败。'
  } finally {
    updatingVisibility.value = false
  }
}

async function recheckDrafts() {
  rechecking.value = true
  error.value = ''
  success.value = ''
  try {
    const result = await api.recheckQuestions(bankId)
    await load()
    success.value = `复核完成：检查 ${result.checked} 道，转为可用 ${result.activated} 道，仍为草稿 ${result.draft} 道。`
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '复核草稿失败。'
  } finally {
    rechecking.value = false
  }
}

onMounted(load)
</script>

<template>
  <Alert v-if="error && !data" tone="danger">{{ error }}</Alert>
  <template v-else-if="data">
    <PageHeader :title="data.bank.name">
      <template #kicker>独立题库</template>
      <p>{{ data.bank.description || '未填写题库说明。' }}</p>
      <template #actions>
        <RouterLink v-if="canEditContent" class="kt-button" :to="data.bank.question_import_url">导入题目文件</RouterLink>
        <RouterLink v-if="canEditContent" class="kt-button kt-button--secondary" :to="data.bank.kc_import_url">导入 KC 对应表</RouterLink>
        <button v-if="canEditContent" data-recheck-drafts class="kt-button kt-button--secondary" type="button" :disabled="rechecking" @click="recheckDrafts">{{ rechecking ? '正在复核…' : '复核草稿' }}</button>
        <RouterLink class="kt-button kt-button--secondary" to="/teacher/banks/">返回题库</RouterLink>
      </template>
    </PageHeader>
    <Alert v-if="error" tone="danger">{{ error }}</Alert>
    <Alert v-if="success" tone="success">{{ success }}</Alert>
    <Alert v-if="data.bank.visibility === 'archived'" tone="warning">题库已归档，题目与导入操作保持只读。</Alert>
    <div class="kt-bank-meta-line">
      <StatusBadge :label="data.bank.visibility_label" :tone="data.bank.visibility === 'shared' ? 'success' : 'neutral'" />
      <span>{{ data.bank.question_count }} 道题</span>
      <label v-if="data.can_edit_metadata">可见性
        <select :value="data.bank.visibility" :disabled="updatingVisibility" @change="updateVisibility">
          <option value="private">私有</option>
          <option value="shared">共享</option>
          <option value="archived">归档</option>
        </select>
      </label>
    </div>

    <section class="kt-section" aria-labelledby="standalone-question-list-title">
      <div class="kt-section-heading">
        <h2 id="standalone-question-list-title">题目清单</h2>
        <span>{{ data.questions.length }} 题</span>
      </div>
      <EmptyState v-if="!data.questions.length" title="题库还没有题目">
        <p>通过题目文件导入后，题目会显示在这里。</p>
      </EmptyState>
      <div v-else class="kt-card kt-table-card">
        <div class="kt-table-scroll">
          <table class="kt-table">
            <thead>
              <tr><th>题库内序号</th><th>题目</th><th>题型</th><th>知识点</th><th>状态</th><th>操作</th></tr>
            </thead>
            <tbody>
              <tr v-for="question in data.questions" :key="question.id">
                <td class="kt-mono">{{ question.sequence }}</td>
                <td><strong>{{ question.text }}</strong><ul v-if="question.draft_reasons.length" class="kt-draft-reasons"><li v-for="reason in question.draft_reasons" :key="reason">{{ reason }}</li></ul></td>
                <td>{{ question.question_type_label }}</td>
                <td>{{ question.kc_mapping.nodes.map((node) => node.name).join('、') || '未映射' }}</td>
                <td><StatusBadge :label="question.status_label" :tone="statusTone(question.status)" /></td>
                <td><RouterLink v-if="canEditContent" :to="question.edit_url">编辑</RouterLink><span v-else>只读</span></td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </section>
  </template>
  <p v-else role="status">正在加载独立题库详情…</p>
</template>
