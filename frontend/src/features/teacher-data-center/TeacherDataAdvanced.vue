<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import Alert from '@/design-system/components/Alert.vue'
import EmptyState from '@/design-system/components/EmptyState.vue'
import PageHeader from '@/design-system/components/PageHeader.vue'
import Paginator from '@/design-system/components/Paginator.vue'
import Drawer from '@/design-system/components/Drawer.vue'
import type { StudentExperimentDetailResponse } from '@/features/teacher-analysis/api'

import { getTeacherDataCenterApi, routeQuery, type DataCenterAdvanced } from './api'

const route = useRoute()
const router = useRouter()
const courseId = Number(route.params.courseId)
const api = getTeacherDataCenterApi()
const data = ref<DataCenterAdvanced>()
const error = ref('')
const search = ref(typeof route.query.search === 'string' ? route.query.search : '')
const previewOpen = ref(false)
const preview = ref<StudentExperimentDetailResponse>()
const previewError = ref('')
let previewGeneration = 0

async function openPreview(student: DataCenterAdvanced['students'][number]) {
  const generation = ++previewGeneration
  previewOpen.value = true
  preview.value = undefined
  previewError.value = ''
  try {
    const result = await api.studentPreview(student.detail_api_url)
    if (generation === previewGeneration) preview.value = result
  } catch {
    if (generation === previewGeneration) previewError.value = '学生作答详情暂时无法加载，请关闭后重试。'
  }
}
function closePreview() { previewGeneration += 1; previewOpen.value = false }
function displayDate(value: string) { return new Date(value).toLocaleString('zh-CN', { hour12: false }) }

async function load(query = routeQuery(route.query)) {
  error.value = ''
  try { data.value = await api.advanced(courseId, query) }
  catch { error.value = '学生作答预览暂时无法加载，请刷新页面后重试。' }
}
async function submitSearch() {
  const query = { ...route.query, search: search.value.trim() || undefined, page: undefined }
  await router.push({ query })
  await load(routeQuery(query))
}
async function navigate(page: number) {
  const query = { ...route.query }
  if (page === 1) delete query.page
  else query.page = String(page)
  await router.push({ query })
  await load(routeQuery(query))
}
onMounted(() => load())
</script>

