<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { getBootstrapContext } from '@/app/bootstrap'
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

const route = useRoute()
const router = useRouter()
const api = getTeacherQuestionBankApi()
const bootstrap = getBootstrapContext()
const courseId = Number(route.params.courseId)
const bankId = Number(route.params.bankId)
const bank = ref<QuestionBankDetail>()
const questions = ref<QuestionCollection>()
const concepts = ref<BankConceptCollection>()
const loading = ref(true)
const error = ref('')
const filters = reactive({
  query: String(route.query.q || ''),
  questionType: String(route.query.question_type || ''),
  status: String(route.query.status || ''),
  conceptQuery: String(route.query.kc_q || ''),
})
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

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [detail, questionCollection, conceptCollection] = await Promise.all([
      api.detail(courseId, bankId),
      api.questions(courseId, bankId, {
        page: questionPage.value,
        query: filters.query,
        questionType: filters.questionType,
        status: filters.status,
      }),
      api.bankConcepts(courseId, bankId, {
        page: conceptPage.value,
        query: filters.conceptQuery,
      }),
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

async function applyFilter(kind: 'question' | 'concept') {
  const query = { ...route.query }
  if (kind === 'question') {
    for (const [key, value] of [['q', filters.query], ['question_type', filters.questionType], ['status', filters.status]] as const) {
      if (value) query[key] = value
      else delete query[key]
    }
    delete query.page
  } else {
    if (filters.conceptQuery) query.kc_q = filters.conceptQuery
    else delete query.kc_q
    delete query.kc_page
  }
  await router.push({ query })
  await load()
}

async function navigate(kind: 'question' | 'concept', page: number) {
  const query = { ...route.query }
  const key = kind === 'question' ? 'page' : 'kc_page'
  if (page === 1) delete query[key]
  else query[key] = String(page)
  await router.push({ query })
  await load()
}

onMounted(load)
</script>

<template>
  <p v-if="loading" role="status">正在加载题库详情…</p>
  <Alert v-else-if="!bank || !questions || !concepts" tone="danger">{{ error || '题库不存在或无法访问。' }}</Alert>
  <template v-else>
    <PageHeader :title="bank.name">
      <template #kicker>{{ bank.course.code }} · {{ bank.course.term }}</template>
      <p>{{ bank.description || '未填写题库说明。' }}</p>
      <template #actions>
        <a v-if="bank.edit_url" class="kt-button kt-button--secondary" :href="bank.edit_url">编辑题库</a>
        <a v-if="bank.question_import_url" class="kt-button" :href="bank.question_import_url">导入题目文件</a>
        <a v-if="bank.kc_import_url" class="kt-button kt-button--secondary" :href="bank.kc_import_url">导入 KC 对应表</a>
        <form v-if="bank.archive_url" method="post" :action="bank.archive_url"><input type="hidden" name="csrfmiddlewaretoken" :value="bootstrap.csrf_token"><button class="kt-button kt-button--secondary" type="submit">归档题库</button></form>
        <form v-if="bank.restore_url" method="post" :action="bank.restore_url"><input type="hidden" name="csrfmiddlewaretoken" :value="bootstrap.csrf_token"><button class="kt-button kt-button--secondary" type="submit">恢复为私有</button></form>
      </template>
    </PageHeader>

    <Alert v-if="error" tone="danger">{{ error }}</Alert>
    <section class="kt-card kt-bank-summary" aria-label="题库概要">
      <StatusBadge :label="bank.visibility_label" :tone="visibilityTone(bank.visibility)" />
      <span>创建者：{{ bank.created_by_name }}</span>
      <span>{{ bank.question_count }} 道题</span>
      <span>更新：{{ new Date(bank.updated_at).toLocaleString('zh-CN') }}</span>
      <details v-if="bank.delete_url" class="kt-bank-danger"><summary>永久删除题库</summary><form class="kt-inline-panel kt-inline-panel--danger" method="post" :action="bank.delete_url"><input type="hidden" name="csrfmiddlewaretoken" :value="bootstrap.csrf_token"><input type="hidden" name="confirmation" value="delete"><p>永久删除会移除题库及未被研究数据引用的题目；已被实验或快照引用时，服务器会拒绝操作。</p><button class="kt-button kt-button--danger" type="submit">确认永久删除</button></form></details>
    </section>
    <Alert v-if="bank.visibility === 'archived'" tone="warning">题库已归档。可查看历史题目与 KC 映射，但不能修改题目内容。</Alert>
    <Alert v-else tone="info">题目文件用于创建题干、选项和答案；KC 对应表只维护当前题库与课程 KC 的映射。</Alert>

    <section class="kt-section" aria-labelledby="question-list-title">
      <div class="kt-section-heading"><h2 id="question-list-title">题目清单</h2><span>{{ questions.page.total_items }} 题</span></div>
      <form class="kt-card kt-question-filter" @submit.prevent="applyFilter('question')">
        <label>搜索<input v-model="filters.query" type="search" placeholder="题干、题号、稳定代码或 KC"></label>
        <label>题型<select v-model="filters.questionType"><option value="">全部题型</option><option v-for="choice in questions.filters.question_types" :key="choice.value" :value="choice.value">{{ choice.label }}</option></select></label>
        <label>状态<select v-model="filters.status"><option value="">全部状态</option><option v-for="choice in questions.filters.statuses" :key="choice.value" :value="choice.value">{{ choice.label }}</option></select></label>
        <button class="kt-button kt-button--secondary" type="submit">筛选</button>
      </form>
      <EmptyState v-if="!questions.items.length" title="没有符合条件的题目"><p>调整筛选条件，或通过题目文件导入。</p></EmptyState>
      <div v-else class="kt-card kt-table-card">
        <div class="kt-table-scroll kt-desktop-only">
          <table class="kt-table">
            <thead><tr><th>课程题号</th><th>稳定课程代码</th><th>题库序号</th><th>题目</th><th>题型</th><th>主 KC</th><th>难度</th><th>状态</th><th>操作</th></tr></thead><tbody>
              <tr v-for="question in questions.items" :key="question.id"><td class="kt-mono">{{ question.course_sequence ?? '—' }}</td><td class="kt-mono">{{ question.stable_code || '—' }}</td><td class="kt-mono">{{ question.sequence }}</td><td><strong>{{ question.text }}</strong><span class="kt-cell-note kt-mono">{{ question.code }}</span></td><td>{{ question.question_type_label }}</td><td>{{ question.primary_concept?.name || '未映射' }}</td><td>{{ question.teacher_difficulty ?? '未设置' }}</td><td><StatusBadge :label="question.status_label" :tone="statusTone(question.status)" /></td><td><div class="kt-inline-actions"><a v-if="question.edit_url" :href="question.edit_url">编辑</a><details v-if="question.delete_url"><summary class="kt-danger-summary">删除</summary><form class="kt-inline-panel kt-inline-panel--danger" method="post" :action="question.delete_url"><input type="hidden" name="csrfmiddlewaretoken" :value="bootstrap.csrf_token"><input type="hidden" name="confirmation" value="delete"><p>确认永久删除“{{ question.code }}”？已被实验或快照引用时服务器会拒绝操作。</p><button class="kt-button kt-button--danger" type="submit">确认删除</button></form></details></div></td></tr>
            </tbody>
          </table>
        </div>
        <MobileCardList class="kt-mobile-only"><li v-for="question in questions.items" :key="question.id" class="kt-mobile-question-card"><div class="kt-section-heading"><span class="kt-mono">{{ question.stable_code || question.code }}</span><StatusBadge :label="question.status_label" :tone="statusTone(question.status)" /></div><h3>{{ question.text }}</h3><dl><div><dt>题型</dt><dd>{{ question.question_type_label }}</dd></div><div><dt>主 KC</dt><dd>{{ question.primary_concept?.name || '未映射' }}</dd></div><div><dt>难度</dt><dd>{{ question.teacher_difficulty ?? '未设置' }}</dd></div><div><dt>题库序号</dt><dd>{{ question.sequence }}</dd></div></dl><div v-if="question.edit_url || question.delete_url" class="kt-inline-actions"><a v-if="question.edit_url" class="kt-button kt-button--secondary" :href="question.edit_url">编辑</a><details v-if="question.delete_url"><summary class="kt-danger-summary">删除</summary><form class="kt-inline-panel kt-inline-panel--danger" method="post" :action="question.delete_url"><input type="hidden" name="csrfmiddlewaretoken" :value="bootstrap.csrf_token"><input type="hidden" name="confirmation" value="delete"><button class="kt-button kt-button--danger" type="submit">确认永久删除</button></form></details></div></li></MobileCardList>
        <Paginator :page="questions.page.number" :total-pages="questions.page.total_pages" @navigate="navigate('question', $event)" />
      </div>
    </section>

    <section class="kt-section" aria-labelledby="bank-concept-title">
      <div class="kt-section-heading"><div><p class="kt-kicker">当前题库</p><h2 id="bank-concept-title">章节 KC</h2></div><span>{{ concepts.page.total_items }} 个</span></div>
      <form class="kt-card kt-bank-search" @submit.prevent="applyFilter('concept')"><label for="bank-kc-search">搜索当前题库 KC</label><div class="kt-inline-controls"><input id="bank-kc-search" v-model="filters.conceptQuery" type="search"><button class="kt-button kt-button--secondary" type="submit">搜索</button></div></form>
      <EmptyState v-if="!concepts.items.length" title="本题库尚未建立 KC 映射"><p>可通过 KC 对应表批量维护，或在单题编辑页选择课程 KC。</p></EmptyState>
      <div v-else class="kt-card kt-table-card"><div class="kt-table-scroll kt-desktop-only"><table class="kt-table"><thead><tr><th>KC</th><th>映射分布</th><th>关联题目</th></tr></thead><tbody><tr v-for="concept in concepts.items" :key="concept.id"><td><strong>{{ concept.name }}</strong></td><td>主 KC：{{ concept.primary_count }} · 次 KC：{{ concept.secondary_count }}</td><td><span v-for="(question, index) in concept.questions" :key="question.id"><a v-if="question.edit_url" class="kt-mono" :href="question.edit_url">{{ question.code }}</a><span v-else class="kt-mono">{{ question.code }}</span><span v-if="index < concept.questions.length - 1">、</span></span></td></tr></tbody></table></div><MobileCardList class="kt-mobile-only"><li v-for="concept in concepts.items" :key="concept.id" class="kt-mobile-bank-card"><h3>{{ concept.name }}</h3><p>主 KC {{ concept.primary_count }} · 次 KC {{ concept.secondary_count }} · 共 {{ concept.question_count }} 题</p><div class="kt-bank-tags"><a v-for="question in concept.questions" :key="question.id" :href="question.edit_url || undefined">{{ question.code }}</a></div></li></MobileCardList><Paginator :page="concepts.page.number" :total-pages="concepts.page.total_pages" @navigate="navigate('concept', $event)" /></div>
    </section>
  </template>
</template>
