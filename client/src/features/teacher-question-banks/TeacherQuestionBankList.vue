<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { ApiError } from '@/api/client'
import CourseConceptSearch from './CourseConceptSearch.vue'
import Alert from '@/design-system/components/Alert.vue'
import TeacherBankEditor from '../teacher-bank-management/TeacherBankEditor.vue'
import ModalDialog from '@/design-system/components/ModalDialog.vue'
import Paginator from '@/design-system/components/Paginator.vue'

import { getTeacherQuestionBankApi, type CourseConceptCollection, type CourseConceptImportPreview, type QuestionBankCollection } from './api'
import { getTeacherBankManagementApi } from '../teacher-bank-management/api'

const route = useRoute()
const router = useRouter()
const api = getTeacherQuestionBankApi()
const managementApi = getTeacherBankManagementApi()
const courseId = Number(route.params.courseId)
const banks = ref<QuestionBankCollection>()
const concepts = ref<CourseConceptCollection>()
const loading = ref(true)
const error = ref('')
const bankQuery = ref('')
const bankVisibility = ref('')
const createOpen = ref(false)
const editBankId = ref<number>()
const archiveBankId = ref<number>()
async function finishBankEdit() { editBankId.value = undefined; await load() }
const currentPage = ref(1)
async function searchBanks() { currentPage.value = 1; await load() }
async function navigateBanks(page: number) { currentPage.value = page; await load() }
const archived = computed(() => route.query.tab === 'archived-kcs')
const activeTab = computed(() => archived.value || route.query.tab === 'course-kcs' ? 'course-kcs' : 'banks')
function desktopPageSize() {
  // Desktop measurements reserve 480 CSS pixels for the shell, overview, controls and margins.
  if (window.innerWidth < 1280) return 5
  return Math.min(20, Math.max(5, Math.floor((window.innerHeight - 480) / 54)))
}
const conceptPageSize = ref(desktopPageSize())
function resizeConceptList() {
  const size = desktopPageSize()
  if (size === conceptPageSize.value) return
  conceptPageSize.value = size
  if (activeTab.value !== 'course-kcs') return
  if (conceptPage.value > 1) void router.replace({ query: { ...route.query, page: 1 } })
  else void load()
}
onMounted(() => window.addEventListener('resize', resizeConceptList))
onBeforeUnmount(() => window.removeEventListener('resize', resizeConceptList))
const conceptPage = computed(() => Number(route.query.page) || 1)
const conceptQuery = computed(() => typeof route.query.q === 'string' ? route.query.q : '')
const conceptId = computed(() => Number(route.query.concept_id) || undefined)
const conceptStatus = computed(() => archived.value ? 'archived' : 'active')
function filterConcepts(query: string, id?: number) {
  void router.push({ query: { tab: route.query.tab, ...(query ? { q: query } : {}), ...(id ? { concept_id: id } : {}) } })
}
const conceptToDelete = ref<CourseConceptCollection['items'][number]>()
const notice = ref('')
let latestLoad = 0
const bankForm = reactive({ name: '', description: '', visibility: 'private' })
const conceptForm = reactive({ sequence: '', name: '', description: '' })
const generatedCode = computed(() => Number(conceptForm.sequence) > 0 ? `KC${Number(conceptForm.sequence)}` : '')
const importInput = ref<HTMLInputElement>()
const importPreview = ref<CourseConceptImportPreview>()
const importBusy = ref(false)
const actionBusy = ref(false)
const conceptRename = reactive({ id: 0, name: '', description: '' })

async function load() {
  const request = ++latestLoad
  loading.value = true
  error.value = ''
  try {
    const [bankCollection, conceptCollection] = await Promise.all([
      api.list(courseId, { page: currentPage.value, query: bankQuery.value, visibility: bankVisibility.value }),
      api.courseConcepts(courseId, {
        page: conceptPage.value, pageSize: conceptPageSize.value, status: conceptStatus.value,
        query: conceptId.value ? undefined : conceptQuery.value || undefined,
        conceptId: conceptId.value,
      }),
    ])
    if (request !== latestLoad) return
    banks.value = bankCollection
    concepts.value = conceptCollection
  } catch (caught) {
    if (request !== latestLoad) return
    concepts.value = undefined
    if (conceptPage.value > 1 && caught instanceof ApiError && caught.status === 404) {
      await router.replace({ query: { ...route.query, page: 1 } })
      return
    }
    error.value = caught instanceof Error ? caught.message : '题库数据加载失败。'
  } finally {
    if (request === latestLoad) loading.value = false
  }
}

