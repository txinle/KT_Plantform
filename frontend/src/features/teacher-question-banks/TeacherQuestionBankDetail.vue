<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import Alert from '@/design-system/components/Alert.vue'
import EmptyState from '@/design-system/components/EmptyState.vue'
import MobileCardList from '@/design-system/components/MobileCardList.vue'
import PageHeader from '@/design-system/components/PageHeader.vue'
import Paginator from '@/design-system/components/Paginator.vue'
import StatusBadge from '@/design-system/components/StatusBadge.vue'

import {
  getTeacherQuestionBankApi,
  type BankConceptCollection,
  type QuestionBankDetail,
  type QuestionCollection,
} from './api'
import { getTeacherBankManagementApi } from '../teacher-bank-management/api'

const route = useRoute()
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
      api.questions(courseId, bankId, { page: questionPage.value }),
      api.bankConcepts(courseId, bankId, { page: conceptPage.value }),
    ])
    bank.value = detail
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
    await router.push(`/teacher/courses/${courseId}/banks/`)
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '删除题库失败。'
  }
}

async function deleteQuestion(questionId: number) {
  error.value = ''
  try {
    await managementApi.deleteQuestion(courseId, bankId, questionId)
    await load()
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '删除题目失败。'
  }
}

onMounted(load)
</script>

