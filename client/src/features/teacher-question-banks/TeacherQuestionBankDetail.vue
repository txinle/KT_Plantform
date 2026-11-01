<script setup lang="ts">
import { useBankPageUrl } from '../teacher-bank-management/bank-page-navigation'
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import ModalDialog from '@/design-system/components/ModalDialog.vue'
import TeacherBankEditor from '../teacher-bank-management/TeacherBankEditor.vue'
import Alert from '@/design-system/components/Alert.vue'
import EmptyState from '@/design-system/components/EmptyState.vue'
import MobileCardList from '@/design-system/components/MobileCardList.vue'
import PageHeader from '@/design-system/components/PageHeader.vue'
import Paginator from '@/design-system/components/Paginator.vue'
import StatusBadge from '@/design-system/components/StatusBadge.vue'
import QuestionPreviewDialog from '../teacher-bank-management/QuestionPreviewDialog.vue'
import QuestionBankInventory from '../teacher-bank-management/QuestionBankInventory.vue'
import { getQuestionGroupApi } from '../teacher-bank-management/group-api'

import {
  getTeacherQuestionBankApi,
  type BankConceptCollection,
  type QuestionBankDetail,
  type QuestionCollection,
} from './api'
import { getTeacherBankManagementApi } from '../teacher-bank-management/api'

const editBankOpen = ref(false)
const confirmation = ref<{ kind: 'archive' | 'bank' | 'question'; questionId?: number; unitKind?: string; label: string }>()
const actionBusy = ref(false)
async function confirmAction() {
  const pending = confirmation.value
  if (!pending || actionBusy.value) return
  actionBusy.value = true
  try {
    if (pending.kind === 'archive') await archiveBank()
    else if (pending.kind === 'bank') await deleteBank()
    else await deleteQuestion(pending.questionId!, pending.unitKind)
    if (!error.value) confirmation.value = undefined
  } finally { actionBusy.value = false }
}
async function finishBankEdit() { editBankOpen.value = false; await load() }
const route = useRoute()
const bankPageUrl = useBankPageUrl()
const router = useRouter()
const api = getTeacherQuestionBankApi()
const managementApi = getTeacherBankManagementApi()
const courseId = Number(route.params.courseId)
const bankId = Number(route.params.bankId)
const bank = ref<QuestionBankDetail>()
const questions = ref<QuestionCollection>()
const concepts = ref<BankConceptCollection>()
const loading = ref(true)
const error = ref('')
const success = ref('')
const rechecking = ref(false)
const previewQuestion = ref<{ id: number; group: boolean }>()
const questionFilter = ref({ query: '', type: '', status: '' })
async function filterQuestions(value: { query: string; type: string; status: string }) { questionFilter.value = value; await router.push({ query: { ...route.query, page: undefined } }); await load() }
const questionPage = computed(() => Number(route.query.page || 1))
const conceptPage = computed(() => Number(route.query.kc_page || 1))

function statusTone(status: string) {
  if (status === 'active') return 'success' as const
  if (status === 'draft') return 'warning' as const
  return 'neutral' as const
}

function visibilityTone(visibility: string) {
  return visibility === 'shared' ? 'success' as const : 'neutral' as const
}

function mappingText(mapping: QuestionCollection['items'][number]['kc_mapping']) {
  if (!mapping.root_kc_code) return '未映射'
  const children = new Map<string | null, typeof mapping.nodes>()
  for (const node of mapping.nodes) {
    const rows = children.get(node.parent_kc_code) || []
    rows.push(node)
    children.set(node.parent_kc_code, rows)
  }
  const render = (code: string): string => {
    const node = mapping.nodes.find((item) => item.kc_code === code)
    const descendants = (children.get(code) || []).sort((a, b) => a.order - b.order)
    if (!descendants.length) return node?.name || code
    const branch = descendants.map((item) => render(item.kc_code))
    return `${node?.name || code} → ${branch.length > 1 ? `{${branch.join('，')}}` : branch[0]}`
  }
  return render(mapping.root_kc_code)
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [detail, questionCollection, conceptCollection] = await Promise.all([
      api.detail(courseId, bankId),
      api.questions(courseId, bankId, { page: questionPage.value, query: questionFilter.value.query, questionType: questionFilter.value.type, status: questionFilter.value.status, completeOnly: true }),
      api.bankConcepts(courseId, bankId, { page: conceptPage.value }),
    ])
    bank.value = detail
    if (route.query.edit_group && detail.groups?.some(group => group.id === Number(route.query.edit_group))) {
      await router.replace(bankPageUrl(`/teacher/courses/${courseId}/banks/${bankId}/questions/0/edit/?group=${route.query.edit_group}`))
    }
    questions.value = questionCollection
    concepts.value = conceptCollection
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '题库详情加载失败。'
  } finally {
    loading.value = false
  }
}