async function selectTab(tab: 'banks' | 'course-kcs') {
  await router.push({ query: tab === 'course-kcs' ? { tab } : {} })
}

async function createBank() {
  error.value = ''
  try {
    await api.createBank(courseId, { ...bankForm })
    Object.assign(bankForm, { name: '', description: '', visibility: 'private' })
    createOpen.value = false
    await load()
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '创建题库失败。'
  }
}

async function createConcept() {
  error.value = ''
  try {
    await api.createCourseConcept(courseId, {
      sequence: Number(conceptForm.sequence),
      name: conceptForm.name,
      description: conceptForm.description,
    })
    Object.assign(conceptForm, { sequence: '', name: '', description: '' })
    await load()
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '新增 KC 失败。'
  }
}

async function previewCourseConceptImport(event: Event) {
  const input = event.currentTarget as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  importBusy.value = true
  error.value = ''
  try {
    importPreview.value = await api.previewCourseConceptImport(courseId, file)
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : 'KC 文件校验失败。'
  } finally {
    importBusy.value = false
    input.value = ''
  }
}

async function confirmCourseConceptImport() {
  if (!importPreview.value?.preview.confirmable) return
  importBusy.value = true
  error.value = ''
  try {
    await api.confirmCourseConceptImport(courseId, importPreview.value.batch_id)
    importPreview.value = undefined
    await load()
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : 'KC 导入确认失败，请重新生成预览。'
  } finally {
    importBusy.value = false
  }
}

async function archiveBank(bankId: number) {
  actionBusy.value = true
  error.value = ''
  try {
    await managementApi.archiveBank(courseId, bankId)
    archiveBankId.value = undefined
    await load()
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '归档题库失败。'
  } finally {
    actionBusy.value = false
  }
}

function prepareConceptRename(concept: CourseConceptCollection['items'][number]) {
  Object.assign(conceptRename, {
    id: concept.id,
    name: concept.name,
    description: concept.description,
  })
}

async function renameConcept(conceptId: number) {
  actionBusy.value = true
  error.value = ''
  try {
    await managementApi.updateCourseConcept(courseId, conceptId, {
      name: conceptRename.name,
      description: conceptRename.description,
    })
    conceptRename.id = 0
    await load()
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '重命名 KC 失败。'
  } finally {
    actionBusy.value = false
  }
}

async function setConceptStatus(conceptId: number, status: 'active' | 'archived') {
  actionBusy.value = true
  error.value = ''
  try {
    await managementApi.setCourseConceptStatus(courseId, conceptId, status)
    await load()
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : `${status === 'active' ? '恢复' : '归档'} KC 失败。`
  } finally {
    actionBusy.value = false
  }
}

function importClassificationLabel(value: string) {
  return ({ new: '可新增', unchanged: '已存在', archived_unchanged: '已归档', conflict: '冲突' } as Record<string, string>)[value] || value
}