<template>
  <PageHeader title="学生作答预览"><template #kicker>教师数据预览</template><p>先查看学生进度，再按实验和题目展开已正式提交的答案与过程。</p></PageHeader>
  <Alert v-if="error" tone="danger">{{ error }}</Alert><p v-if="!data && !error" role="status">正在加载学生作答预览…</p>
  <template v-else-if="data">
    <EmptyState v-if="!data.scope.is_complete" title="请先选择实验和班级"><p>预览只读取当前明确的教学范围。</p></EmptyState>
    <template v-else>
      <form class="kt-card kt-preview-search" role="search" @submit.prevent="submitSearch">
        <label for="student-preview-search">搜索学生</label>
        <div><input id="student-preview-search" v-model="search" type="search" placeholder="输入学号或姓名"><button class="kt-button" type="submit">搜索</button></div>
      </form>
      <section class="kt-card kt-table-card" aria-labelledby="student-preview-title">
        <div class="kt-section-heading"><div><h2 id="student-preview-title">学生列表</h2><p>每名学生一行；未提交题目只计入进度，不显示草稿内容。</p></div><strong>共 {{ data.page.total_items }} 人</strong></div>
        <div v-if="data.students.length" class="kt-table-scroll"><table class="kt-table"><thead><tr><th>学号</th><th>姓名</th><th>班级</th><th>参与实验</th><th>提交进度</th><th>状态</th><th>操作</th></tr></thead><tbody><tr v-for="student in data.students" :key="student.detail_url"><td>{{ student.student_number }}</td><td>{{ student.display_name }}</td><td>{{ student.class_name }}</td><td>{{ student.experiment_count }}</td><td>{{ student.progress_label }}</td><td>{{ student.status_label }}</td><td><a :href="student.detail_url" @click.prevent="openPreview(student)">查看详情</a></td></tr></tbody></table></div>
        <EmptyState v-else title="没有匹配的学生"><p>可改用学号或姓名重新搜索。</p></EmptyState>
        <Paginator :page="data.page.number" :total-pages="data.page.total_pages" @navigate="navigate" />
      </section>
    </template>
  </template>
  <Drawer class="kt-preview-drawer" :open="previewOpen" title="学生作答详情" @close="closePreview">
    <Alert v-if="previewError" tone="danger">{{ previewError }}</Alert>
    <p v-else-if="!preview" role="status">正在加载已提交题目…</p>
    <template v-else>
      <h2>{{ preview.detail.display_name }} · {{ preview.detail.experiment_name }}</h2>
      <p>{{ preview.detail.student_number }} · 仅显示本次任务已正式提交的题目</p>
      <EmptyState v-if="!preview.detail.questions.length" title="还没有正式提交的题目" />
      <div v-else class="kt-table-scroll">
        <table class="kt-table">
          <thead><tr><th>题号</th><th>最终答案</th><th>结果 / 得分</th><th>用时 / 修改</th><th>信心</th><th>提交状态 / 时间</th><th>操作</th></tr></thead>
          <tbody>
            <tr v-for="row in preview.detail.questions" :key="row.process_url">
              <td data-label="题号">{{ row.code }}<small>{{ row.question_summary }}</small></td><td data-label="最终答案" class="kt-preview-answer">{{ row.final_answer.length > 100 ? `${row.final_answer.slice(0, 100)}…` : row.final_answer }}</td><td data-label="结果 / 得分">{{ row.result_label }} · {{ row.score_display }}</td>
              <td data-label="用时 / 修改">{{ row.answer_time_display }}<small>{{ row.revision_count === null ? '不适用可比修改次数' : `修改 ${row.revision_count} 次` }}</small></td>
              <td data-label="信心">{{ row.confidence_label }}</td><td data-label="提交状态 / 时间">{{ row.submission_status_label }}<small>{{ displayDate(row.submitted_at) }}</small></td>
              <td data-label="操作"><a :href="row.process_url">查看题目与过程</a></td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </Drawer>
</template>

<style scoped>
.kt-preview-search { margin-bottom: 1rem; }
.kt-preview-search > div { display: flex; gap: .75rem; margin-top: .5rem; }
.kt-preview-search input { min-width: 0; flex: 1; }
.kt-preview-drawer :deep(.kt-drawer) { width: min(76rem, 96vw); max-width: none; }
.kt-preview-answer { max-width: 18rem; white-space: pre-wrap; overflow-wrap: anywhere; }
.kt-preview-drawer small { display: block; margin-top: .3rem; }
@media (max-width: 42rem) {
  .kt-preview-drawer :deep(.kt-drawer) { width: 100vw; height: 100dvh; border-radius: 0; }
  .kt-preview-drawer .kt-table-scroll { overflow: visible; }
  .kt-preview-drawer table, .kt-preview-drawer tbody { display: block; min-width: 0; }
  .kt-preview-drawer thead { display: none; }
  .kt-preview-drawer tr { display: grid; grid-template-columns: 1fr 1fr; gap: .8rem; border-top: 1px solid var(--kt-border); padding: 1rem 0; }
  .kt-preview-drawer td { border: 0; display: block; min-width: 0; padding: 0; white-space: normal; }
  .kt-preview-drawer td::before { color: var(--kt-text-muted); content: attr(data-label); display: block; font-size: .75rem; margin-bottom: .3rem; }
  .kt-preview-drawer td[data-label="最终答案"], .kt-preview-drawer td[data-label="提交状态 / 时间"] { grid-column: 1 / -1; max-width: none; }
}
</style>
