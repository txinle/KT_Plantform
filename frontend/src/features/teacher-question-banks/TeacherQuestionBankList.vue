<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { getBootstrapContext } from '@/app/bootstrap'
import Alert from '@/design-system/components/Alert.vue'
import EmptyState from '@/design-system/components/EmptyState.vue'
import MobileCardList from '@/design-system/components/MobileCardList.vue'
import PageHeader from '@/design-system/components/PageHeader.vue'
import Paginator from '@/design-system/components/Paginator.vue'
import StatusBadge from '@/design-system/components/StatusBadge.vue'

import {
  getTeacherQuestionBankApi,
  type CourseConceptCollection,
  type QuestionBankCollection,
} from './api'

const route = useRoute()
const router = useRouter()
const api = getTeacherQuestionBankApi()
const bootstrap = getBootstrapContext()
const courseId = Number(route.params.courseId)
const banks = ref<QuestionBankCollection>()
const concepts = ref<CourseConceptCollection>()
const loading = ref(true)
const error = ref('')
const filters = reactive({
  query: String(route.query.q || ''),
  conceptQuery: String(route.query.kc_q || ''),
})
const bankPage = computed(() => Number(route.query.page || 1))
const conceptPage = computed(() => Number(route.query.kc_page || 1))

function visibilityTone(visibility: string) {
  return visibility === 'shared' ? 'success' as const : 'neutral' as const
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [bankCollection, conceptCollection] = await Promise.all([
      api.list(courseId, { page: bankPage.value, query: filters.query }),
      api.courseConcepts(courseId, { page: conceptPage.value, query: filters.conceptQuery }),
    ])
    banks.value = bankCollection
    concepts.value = conceptCollection
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '题库数据加载失败。'
  } finally {
    loading.value = false
  }
}

async function applyQuery(kind: 'bank' | 'concept') {
  const query = { ...route.query }
  if (kind === 'bank') {
    if (filters.query) query.q = filters.query
    else delete query.q
    delete query.page
  } else {
    if (filters.conceptQuery) query.kc_q = filters.conceptQuery
    else delete query.kc_q
    delete query.kc_page
  }
  await router.push({ query })
  await load()
}

async function navigate(kind: 'bank' | 'concept', page: number) {
  const query = { ...route.query }
  const key = kind === 'bank' ? 'page' : 'kc_page'
  if (page === 1) delete query[key]
  else query[key] = String(page)
  await router.push({ query })
  await load()
}

onMounted(load)
</script>

