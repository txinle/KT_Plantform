<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import Alert from '@/design-system/components/Alert.vue'
import EmptyState from '@/design-system/components/EmptyState.vue'
import PageHeader from '@/design-system/components/PageHeader.vue'
import StatusBadge from '@/design-system/components/StatusBadge.vue'
import QuestionBankInventory from './QuestionBankInventory.vue'
import type { BankUnitSummary } from './api'
import QuestionGroupEditor from './QuestionGroupEditor.vue'
import type { GroupEditorDocument } from './group-api'

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
  unit_summary?: BankUnitSummary
  groups?: GroupEditorDocument[]
}>()
const error = ref('')
const success = ref('')
const updatingVisibility = ref(false)
const rechecking = ref(false)
const editingGroup = ref(false)
const selectedGroupId = ref<number>()
const questionFilter = ref({ query: '', type: '' })
const shownQuestions = computed(() => (data.value?.questions || []).filter(question => !question.group_id && (!questionFilter.value.type || (question.educational_type && question.educational_type !== 'standard' ? question.educational_type : question.question_type) === questionFilter.value.type) && `${question.title || ''} ${question.text} ${question.instructions || ''}`.toLocaleLowerCase().includes(questionFilter.value.query.toLocaleLowerCase())))
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
    if (route.query?.edit_group && response.groups?.some(group => group.id === Number(route.query.edit_group))) {
      selectedGroupId.value = Number(route.query.edit_group)
      editingGroup.value = true
    }
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
    <QuestionBankInventory :summary="data.unit_summary" :groups="data.groups" :can-edit="canEditContent" @filter="questionFilter = $event" @edit="id => { selectedGroupId = id; editingGroup = true }" />
    <QuestionGroupEditor v-if="editingGroup" :key="selectedGroupId || 'new'" :bank-id="bankId" :group-id="selectedGroupId" @saved="editingGroup = false; load()" @close="editingGroup = false" />
    <Alert v-if="error" tone="danger">{{ error }}</Alert>
    <Alert v-if="success" tone="success">{{ success }}</Alert>
    <Alert v-if="data.bank.visibility === 'archived'" tone="warning">题库已归档，题目与导入操作保持只读。</Alert>
    <div class="kt-bank-meta-line">
      <StatusBadge :label="data.bank.visibility_label" :tone="data.bank.visibility === 'shared' ? 'success' : 'neutral'" />
      <span>{{ data.unit_summary?.complete_count ?? data.bank.question_count }} 道完整题</span>
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
        <span>{{ shownQuestions.length }} 题</span>
      </div>
      <EmptyState v-if="!shownQuestions.length" title="题库还没有题目">
        <p>通过题目文件导入后，题目会显示在这里。</p>
      </EmptyState>
      <div v-else class="kt-card kt-table-card">
        <div class="kt-table-scroll">
          <table class="kt-table">
            <thead>
              <tr><th>题库内序号</th><th>题目</th><th>题型</th><th>知识点</th><th>分值</th><th>状态</th><th>操作</th></tr>
            </thead>
            <tbody>
              <tr v-for="question in shownQuestions" :key="question.id">
                <td class="kt-mono">{{ question.sequence }}</td>
                <td><strong>{{ question.title || question.text }}</strong><p v-if="question.instructions">{{ question.instructions }}</p><ul v-if="question.draft_reasons.length" class="kt-draft-reasons"><li v-for="reason in question.draft_reasons" :key="reason">{{ reason }}</li></ul></td>
                <td>{{ question.question_type_label }}</td>
                <td>{{ question.kc_mapping.nodes.map((node) => node.name).join('、') || '未映射' }}</td>
                <td>{{ question.max_score || '1.00' }} 分</td>
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