<template>
  <p v-if="loading" role="status">正在加载题库详情…</p>
  <Alert v-else-if="!bank || !questions || !concepts" tone="danger">{{ error || '题库不存在或无法访问。' }}</Alert>
  <template v-else>
    <div class="kt-bank-course-context"><strong>{{ bank.course.name }}</strong><span>{{ bank.course.code }} · {{ bank.course.term }}</span></div>
    <nav class="kt-bank-breadcrumb" aria-label="层级目录">
      <a href="/teacher/courses/">我的课程</a><span aria-hidden="true">/</span>
      <a :href="`/teacher/courses/${courseId}/`">{{ bank.course.name }}</a><span aria-hidden="true">/</span>
      <a :href="bank.list_url">题库</a><span aria-hidden="true">/</span>
      <strong aria-current="page">{{ bank.name }}</strong>
    </nav>
    <PageHeader class="kt-bank-detail-header" :title="bank.name">
      <template #kicker>{{ bank.course.code }} · {{ bank.course.term }}</template>
      <p>{{ bank.description || '未填写题库说明。' }}</p>
      <template #actions>
        <a v-if="bank.edit_url" data-bank-edit class="kt-button kt-button--secondary" :href="bank.edit_url">编辑题库</a>
        <button v-if="bank.archive_url" data-bank-archive class="kt-button kt-button--secondary" type="button" @click="archiveBank">归档题库</button>
        <button v-if="bank.restore_url" data-bank-restore class="kt-button kt-button--secondary" type="button" @click="restoreBank">恢复为私有</button>
        <details v-if="bank.delete_url" class="kt-header-danger"><summary class="kt-button kt-button--danger-outline">永久删除</summary><section class="kt-inline-panel kt-inline-panel--danger"><p>永久删除会移除题库及未被研究数据引用的题目；已被实验或快照引用时，服务器会拒绝操作。</p><button data-bank-delete class="kt-button kt-button--danger" type="button" @click="deleteBank">确认永久删除</button></section></details>
        <a v-if="bank.question_import_url" data-question-import class="kt-button" :href="bank.question_import_url">导入题目文件</a>
        <a v-if="bank.kc_import_url" data-kc-import class="kt-button kt-button--secondary" :href="bank.kc_import_url">导入 KC 对应表</a>
      </template>
    </PageHeader>

    <Alert v-if="error" tone="danger">{{ error }}</Alert>
    <div class="kt-bank-meta-line" aria-label="题库概要">
      <StatusBadge :label="bank.visibility_label" :tone="visibilityTone(bank.visibility)" />
      <span>创建者：{{ bank.created_by_name }}</span>
      <span>更新：{{ new Date(bank.updated_at).toLocaleString('zh-CN') }}</span>
    </div>
    <Alert v-if="bank.visibility === 'archived'" tone="warning">题库已归档。可查看历史题目与 KC 映射，但不能修改题目内容。</Alert>
    <Alert v-else tone="info">题目文件用于创建题干、选项和答案；KC 对应表只维护当前题库与课程 KC 的映射。</Alert>

    <section class="kt-section kt-bank-question-section" aria-labelledby="question-list-title">
      <div class="kt-section-heading"><h2 id="question-list-title">题目清单</h2><span>{{ questions.page.total_items }} 题</span></div>
      <EmptyState v-if="!questions.items.length" title="没有符合条件的题目"><p>调整筛选条件，或通过题目文件导入。</p></EmptyState>
      <div v-else class="kt-card kt-table-card">
        <div class="kt-table-scroll kt-desktop-only">
          <table class="kt-table">
            <thead><tr><th>课程题号</th><th>稳定课程代码</th><th>题库序号</th><th>题目</th><th>题型</th><th>知识点结构</th><th>难度</th><th>状态</th><th>操作</th></tr></thead><tbody>
              <tr v-for="question in questions.items" :key="question.id"><td class="kt-mono">{{ question.course_sequence ?? '—' }}</td><td class="kt-mono">{{ question.stable_code || '—' }}</td><td class="kt-mono">{{ question.sequence }}</td><td><strong>{{ question.text }}</strong><span class="kt-cell-note kt-mono">{{ question.code }}</span></td><td>{{ question.question_type_label }}</td><td><div class="kt-kc-structure" :aria-label="`知识点结构：${mappingText(question.kc_mapping)}`"><span v-for="node in question.kc_mapping.nodes" :key="node.kc_code" class="kt-kc-node" :data-depth="node.depth">{{ node.name }}</span><span v-if="!question.kc_mapping.nodes.length">未映射</span></div></td><td>{{ question.teacher_difficulty ?? '未设置' }}</td><td><StatusBadge :label="question.status_label" :tone="statusTone(question.status)" /></td><td><div class="kt-inline-actions"><a v-if="question.edit_url" :href="question.edit_url">编辑</a><details v-if="question.delete_url"><summary class="kt-danger-summary">删除</summary><section class="kt-inline-panel kt-inline-panel--danger"><p>确认永久删除“{{ question.code }}”？已被实验或快照引用时服务器会拒绝操作。</p><button data-delete-question class="kt-button kt-button--danger" type="button" @click="deleteQuestion(question.id)">确认删除</button></section></details></div></td></tr>
            </tbody>
          </table>
        </div>
        <MobileCardList class="kt-mobile-only"><li v-for="question in questions.items" :key="question.id" class="kt-mobile-question-card"><div class="kt-section-heading"><span class="kt-mono">{{ question.stable_code || question.code }}</span><StatusBadge :label="question.status_label" :tone="statusTone(question.status)" /></div><h3>{{ question.text }}</h3><dl><div><dt>题型</dt><dd>{{ question.question_type_label }}</dd></div><div><dt>知识点结构</dt><dd>{{ mappingText(question.kc_mapping) }}</dd></div><div><dt>难度</dt><dd>{{ question.teacher_difficulty ?? '未设置' }}</dd></div><div><dt>题库序号</dt><dd>{{ question.sequence }}</dd></div></dl><div v-if="question.edit_url || question.delete_url" class="kt-inline-actions"><a v-if="question.edit_url" class="kt-button kt-button--secondary" :href="question.edit_url">编辑</a><details v-if="question.delete_url"><summary class="kt-danger-summary">删除</summary><section class="kt-inline-panel kt-inline-panel--danger"><button class="kt-button kt-button--danger" type="button" @click="deleteQuestion(question.id)">确认永久删除</button></section></details></div></li></MobileCardList>
        <Paginator :page="questions.page.number" :total-pages="questions.page.total_pages" @navigate="navigate('question', $event)" />
      </div>
    </section>

    <section class="kt-section kt-bank-concept-section" aria-labelledby="bank-concept-title">
      <div class="kt-section-heading"><div><p class="kt-kicker">当前题库</p><h2 id="bank-concept-title">KC 使用情况</h2></div><span>{{ concepts.page.total_items }} 个 KC</span></div>
      <EmptyState v-if="!concepts.items.length" title="本题库尚未建立 KC 映射"><p>可通过 KC 对应表批量维护，或在单题编辑页选择课程 KC。</p></EmptyState>
      <div v-else class="kt-card kt-table-card"><div class="kt-table-scroll kt-desktop-only"><table class="kt-table"><thead><tr><th>KC</th><th>一级</th><th>二级</th><th>三级</th><th>关联题目</th></tr></thead><tbody><tr v-for="concept in concepts.items" :key="concept.id"><td><strong>{{ concept.name }}</strong><span class="kt-cell-note kt-mono">{{ concept.kc_code }}</span></td><td>{{ concept.level_1_count }}</td><td>{{ concept.level_2_count }}</td><td>{{ concept.level_3_count }}</td><td><span v-for="(question, index) in concept.questions" :key="question.id"><a v-if="question.edit_url" class="kt-mono" :href="question.edit_url">{{ question.code }}</a><span v-else class="kt-mono">{{ question.code }}</span><span v-if="index < concept.questions.length - 1">、</span></span></td></tr></tbody></table></div><MobileCardList class="kt-mobile-only"><li v-for="concept in concepts.items" :key="concept.id" class="kt-mobile-bank-card"><h3>{{ concept.name }} <span class="kt-mono">{{ concept.kc_code }}</span></h3><p>一级 {{ concept.level_1_count }} · 二级 {{ concept.level_2_count }} · 三级 {{ concept.level_3_count }}</p><div class="kt-bank-tags"><a v-for="question in concept.questions" :key="question.id" :href="question.edit_url || undefined">{{ question.code }}</a></div></li></MobileCardList><Paginator :page="concepts.page.number" :total-pages="concepts.page.total_pages" @navigate="navigate('concept', $event)" /></div>
    </section>
  </template>
</template>
