<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { getBootstrapContext } from '@/app/bootstrap'
import Alert from '@/design-system/components/Alert.vue'

import { getTeacherQuestionBankApi, type CourseConceptCollection, type CourseConceptImportPreview, type QuestionBankCollection } from './api'

const route = useRoute()
const router = useRouter()
const api = getTeacherQuestionBankApi()
const bootstrap = getBootstrapContext()
const courseId = Number(route.params.courseId)
const banks = ref<QuestionBankCollection>()
const concepts = ref<CourseConceptCollection>()
const loading = ref(true)
const error = ref('')
const activeTab = computed(() => route.query.tab === 'course-kcs' ? 'course-kcs' : 'banks')
const bankForm = reactive({ name: '', description: '', visibility: 'private' })
const conceptForm = reactive({ sequence: '', name: '', description: '' })
const generatedCode = computed(() => Number(conceptForm.sequence) > 0 ? `KC${Number(conceptForm.sequence)}` : '')
const importInput = ref<HTMLInputElement>()
const importPreview = ref<CourseConceptImportPreview>()
const importBusy = ref(false)

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [bankCollection, conceptCollection] = await Promise.all([
      api.list(courseId, { page: Number(route.query.page || 1) }),
      api.courseConcepts(courseId, { page: Number(route.query.page || 1), all: true }),
    ])
    banks.value = bankCollection
    concepts.value = conceptCollection
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '题库数据加载失败。'
  } finally {
    loading.value = false
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

function importClassificationLabel(value: string) {
  return ({ new: '可新增', unchanged: '已存在', archived_unchanged: '已归档', conflict: '冲突' } as Record<string, string>)[value] || value
}

function formattedDate(value: string) {
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

onMounted(load)
</script>

<template>
  <nav v-if="banks" class="kt-qb-breadcrumb" aria-label="层级目录">
    <a href="/teacher/courses/">我的课程</a><span>/</span>
    <a :href="`/teacher/courses/${courseId}/`">{{ banks.course.name }}</a><span>/</span>
    <strong aria-current="page">题库</strong>
  </nav>

  <header class="kt-qb-titlebar">
    <div>
      <h1>题库</h1>
      <p>管理本课程题目与 KC 映射；跨课程共享题库只能复制为独立副本。</p>
    </div>
    <div v-if="banks" class="kt-qb-title-actions">
      <a class="kt-qb-btn kt-qb-btn--outline" :href="banks.summary.shared_sources_url">浏览共享题库</a>
      <a class="kt-qb-btn kt-qb-btn--outline" :href="banks.summary.archived_banks_url">归档题库 {{ banks.summary.archived_count }}</a>
    </div>
  </header>

  <nav v-if="banks && concepts" class="kt-qb-tabs" :class="`is-${activeTab}`" aria-label="题库模块">
    <button data-tab="banks" :class="{ 'is-active': activeTab === 'banks' }" type="button" @click="selectTab('banks')">
      题库管理 <span>{{ banks.page.total_items }}</span>
    </button>
    <button data-tab="course-kcs" :class="{ 'is-active': activeTab === 'course-kcs' }" type="button" @click="selectTab('course-kcs')">
      课程 KC 总库 <span>{{ concepts.summary.total }}</span>
    </button>
  </nav>

  <Alert v-if="error" tone="danger">{{ error }}</Alert>
  <p v-if="loading" class="kt-qb-loading" role="status">正在加载题库…</p>

  <template v-else-if="banks && concepts">
    <div v-if="activeTab === 'banks'" class="kt-qb-grid" data-panel="banks">
      <div class="kt-qb-main-column">
        <section class="kt-qb-card kt-qb-bank-card" aria-labelledby="course-bank-heading">
          <div class="kt-qb-card-title"><h2 id="course-bank-heading">课程题库</h2><span>{{ banks.page.total_items }} 个</span></div>
          <div class="kt-qb-table-wrap kt-qb-desktop-table">
            <table class="kt-qb-table kt-qb-bank-table">
              <thead><tr><th>题库</th><th>题目数</th><th>可见性</th><th>最近更新</th><th>操作</th></tr></thead>
              <tbody>
                <tr v-for="bank in banks.items" :key="bank.id">
                  <td><RouterLink :to="bank.detail_url"><strong>{{ bank.name }}</strong></RouterLink><small>创建者：{{ bank.created_by_name }}</small><small>{{ bank.description }}</small></td>
                  <td>{{ bank.question_count }}</td>
                  <td><span class="kt-qb-badge" :class="`is-${bank.visibility}`">{{ bank.visibility_label }}</span></td>
                  <td>{{ formattedDate(bank.updated_at) }}</td>
                  <td><div class="kt-qb-row-actions"><RouterLink class="kt-qb-btn kt-qb-btn--outline" :to="bank.detail_url">管理</RouterLink><form v-if="bank.archive_url" method="post" :action="bank.archive_url"><input type="hidden" name="csrfmiddlewaretoken" :value="bootstrap.csrf_token"><button class="kt-qb-btn kt-qb-btn--outline" type="submit">归档</button></form></div></td>
                </tr>
              </tbody>
            </table>
          </div>
          <ul class="kt-qb-mobile-list"><li v-for="bank in banks.items" :key="bank.id"><div><RouterLink :to="bank.detail_url"><strong>{{ bank.name }}</strong></RouterLink><span class="kt-qb-badge">{{ bank.visibility_label }}</span></div><p>{{ bank.description }}</p><dl><div><dt>题目数</dt><dd>{{ bank.question_count }}</dd></div><div><dt>创建者</dt><dd>{{ bank.created_by_name }}</dd></div></dl><RouterLink class="kt-qb-btn kt-qb-btn--outline" :to="bank.detail_url">管理</RouterLink></li></ul>
        </section>

        <section class="kt-qb-card kt-qb-kc-entry" aria-labelledby="kc-entry-heading">
          <div><h2 id="kc-entry-heading">课程 KC 总库</h2><p>课程总库只维护 KC 的稳定身份，二三级关系由题目映射单独定义。</p></div>
          <div><span>{{ concepts.summary.total }} 个</span><button class="kt-qb-btn kt-qb-btn--outline" type="button" @click="selectTab('course-kcs')">进入 KC 总库</button></div>
        </section>
      </div>

      <aside v-if="banks.course.status === 'active'" class="kt-qb-card kt-qb-form-card">
        <h2>创建题库</h2>
        <form data-create-bank @submit.prevent="createBank">
          <label>题库名称 <span>必填</span><input v-model="bankForm.name" name="name" maxlength="160" placeholder="请输入题库名称" required></label>
          <label>说明 <span>可选</span><textarea v-model="bankForm.description" name="description" rows="5" placeholder="请输入说明" /></label>
          <label>可见性 <span>必填</span><select v-model="bankForm.visibility" name="visibility"><option value="private">私有</option><option value="shared">共享</option></select></label>
          <button class="kt-qb-btn kt-qb-btn--primary" type="submit">创建题库</button>
        </form>
      </aside>
    </div>

    <div v-else class="kt-qb-grid" data-panel="course-kcs">
      <div class="kt-qb-main-column kt-qb-main-column--kc">
        <section class="kt-qb-card kt-qb-overview" aria-labelledby="kc-overview-heading">
          <h2 id="kc-overview-heading">课程 KC 总览</h2>
          <dl><div><dt>KC 总数</dt><dd>{{ concepts.summary.total }}</dd></div><div><dt>有效</dt><dd>{{ concepts.summary.active }}</dd></div><div><dt>已被题目使用</dt><dd>{{ concepts.summary.used }}</dd></div><div><dt>已归档</dt><dd>{{ concepts.summary.archived }}</dd></div></dl>
        </section>

        <section class="kt-qb-card kt-qb-kc-list" aria-labelledby="kc-list-heading">
          <div class="kt-qb-card-title"><h2 id="kc-list-heading">课程 KC 列表</h2><span>{{ concepts.page.total_items }} 个</span></div>
          <div class="kt-qb-table-wrap kt-qb-desktop-table">
            <table class="kt-qb-table">
              <thead><tr><th>KC</th><th>使用题目</th><th>题库分布</th><th>角色分布</th><th>状态</th><th>操作</th></tr></thead><tbody>
                <tr v-for="concept in concepts.items" :key="concept.id"><td><strong>{{ concept.name }}</strong><small>{{ concept.kc_code }}</small></td><td>{{ concept.question_count }}</td><td>{{ concept.bank_count }}</td><td>一级 {{ concept.role_counts.level_1 }} · 二级 {{ concept.role_counts.level_2 }} · 三级 {{ concept.role_counts.level_3 }}</td><td><span class="kt-qb-badge" :class="concept.status === 'active' ? 'is-active' : 'is-archived'">{{ concept.status === 'active' ? '有效' : '已归档' }}</span></td><td><div class="kt-qb-row-actions"><details v-if="concept.rename_url"><summary class="kt-qb-btn kt-qb-btn--outline">重命名</summary><form class="kt-qb-popover" method="post" :action="concept.rename_url"><input type="hidden" name="csrfmiddlewaretoken" :value="bootstrap.csrf_token"><input name="name" :value="concept.name" required><textarea name="description" :value="concept.description" /><button class="kt-qb-btn kt-qb-btn--primary" type="submit">保存</button></form></details><form v-if="concept.delete_url" method="post" :action="concept.delete_url"><input type="hidden" name="csrfmiddlewaretoken" :value="bootstrap.csrf_token"><button class="kt-qb-btn kt-qb-btn--outline" type="submit">归档</button></form></div></td></tr>
              </tbody>
            </table>
          </div>
          <ul class="kt-qb-mobile-list"><li v-for="concept in concepts.items" :key="concept.id"><div><strong>{{ concept.name }}</strong><span class="kt-qb-badge">{{ concept.status === 'active' ? '有效' : '已归档' }}</span></div><p>{{ concept.kc_code }} · {{ concept.question_count }} 道题</p><p>一级 {{ concept.role_counts.level_1 }} · 二级 {{ concept.role_counts.level_2 }} · 三级 {{ concept.role_counts.level_3 }}</p></li></ul>
        </section>
      </div>

      <div class="kt-qb-side-stack">
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
          <h2>批量导入 KC</h2><p>上传 CSV 后按序号生成 KC1、KC2...</p><div><a data-course-kc-template class="kt-qb-btn kt-qb-btn--outline" download="course_kc_template.csv" href="/teacher/import-templates/course-kcs.csv">下载模板</a><button class="kt-qb-btn kt-qb-btn--primary" type="button" :disabled="importBusy" @click="importInput?.click()">批量导入 KC</button><input ref="importInput" data-course-kc-file class="kt-qb-visually-hidden" type="file" accept=".csv,.xlsx" @change="previewCourseConceptImport"></div><small>上传文件&nbsp; → &nbsp;校验预览&nbsp; → &nbsp;确认导入</small>
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