async function navigate(kind: 'question' | 'concept', page: number) {
  const query = { ...route.query }
  const key = kind === 'question' ? 'page' : 'kc_page'
  if (page === 1) delete query[key]
  else query[key] = String(page)
  await router.push({ query })
  await load()
}

async function archiveBank() {
  error.value = ''
  try {
    await managementApi.archiveBank(courseId, bankId)
    await load()
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '归档题库失败。'
  }
}

async function restoreBank() {
  error.value = ''
  try {
    await managementApi.restoreBank(courseId, bankId)
    await load()
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '恢复题库失败。'
  }
}

async function deleteBank() {
  error.value = ''
  try {
    await managementApi.deleteBank(courseId, bankId)
    await router.push(bankPageUrl(`/teacher/courses/${courseId}/banks/`))
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '删除题库失败。'
  }
}

async function deleteQuestion(questionId: number, kind?: string) {
  error.value = ''
  try {
    if (kind === 'group') await getQuestionGroupApi(bankId, courseId).remove(questionId)
    else await managementApi.deleteQuestion(courseId, bankId, questionId)
    await load()
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '删除题目失败。'
  }
}

async function recheckDrafts() {
  rechecking.value = true
  error.value = ''
  success.value = ''
  try {
    const result = await managementApi.recheckQuestions(courseId, bankId)
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
  <p v-if="loading" role="status">正在加载题库详情…</p>
  <Alert v-else-if="!bank || !questions || !concepts" tone="danger">{{ error || '题库不存在或无法访问。' }}</Alert>
  <template v-else>
    <ModalDialog v-if="confirmation" :open="true" :title="confirmation.kind === 'archive' ? '归档题库' : confirmation.kind === 'bank' ? '永久删除题库' : '删除完整题目'" @close="!actionBusy && (confirmation = undefined)">
      <p>确认{{ confirmation.kind === 'archive' ? '归档' : '删除' }}“{{ confirmation.label }}”？</p>
      <p>{{ confirmation.kind === 'archive' ? '归档后保留题目与研究数据，可恢复为私有题库。' : '完整题目将连同所属小题一并处理。已被实验或快照引用时，服务器会拒绝删除。' }}</p>
      <Alert v-if="error" tone="danger">{{ error }}</Alert>
      <template #footer><button class="kt-button kt-button--secondary" type="button" :disabled="actionBusy" @click="confirmation = undefined">取消</button><button class="kt-button" type="button" :data-bank-delete="confirmation.kind === 'bank' ? '' : undefined" :data-delete-question="confirmation.kind === 'question' ? '' : undefined" data-confirm-bank-action :disabled="actionBusy" @click="confirmAction">{{ confirmation.kind === 'archive' ? '确认归档' : '确认删除' }}</button></template>
    </ModalDialog>
    <ModalDialog v-if="editBankOpen" :open="true" title="编辑题库" @close="editBankOpen = false"><TeacherBankEditor :bank-id="bankId" embedded @saved="finishBankEdit" @cancelled="editBankOpen = false" /></ModalDialog>
    <nav class="kt-bank-breadcrumb" aria-label="层级目录">
      <a :href="bankPageUrl('/teacher/courses/')">{{ route.path?.startsWith('/manage/resources/') ? '课程管理' : '我的课程' }}</a><span aria-hidden="true">/</span>
      <a :href="bankPageUrl(`/teacher/courses/${courseId}/`)">{{ bank.course.name }}</a><span aria-hidden="true">/</span>
      <a :href="bankPageUrl(bank.list_url)">题库</a><span aria-hidden="true">/</span>
      <strong aria-current="page">{{ bank.name }}</strong>
    </nav>
    <PageHeader class="kt-bank-detail-header" :title="bank.name">
      <p>{{ bank.description || '未填写题库说明。' }}</p>
      <template #actions>
        <a v-if="bank.edit_url" data-bank-edit class="kt-button kt-button--secondary" :href="bankPageUrl(bank.edit_url)" @click.prevent="editBankOpen = true">编辑题库</a>
        <button v-if="bank.restore_url" data-bank-restore class="kt-button kt-button--secondary" type="button" @click="restoreBank">恢复为私有</button>

        <a v-if="bank.question_import_url" data-question-import class="kt-button kt-button--secondary" :href="bankPageUrl(bank.question_import_url)">导入题目</a>

        <a v-if="bank.can_edit_content" class="kt-button" :href="bankPageUrl(`/teacher/courses/${courseId}/banks/${bankId}/questions/0/edit/`)">新建题目</a>
      </template>
    </PageHeader>


    <nav class="kt-bank-module-nav" aria-label="题库模块"><a :href="bankPageUrl(bank.list_url)" aria-current="page">题库管理</a><a :href="bankPageUrl(`${bank.list_url}?tab=course-kcs`)">课程 KC 总库</a><a :href="bankPageUrl(`${bank.list_url}shared/`)">共享题库</a><a :href="bankPageUrl(`${bank.list_url}archived/`)">已归档</a></nav>
    <Alert v-if="error" tone="danger">{{ error }}</Alert>
    <Alert v-if="success" tone="success">{{ success }}</Alert>
    <div class="kt-bank-meta-line" aria-label="题库概要">
      <StatusBadge :label="bank.visibility_label" :tone="visibilityTone(bank.visibility)" />
      <span>创建者：{{ bank.created_by_name }}</span>
      <span>共 <strong>{{ bank.unit_summary?.complete_count ?? bank.question_count }}</strong> 道题</span><span>可用 <strong>{{ bank.unit_summary?.usable_count ?? '—' }}</strong></span><span>草稿 <strong>{{ bank.unit_summary ? bank.unit_summary.complete_count - bank.unit_summary.usable_count : '—' }}</strong></span>
      <div class="kt-bank-meta-actions">
        <button v-if="bank.delete_url" class="kt-text-button kt-text-button--danger" type="button" @click="error = ''; confirmation = { kind: 'bank', label: bank.name }">永久删除</button>
        <button v-if="bank.can_edit_content" data-recheck-drafts class="kt-button kt-button--secondary" type="button" :disabled="rechecking" @click="recheckDrafts">{{ rechecking ? '正在复核…' : '复核草稿' }}</button>        <button v-if="bank.archive_url" data-bank-archive class="kt-button kt-button--secondary" type="button" @click="error = ''; confirmation = { kind: 'archive', label: bank.name }">归档题库</button>
      </div>
    </div>
    <Alert v-if="bank.visibility === 'archived'" tone="warning">题库已归档。可查看历史题目与 KC 映射，但不能修改题目内容。</Alert>


    <QuestionBankInventory :compact="true" :result-count="questions.page.total_items" :summary="bank.unit_summary" :groups="bank.groups" :can-edit="bank.can_edit_content" @filter="filterQuestions" @edit="router.push(bankPageUrl(`/teacher/courses/${courseId}/banks/${bankId}/questions/0/edit/`))" />
    <section class="kt-section kt-bank-question-section" aria-labelledby="question-list-title">
      <h2 id="question-list-title" class="kt-visually-hidden">完整题目</h2>
      <EmptyState v-if="!questions.items.length" title="没有符合条件的题目"><p>调整筛选条件，或通过题目文件导入。</p></EmptyState>
      <div v-else class="kt-card kt-table-card">
        <div class="kt-table-scroll">
          <table class="kt-table">
            <thead><tr><th>序号</th><th>题目</th><th>题型</th><th>知识点结构</th><th>难度 / 分值</th><th>状态</th><th>操作</th></tr></thead><tbody>
              <tr v-for="question in questions.items" :key="question.unit_key || `question:${question.id}`"><td class="kt-mono">{{ question.sequence }}</td><td><a v-if="question.edit_url" :href="bankPageUrl(question.edit_url)"><strong>{{ question.title || question.text }}</strong></a><strong v-else>{{ question.title || question.text }}</strong><p v-if="question.instructions" class="kt-bank-question-description">{{ question.instructions }}</p><ul v-if="question.draft_reasons.length" class="kt-draft-reasons"><li v-for="reason in question.draft_reasons" :key="reason">{{ reason }}</li></ul></td><td>{{ question.question_type_label }}</td><td><div class="kt-kc-structure" :aria-label="`知识点结构：${mappingText(question.kc_mapping)}`"><span v-for="node in question.kc_mapping.nodes" :key="node.kc_code" class="kt-kc-node" :data-depth="node.depth">{{ node.name }}</span><span v-if="!question.kc_mapping.nodes.length && !question.member_kc_mappings?.length">未映射</span><details v-if="question.member_kc_mappings?.length"><summary>{{ question.member_kc_mappings.length }} 个作答项 KC</summary><p v-for="member in question.member_kc_mappings" :key="member.id">{{ member.order }}. {{ mappingText(member.mapping) }}</p></details></div></td><td>{{ question.teacher_difficulty ?? '未设置' }}<small class="kt-cell-note">{{ question.max_score || '1.00' }} 分</small></td><td><StatusBadge :label="question.status_label" :tone="statusTone(question.status)" /></td><td><div class="kt-inline-actions"><button class="kt-bank-view-link" type="button" @click="previewQuestion = { id: question.id, group: question.unit_kind === 'group' }">查看</button><a v-if="question.edit_url" :href="bankPageUrl(question.edit_url)">编辑</a><button v-if="question.delete_url" class="kt-text-button kt-text-button--danger" type="button" data-request-delete-question @click="error = ''; confirmation = { kind: 'question', questionId: question.id, unitKind: question.unit_kind, label: `第 ${question.sequence} 题` }">删除</button></div></td></tr>
            </tbody>
          </table>
        </div>

        <Paginator :page="questions.page.number" :total-pages="questions.page.total_pages" @navigate="navigate('question', $event)" />
      </div>
    </section>

    <section class="kt-section kt-bank-concept-section" aria-labelledby="bank-concept-title">
      <div class="kt-section-heading"><div><p class="kt-kicker">当前题库</p><h2 id="bank-concept-title">KC 使用情况</h2></div><span>{{ concepts.page.total_items }} 个 KC</span></div>
      <EmptyState v-if="!concepts.items.length" title="本题库尚未建立 KC 映射"><p>可通过 KC 对应表批量维护，或在单题编辑页选择课程 KC。</p></EmptyState>
      <div v-else class="kt-card kt-table-card"><div class="kt-table-scroll kt-desktop-only"><table class="kt-table"><thead><tr><th>KC</th><th>一级</th><th>二级</th><th>三级</th><th>关联题目</th></tr></thead><tbody><tr v-for="concept in concepts.items" :key="concept.id"><td><strong>{{ concept.name }}</strong><span class="kt-cell-note kt-mono">{{ concept.kc_code }}</span></td><td>{{ concept.level_1_count }}</td><td>{{ concept.level_2_count }}</td><td>{{ concept.level_3_count }}</td><td><span v-for="(question, index) in concept.questions" :key="question.id"><a v-if="question.edit_url" class="kt-mono" :href="bankPageUrl(question.edit_url)">第 {{ question.sequence }} 题</a><span v-else class="kt-mono">第 {{ question.sequence }} 题</span><span v-if="index < concept.questions.length - 1">、</span></span></td></tr></tbody></table></div><MobileCardList class="kt-mobile-only"><li v-for="concept in concepts.items" :key="concept.id" class="kt-mobile-bank-card"><h3>{{ concept.name }} <span class="kt-mono">{{ concept.kc_code }}</span></h3><p>一级 {{ concept.level_1_count }} · 二级 {{ concept.level_2_count }} · 三级 {{ concept.level_3_count }}</p><div class="kt-bank-tags"><a v-for="question in concept.questions" :key="question.id" :href="bankPageUrl(question.edit_url || undefined)">第 {{ question.sequence }} 题</a></div></li></MobileCardList><Paginator :page="concepts.page.number" :total-pages="concepts.page.total_pages" @navigate="navigate('concept', $event)" /></div>
    </section>
    <QuestionPreviewDialog v-if="previewQuestion" :id="previewQuestion.id" :course-id="courseId" :bank-id="bankId" :group="previewQuestion.group" @close="previewQuestion = undefined" />
  </template>
</template>