function formattedDate(value: string) {
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

async function deleteConcept() {
  const target = conceptToDelete.value
  if (!target || actionBusy.value) return
  actionBusy.value = true
  error.value = ''
  notice.value = ''
  try {
    const result = await managementApi.deleteCourseConcept(courseId, target.id)
    notice.value = result?.status === 'archived'
      ? `“${target.name}”仍有引用，已归档。`
      : `已永久删除“${target.name}”。`
    conceptToDelete.value = undefined
    await load()
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '删除 KC 失败。'
  } finally {
    actionBusy.value = false
  }
}

watch([archived, conceptPage, conceptQuery, conceptId], ([nextArchived], [previousArchived]) => {
  if (nextArchived !== previousArchived) notice.value = ''
  conceptToDelete.value = undefined
  conceptRename.id = 0
  void load()
}, { immediate: true })
</script>

<template>
  <h1 class="kt-visually-hidden">{{ activeTab === 'banks' ? '题库' : archived ? '已归档课程 KC' : '课程 KC 总库' }}</h1>

  <div v-if="banks && concepts" class="kt-bank-module-tabs">
    <nav class="kt-qb-tabs kt-sidebar-secondary-nav" :class="`is-${activeTab}`" role="tablist" aria-label="题库模块" aria-orientation="horizontal">
      <button id="question-bank-tab-banks" data-tab="banks" :class="{ 'is-active': activeTab === 'banks' }" type="button" role="tab" :aria-selected="activeTab === 'banks'" aria-controls="question-bank-panel" @click="selectTab('banks')">
        题库管理 <span>{{ banks.page.total_items }}</span>
      </button>
      <button id="question-bank-tab-course-kcs" data-tab="course-kcs" :class="{ 'is-active': activeTab === 'course-kcs' }" type="button" role="tab" :aria-selected="activeTab === 'course-kcs'" aria-controls="course-kc-panel" @click="selectTab('course-kcs')">
        课程 KC 总库 <span>{{ concepts.summary.active }}</span>
      </button>
      <a :href="banks.summary.shared_sources_url">共享题库</a>
      <a :href="banks.summary.archived_banks_url">已归档</a>
    </nav>
  </div>

  <ModalDialog v-if="conceptToDelete" :open="true" title="删除课程 KC" @close="!actionBusy && (conceptToDelete = undefined)">
    <p>确认删除“{{ conceptToDelete.name }}”（{{ conceptToDelete.kc_code }}）？无引用时将永久删除，有引用时将归档并保留记录。</p>
    <Alert v-if="error" tone="danger">{{ error }}</Alert>
    <template #footer>
      <button class="kt-button kt-button--secondary" type="button" :disabled="actionBusy" @click="conceptToDelete = undefined">取消</button>
      <button class="kt-button kt-button--danger" type="button" data-confirm-concept-delete :disabled="actionBusy" @click="deleteConcept">确认删除</button>
    </template>
  </ModalDialog>
  <p v-if="notice" role="status">{{ notice }}</p>
  <ModalDialog v-if="archiveBankId" :open="true" title="归档题库" @close="!actionBusy && (archiveBankId = undefined)">
    <p>归档“{{ banks?.items.find(bank => bank.id === archiveBankId)?.name }}”后保留题目和已有研究记录，可在已归档列表恢复为私有题库。</p><Alert v-if="error" tone="danger">{{ error }}</Alert>
    <template #footer><button class="kt-button kt-button--secondary" type="button" :disabled="actionBusy" @click="archiveBankId = undefined">取消</button><button class="kt-button" type="button" data-confirm-bank-archive :disabled="actionBusy" @click="archiveBank(archiveBankId)">确认归档</button></template>
  </ModalDialog>
  <ModalDialog v-if="editBankId" :open="true" title="编辑题库" @close="editBankId = undefined"><TeacherBankEditor :bank-id="editBankId" embedded @saved="finishBankEdit" @cancelled="editBankId = undefined" /></ModalDialog>
  <Alert v-if="error" tone="danger">{{ error }}</Alert>
  <p v-if="loading" class="kt-qb-loading" role="status">正在加载题库…</p>

  <template v-else-if="banks && concepts">
    <div v-if="activeTab === 'banks'" id="question-bank-panel" class="kt-qb-grid" data-panel="banks" role="tabpanel" aria-labelledby="question-bank-tab-banks">
      <div class="kt-qb-main-column">
        <form class="kt-site-bank-toolbar" @submit.prevent="searchBanks">
          <input v-model="bankQuery" aria-label="搜索题库" placeholder="搜索题库名称或说明" @change="searchBanks">
          <select v-model="bankVisibility" aria-label="题库可见性" @change="searchBanks">
            <option value="">全部可见性</option>
            <option value="private">私有题库</option>
            <option value="shared">共享题库</option>
          </select>
          <button v-if="banks.course.status === 'active'" class="kt-qb-btn kt-qb-btn--primary" type="button" @click="createOpen = true">新建题库</button>
          <span>{{ banks.page.total_items }} 个题库</span>
        </form>
        <section class="kt-qb-card kt-qb-bank-card" aria-label="课程题库">
          <div class="kt-qb-table-wrap kt-qb-desktop-table">
            <table class="kt-qb-table kt-qb-bank-table">
              <thead><tr><th>题库</th><th>题型构成</th><th>可用 / 草稿</th><th>可见性</th><th>创建者 / 更新</th><th>操作</th></tr></thead>
              <tbody>
                <tr v-for="bank in banks.items" :key="bank.id">
                  <td><RouterLink :to="bank.detail_url"><strong>{{ bank.name }}</strong></RouterLink><small>{{ bank.description || '未填写说明' }}</small></td>
                  <td><div class="kt-site-bank-types"><span v-for="item in bank.unit_summary?.types" :key="item.value">{{ item.label }} <b>{{ item.count }}</b></span><span v-if="!bank.unit_summary">{{ bank.complete_question_count ?? bank.question_count }} 道</span></div></td><td>{{ bank.unit_summary?.usable_count ?? 0 }} / {{ (bank.unit_summary?.complete_count ?? 0) - (bank.unit_summary?.usable_count ?? 0) }}</td>
                  <td><span class="kt-qb-badge" :class="`is-${bank.visibility}`">{{ bank.visibility_label }}</span></td>
                  <td>{{ bank.created_by_name }}<small>{{ formattedDate(bank.updated_at) }}</small></td>
                  <td><div class="kt-qb-row-actions"><RouterLink class="kt-qb-btn kt-qb-btn--outline" :to="bank.detail_url">管理</RouterLink><a v-if="bank.can_edit_metadata" :href="`${bank.detail_url}edit/`" :data-edit-bank="bank.id" @click.prevent="editBankId = bank.id">编辑信息</a><RouterLink :to="`${bank.detail_url}import/`">导入</RouterLink><button v-if="bank.archive_url" data-bank-archive class="kt-qb-btn kt-qb-btn--outline" type="button" :disabled="actionBusy" @click="error = ''; archiveBankId = bank.id">归档</button></div></td>
                </tr>
              </tbody>
            </table>
          </div>
          <ul class="kt-qb-mobile-list"><li v-for="bank in banks.items" :key="bank.id"><div><RouterLink :to="bank.detail_url"><strong>{{ bank.name }}</strong></RouterLink><span class="kt-qb-badge" :class="`is-${bank.visibility}`">{{ bank.visibility_label }}</span></div><p>{{ bank.description }}</p><dl><div><dt>题目数</dt><dd>{{ bank.complete_question_count ?? bank.question_count }}</dd></div><div><dt>创建者</dt><dd>{{ bank.created_by_name }}</dd></div></dl><div class="kt-qb-row-actions"><RouterLink class="kt-qb-btn kt-qb-btn--outline" :to="bank.detail_url">管理</RouterLink><button v-if="bank.archive_url" data-bank-archive class="kt-qb-btn kt-qb-btn--outline" type="button" :disabled="actionBusy" @click="error = ''; archiveBankId = bank.id">归档</button></div></li></ul>
        </section>

        <Paginator :page="banks.page.number" :total-pages="banks.page.total_pages" @navigate="navigateBanks" /><section class="kt-qb-card kt-qb-kc-entry" aria-labelledby="kc-entry-heading">
          <div><h2 id="kc-entry-heading">课程 KC 总库</h2><p>课程总库只维护 KC 的稳定身份，二三级关系由题目映射单独定义。</p></div>
          <div><span>{{ concepts.summary.active }} 个有效 KC</span><button class="kt-qb-btn kt-qb-btn--outline" type="button" @click="selectTab('course-kcs')">进入 KC 总库</button></div>
        </section>
      </div>

      <ModalDialog v-if="banks.course.status === 'active'" :open="createOpen" title="创建题库" @close="createOpen = false">
        <Alert v-if="error" tone="danger">{{ error }}</Alert>
        <form id="create-bank-form" class="kt-bank-modal-form" data-create-bank @submit.prevent="createBank">
          <label>题库名称 <span>必填</span><input v-model="bankForm.name" name="name" maxlength="160" placeholder="请输入题库名称" required></label>
          <label>说明 <span>可选</span><textarea v-model="bankForm.description" name="description" rows="5" placeholder="请输入说明" /></label>
          <label>可见性 <span>必填</span><select v-model="bankForm.visibility" name="visibility"><option value="private">私有</option><option value="shared">共享</option></select></label>
        </form>
        <template #footer><button class="kt-button kt-button--secondary" type="button" @click="createOpen = false">取消</button><button class="kt-button" type="submit" form="create-bank-form">创建题库</button></template>
      </ModalDialog>
    </div>

    <div v-else id="course-kc-panel" class="kt-qb-grid" :data-panel="archived ? 'archived-kcs' : 'course-kcs'" role="tabpanel" aria-labelledby="question-bank-tab-course-kcs">
      <div class="kt-qb-main-column kt-qb-main-column--kc">
        <section v-if="!archived" class="kt-qb-card kt-qb-overview" aria-labelledby="kc-overview-heading">
          <h2 id="kc-overview-heading">课程 KC 总览</h2>
          <dl><div><dt>KC 总数</dt><dd>{{ concepts.summary.total }}</dd></div><div><dt>有效</dt><dd>{{ concepts.summary.active }}</dd></div><div><dt>已被题目使用</dt><dd>{{ concepts.summary.used }}</dd></div><div><dt>已归档</dt><dd>{{ concepts.summary.archived }}</dd></div></dl>
        </section>

        <section class="kt-qb-card kt-qb-kc-list" aria-labelledby="kc-list-heading">
          <div class="kt-qb-card-title">
            <h2 id="kc-list-heading">{{ archived ? '已归档 KC 列表' : '课程 KC 列表' }}</h2>
            <CourseConceptSearch :course-id="courseId" :status="conceptStatus" :initial-query="conceptQuery" @filter="filterConcepts" />
            <RouterLink v-if="archived" class="kt-qb-btn kt-qb-btn--outline" :to="`/teacher/courses/${courseId}/banks/?tab=course-kcs`">返回课程 KC 总库</RouterLink>
            <RouterLink v-else data-archived-kcs class="kt-qb-btn kt-qb-btn--outline" :to="`/teacher/courses/${courseId}/banks/?tab=archived-kcs`">查看已归档 KC</RouterLink>
          </div>
          <div class="kt-qb-table-wrap kt-qb-desktop-table">
            <table class="kt-qb-table">
              <thead><tr><th>KC</th><th>使用题目</th><th>题库分布</th><th>角色分布</th><th>状态</th><th>操作</th></tr></thead><tbody>
                <tr v-for="concept in concepts.items" :key="concept.id"><td><strong>{{ concept.name }}</strong><small>{{ concept.kc_code }}</small></td><td>{{ concept.question_count }}</td><td>{{ concept.bank_count }}</td><td>一级 {{ concept.role_counts.level_1 }} · 二级 {{ concept.role_counts.level_2 }} · 三级 {{ concept.role_counts.level_3 }}</td><td><span class="kt-qb-badge" :class="concept.status === 'active' ? 'is-active' : 'is-archived'">{{ concept.status === 'active' ? '有效' : '已归档' }}</span></td><td><div class="kt-qb-row-actions"><details v-if="concept.can_rename && concept.rename_url"><summary data-concept-rename class="kt-qb-btn kt-qb-btn--outline" @click="prepareConceptRename(concept)">重命名</summary><form v-if="conceptRename.id === concept.id" data-concept-rename-form class="kt-qb-popover" @submit.prevent="renameConcept(concept.id)"><input v-model="conceptRename.name" name="name" required><textarea v-model="conceptRename.description" name="description" /><button class="kt-qb-btn kt-qb-btn--primary" type="submit" :disabled="actionBusy">保存</button></form></details><button v-if="concept.can_archive" data-concept-archive class="kt-qb-btn kt-qb-btn--outline" type="button" :disabled="actionBusy" @click="setConceptStatus(concept.id, 'archived')">归档</button><button v-if="concept.can_restore" data-concept-restore class="kt-qb-btn kt-qb-btn--outline" type="button" :disabled="actionBusy" @click="setConceptStatus(concept.id, 'active')">恢复</button><button v-if="concept.can_delete && concept.delete_url" data-concept-delete class="kt-qb-btn kt-qb-btn--outline" type="button" :disabled="actionBusy" @click="error = ''; conceptToDelete = concept">删除</button></div></td></tr>
              </tbody>
            </table>
          </div>
          <ul class="kt-qb-mobile-list"><li v-for="concept in concepts.items" :key="concept.id"><div><strong>{{ concept.name }}</strong><span class="kt-qb-badge" :class="concept.status === 'active' ? 'is-active' : 'is-archived'">{{ concept.status === 'active' ? '有效' : '已归档' }}</span></div><p>{{ concept.kc_code }} · {{ concept.question_count }} 道题</p><p>一级 {{ concept.role_counts.level_1 }} · 二级 {{ concept.role_counts.level_2 }} · 三级 {{ concept.role_counts.level_3 }}</p><div class="kt-qb-row-actions"><details v-if="concept.can_rename && concept.rename_url"><summary data-concept-rename class="kt-qb-btn kt-qb-btn--outline" @click="prepareConceptRename(concept)">重命名</summary><form v-if="conceptRename.id === concept.id" data-concept-rename-form class="kt-qb-popover" @submit.prevent="renameConcept(concept.id)"><label>知识点名称<input v-model="conceptRename.name" name="name" required></label><label>说明<textarea v-model="conceptRename.description" name="description" /></label><button class="kt-qb-btn kt-qb-btn--primary" type="submit" :disabled="actionBusy">保存</button></form></details><button v-if="concept.can_archive" data-concept-archive class="kt-qb-btn kt-qb-btn--outline" type="button" :disabled="actionBusy" @click="setConceptStatus(concept.id, 'archived')">归档</button><button v-if="concept.can_restore" data-concept-restore class="kt-qb-btn kt-qb-btn--outline" type="button" :disabled="actionBusy" @click="setConceptStatus(concept.id, 'active')">恢复</button><button v-if="concept.can_delete && concept.delete_url" data-concept-delete class="kt-qb-btn kt-qb-btn--outline" type="button" :disabled="actionBusy" @click="error = ''; conceptToDelete = concept">删除</button></div></li></ul>
          <p v-if="!concepts.items.length">{{ conceptQuery || conceptId ? '没有匹配的课程 KC。' : archived ? '暂无已归档的 KC。' : '暂无有效的课程 KC。' }}</p>
          <Paginator :page="concepts.page.number" :total-pages="concepts.page.total_pages" @navigate="page => router.push({ query: { ...route.query, page } })" />
        </section>
      </div>

      <div v-if="!archived && concepts.can_manage" class="kt-qb-side-stack">
        <aside class="kt-qb-card kt-qb-form-card">
          <h2>新增课程 KC</h2>
          <form data-create-concept @submit.prevent="createConcept">
            <label>KC 序号 <span>必填</span><small>系统将根据序号自动生成唯一 KC Code</small><input v-model="conceptForm.sequence" name="sequence" type="number" min="1" step="1" placeholder="请输入正整数" required></label>
            <label>KC Code<input :value="generatedCode" readonly></label>
            <label>KC 名称 <span>必填</span><input v-model="conceptForm.name" name="name" maxlength="160" placeholder="请输入 KC 名称" required></label>
            <label>说明（可选） <span>可选</span><textarea v-model="conceptForm.description" name="description" rows="4" placeholder="请输入说明" /></label>
            <button class="kt-qb-btn kt-qb-btn--primary" type="submit">新增 KC</button>
          </form>
        </aside>
        <aside class="kt-qb-card kt-qb-import-card" data-course-kc-import>
          <h2>批量导入 KC</h2><p>上传 CSV 后按序号生成 KC1、KC2...</p><div><a data-course-kc-template class="kt-qb-btn kt-qb-btn--outline" download="course_kc_template.csv" href="/api/v1/teacher/import-templates/course-kcs.csv">下载模板</a><button class="kt-qb-btn kt-qb-btn--primary" type="button" :disabled="importBusy" @click="importInput?.click()">批量导入 KC</button><input ref="importInput" data-course-kc-file class="kt-qb-visually-hidden" type="file" accept=".csv,.xlsx" @change="previewCourseConceptImport"></div><small>上传文件&nbsp; → &nbsp;校验预览&nbsp; → &nbsp;确认导入</small>
        </aside>
      </div>
    </div>
  </template>

  <div v-if="importPreview" class="kt-qb-modal-backdrop" role="presentation" @click.self="importPreview = undefined">
    <section class="kt-qb-modal" role="dialog" aria-modal="true" aria-labelledby="course-kc-preview-title">
      <header><div><h2 id="course-kc-preview-title">校验预览</h2><p>确认后仅新增明确列出的课程 KC，现有节点保持不变。</p></div><button class="kt-qb-modal-close" type="button" aria-label="关闭" @click="importPreview = undefined">×</button></header>
      <div class="kt-qb-import-steps"><strong>上传文件</strong><span>→</span><strong>校验预览</strong><span>→</span><span>确认导入</span></div>
      <div class="kt-qb-import-preview-table"><table><thead><tr><th>序号</th><th>KC Code</th><th>知识点名称</th><th>校验结果</th></tr></thead><tbody><tr v-for="row in importPreview.preview.rows" :key="row.sequence"><td>{{ row.sequence }}</td><td>{{ row.code }}</td><td>{{ row.name }}</td><td><span class="kt-qb-badge" :class="row.classification === 'conflict' ? 'is-conflict' : 'is-active'">{{ importClassificationLabel(row.classification) }}</span><small v-if="row.error">{{ row.error }}</small></td></tr></tbody></table></div>
      <p v-if="!importPreview.preview.confirmable" class="kt-qb-import-conflict">预览包含冲突，修正文件后重新上传。</p>
      <footer><button class="kt-qb-btn kt-qb-btn--outline" type="button" @click="importPreview = undefined">取消</button><button class="kt-qb-btn kt-qb-btn--primary" data-confirm-course-kc-import type="button" :disabled="importBusy || !importPreview.preview.confirmable" @click="confirmCourseConceptImport">确认导入</button></footer>
    </section>
  </div>
</template>