<template>
  <PageHeader title="题库">
    <template #kicker>课程资源</template>
    <p>管理本课程题目与 KC 映射；跨课程共享题库只能复制为独立副本。</p>
    <template v-if="banks" #actions>
      <a class="kt-button kt-button--secondary" :href="banks.summary.shared_sources_url">浏览共享题库</a>
      <a class="kt-button kt-button--secondary" :href="banks.summary.archived_banks_url">
        归档题库 {{ banks.summary.archived_count }}
      </a>
    </template>
  </PageHeader>

  <Alert v-if="error" tone="danger">{{ error }}</Alert>
  <p v-if="loading" role="status">正在加载题库…</p>
  <template v-else-if="banks && concepts">
    <div class="kt-bank-list-layout">
      <section aria-labelledby="bank-list-title">
        <form class="kt-card kt-bank-search" role="search" @submit.prevent="applyQuery('bank')">
          <label for="bank-search">搜索题库</label>
          <div class="kt-inline-controls">
            <input id="bank-search" v-model="filters.query" type="search" placeholder="名称、说明或创建者">
            <button class="kt-button kt-button--secondary" type="submit">搜索</button>
          </div>
        </form>
        <EmptyState v-if="!banks.items.length" title="本课程还没有题库">
          <p>{{ banks.course.status === 'active' ? '使用创建表单建立空题库，或浏览共享题库复制副本。' : '归档课程保持只读。' }}</p>
        </EmptyState>
        <div v-else class="kt-card kt-table-card">
          <div class="kt-section-heading">
            <h2 id="bank-list-title">课程题库</h2>
            <span>{{ banks.page.total_items }} 个</span>
          </div>
          <div class="kt-table-scroll kt-desktop-only">
            <table class="kt-table">
              <thead><tr><th>题库</th><th>题目</th><th>可见性</th><th>最近更新</th><th><span class="kt-visually-hidden">操作</span></th></tr></thead>
              <tbody>
                <tr v-for="bank in banks.items" :key="bank.id">
                  <td>
                    <RouterLink :to="bank.detail_url"><strong>{{ bank.name }}</strong></RouterLink>
                    <span class="kt-cell-note">创建者：{{ bank.created_by_name }} · {{ bank.description || '未填写说明' }}</span>
                  </td>
                  <td>{{ bank.question_count }}</td>
                  <td>
                    <form v-if="bank.visibility_update_url" class="kt-compact-form" method="post" :action="bank.visibility_update_url">
                      <input type="hidden" name="csrfmiddlewaretoken" :value="bootstrap.csrf_token">
                      <label class="kt-visually-hidden" :for="`visibility-${bank.id}`">题库可见性</label>
                      <select :id="`visibility-${bank.id}`" name="visibility" :value="bank.visibility">
                        <option value="private">私有</option><option value="shared">共享</option>
                      </select>
                      <button class="kt-text-button" type="submit">保存</button>
                    </form>
                    <StatusBadge v-else :label="bank.visibility_label" :tone="visibilityTone(bank.visibility)" />
                  </td>
                  <td>{{ new Date(bank.updated_at).toLocaleString('zh-CN') }}</td>
                  <td><form v-if="bank.archive_url" method="post" :action="bank.archive_url"><input type="hidden" name="csrfmiddlewaretoken" :value="bootstrap.csrf_token"><button class="kt-text-button" type="submit">归档</button></form></td>
                </tr>
              </tbody>
            </table>
          </div>
          <MobileCardList class="kt-mobile-only">
            <li v-for="bank in banks.items" :key="bank.id" class="kt-mobile-bank-card">
              <div class="kt-section-heading"><RouterLink :to="bank.detail_url"><strong>{{ bank.name }}</strong></RouterLink><StatusBadge :label="bank.visibility_label" :tone="visibilityTone(bank.visibility)" /></div>
              <p>{{ bank.description || '未填写说明' }}</p>
              <dl><div><dt>题目</dt><dd>{{ bank.question_count }}</dd></div><div><dt>创建者</dt><dd>{{ bank.created_by_name }}</dd></div></dl>
              <div v-if="bank.can_edit_metadata" class="kt-inline-actions">
                <details v-if="bank.visibility_update_url"><summary>修改可见性</summary><form method="post" :action="bank.visibility_update_url"><input type="hidden" name="csrfmiddlewaretoken" :value="bootstrap.csrf_token"><select name="visibility" :value="bank.visibility"><option value="private">私有</option><option value="shared">共享</option></select><button class="kt-button kt-button--secondary" type="submit">保存</button></form></details>
                <form v-if="bank.archive_url" method="post" :action="bank.archive_url"><input type="hidden" name="csrfmiddlewaretoken" :value="bootstrap.csrf_token"><button class="kt-text-button" type="submit">归档</button></form>
              </div>
            </li>
          </MobileCardList>
          <Paginator :page="banks.page.number" :total-pages="banks.page.total_pages" @navigate="navigate('bank', $event)" />
        </div>
      </section>

      <aside v-if="banks.course.status === 'active'" class="kt-card kt-bank-create" aria-labelledby="create-bank-title">
        <h2 id="create-bank-title">创建题库</h2>
        <form data-create-bank method="post" :action="`/teacher/courses/${courseId}/banks/`">
          <input type="hidden" name="csrfmiddlewaretoken" :value="bootstrap.csrf_token">
          <label>名称<input name="name" maxlength="160" required></label>
          <label>说明<textarea name="description" rows="4" /></label>
          <label>可见性<select name="visibility"><option value="private">私有</option><option value="shared">共享</option></select></label>
          <button class="kt-button" type="submit">创建题库</button>
        </form>
      </aside>
    </div>

    <section class="kt-section" aria-labelledby="course-concept-title">
      <div class="kt-section-heading"><div><p class="kt-kicker">统一知识体系</p><h2 id="course-concept-title">课程 KC 总库</h2></div><span>{{ concepts.page.total_items }} 个</span></div>
      <p class="kt-muted">统一维护本课程知识点；题库详情只展示当前题库实际引用的子集。</p>
      <div class="kt-concept-layout">
        <div>
          <form class="kt-card kt-bank-search" role="search" @submit.prevent="applyQuery('concept')">
            <label for="concept-search">搜索 KC</label>
            <div class="kt-inline-controls"><input id="concept-search" v-model="filters.conceptQuery" type="search" placeholder="KC 名称"><button class="kt-button kt-button--secondary" type="submit">搜索</button></div>
          </form>
          <EmptyState v-if="!concepts.items.length" title="课程中还没有 KC"><p>可新增 KC，或在题库中导入 KC 对应表。</p></EmptyState>
          <div v-else class="kt-card kt-table-card">
            <div class="kt-table-scroll kt-desktop-only">
              <table class="kt-table">
                <thead><tr><th>KC</th><th>关联范围</th><th>题库分布</th><th>操作</th></tr></thead><tbody>
                  <tr v-for="concept in concepts.items" :key="concept.id">
                    <td><strong>{{ concept.name }}</strong></td><td>{{ concept.bank_count }} 个题库 · {{ concept.question_count }} 道题</td>
                    <td><span v-for="(item, index) in concept.distribution" :key="item.bank_id"><RouterLink :to="item.detail_url">{{ item.bank_name }}</RouterLink>：{{ item.question_count }} 题<span v-if="index < concept.distribution.length - 1">；</span></span><span v-if="!concept.distribution.length" class="kt-muted">尚未关联题目</span></td>
                    <td><details v-if="concept.rename_url"><summary>重命名</summary><form class="kt-inline-panel" method="post" :action="concept.rename_url"><input type="hidden" name="csrfmiddlewaretoken" :value="bootstrap.csrf_token"><label>KC 名称<input name="name" :value="concept.name" maxlength="160" required></label><button class="kt-button kt-button--secondary" type="submit">保存名称</button></form></details><details v-if="concept.delete_url"><summary class="kt-danger-summary">删除</summary><form class="kt-inline-panel kt-inline-panel--danger" method="post" :action="concept.delete_url"><input type="hidden" name="csrfmiddlewaretoken" :value="bootstrap.csrf_token"><p>确认删除未被任何题目引用的“{{ concept.name }}”？</p><button class="kt-button kt-button--danger" type="submit">确认删除</button></form></details><span v-if="concept.can_rename && !concept.can_delete" class="kt-cell-note">已引用，不能删除</span></td>
                  </tr>
                </tbody>
              </table>
            </div>
            <MobileCardList class="kt-mobile-only"><li v-for="concept in concepts.items" :key="concept.id" class="kt-mobile-bank-card"><div class="kt-section-heading"><strong>{{ concept.name }}</strong><span>{{ concept.question_count }} 题</span></div><p>{{ concept.bank_count }} 个题库</p><div class="kt-bank-tags"><RouterLink v-for="item in concept.distribution" :key="item.bank_id" :to="item.detail_url">{{ item.bank_name }} · {{ item.question_count }}</RouterLink></div><details v-if="concept.rename_url"><summary>重命名</summary><form class="kt-inline-panel" method="post" :action="concept.rename_url"><input type="hidden" name="csrfmiddlewaretoken" :value="bootstrap.csrf_token"><input name="name" :value="concept.name" maxlength="160" required><button class="kt-button kt-button--secondary" type="submit">保存</button></form></details></li></MobileCardList>
            <Paginator :page="concepts.page.number" :total-pages="concepts.page.total_pages" @navigate="navigate('concept', $event)" />
          </div>
        </div>
        <aside v-if="concepts.course.status === 'active'" class="kt-card kt-bank-create"><h2>新增课程 KC</h2><form data-create-concept method="post" :action="`/teacher/courses/${courseId}/concepts/create/`"><input type="hidden" name="csrfmiddlewaretoken" :value="bootstrap.csrf_token"><label>KC 名称<input name="name" maxlength="160" required></label><button class="kt-button" type="submit">新增 KC</button></form></aside>
      </div>
    </section>
  </template>
</template>
