<script setup lang="ts">
import { computed, defineAsyncComponent, inject, onMounted, ref, type Ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import Alert from '@/design-system/components/Alert.vue'
import EmptyState from '@/design-system/components/EmptyState.vue'
import MobileCardList from '@/design-system/components/MobileCardList.vue'
import PageHeader from '@/design-system/components/PageHeader.vue'
import Paginator from '@/design-system/components/Paginator.vue'
import SidebarSection from '@/design-system/components/SidebarSection.vue'
import StatusBadge from '@/design-system/components/StatusBadge.vue'
import type { components } from '@/api/generated/schema'
import DeletionDialog from '@/features/teacher-deletion/DeletionDialog.vue'
import type { DeletionTarget } from '@/features/teacher-deletion/api'

import ClassOverviewCharts from './ClassOverviewCharts.vue'

import { getTeacherClassApi, type TeacherClassCenter } from './api'

const TeacherClassManagement = defineAsyncComponent(() => import('@/features/teacher-account-management/TeacherClassManagement.vue'))

type Section = 'overview' | 'students' | 'experiments'

const route = useRoute()
const router = useRouter()
const api = getTeacherClassApi()
const courseId = Number(route.params.courseId)
const classId = Number(route.params.classId)
const course = inject<Ref<components['schemas']['CourseContext'] | undefined>>(
  'teacher-classes-course',
  ref(),
)
const center = ref<TeacherClassCenter>()
const loading = ref(true)
const error = ref('')
const search = ref(String(route.query.q || ''))
const sort = ref(String(route.query.sort || 'student_number'))
const status = ref(String(route.query.status || ''))
const selectedExperiment = ref(String(route.query.experiment_id || ''))
const deletionTarget = ref<DeletionTarget>()
const canManage = computed(() => course.value?.can_manage === true)
const activeSection = computed<Section>(() => {
  const value = String(route.query.section || 'overview')
  return value === 'students' || value === 'experiments' ? value : 'overview'
})

function statusLabel(value: string) {
  return {
    completed: '已完成',
    in_progress: '进行中',
    not_participated: '未参与',
  }[value] || value
}

function statusTone(value: string) {
  if (value === 'completed') return 'success' as const
  if (value === 'in_progress') return 'warning' as const
  return 'neutral' as const
}

async function load(overrides: { page?: number; experimentId?: number } = {}) {
  loading.value = true
  error.value = ''
  try {
    center.value = await api.center(courseId, classId, {
      section: activeSection.value,
      page: overrides.page ?? Number(route.query.page || 1),
      query: activeSection.value === 'students' ? search.value.trim() : undefined,
      sort: activeSection.value === 'students' ? sort.value : undefined,
      status: activeSection.value === 'students' ? status.value : undefined,
      experimentId: overrides.experimentId ?? (route.query.experiment_id ? Number(route.query.experiment_id) : undefined),
    })
    selectedExperiment.value = String(center.value.filters.experiment_id || '')
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '班级教学事实加载失败。'
  } finally {
    loading.value = false
  }
}

async function navigateSection(section: Section) {
  search.value = ''
  status.value = ''
  sort.value = 'student_number'
  await router.push({ query: section === 'overview' ? {} : { section } })
  await load({ page: 1 })
}

async function applyStudentFilters() {
  const query = {
    section: 'students',
    ...(search.value.trim() ? { q: search.value.trim() } : {}),
    ...(sort.value !== 'student_number' ? { sort: sort.value } : {}),
    ...(status.value ? { status: status.value } : {}),
  }
  await router.push({ query })
  await load({ page: 1 })
}

async function navigateStudents(page: number) {
  await router.push({ query: { ...route.query, ...(page > 1 ? { page: String(page) } : { page: undefined }) } })
  await load({ page })
}

async function selectExperiment() {
  const value = Number(selectedExperiment.value)
  await router.push({ query: value ? { section: 'experiments', experiment_id: String(value) } : { section: 'experiments' } })
  await load({ page: 1, experimentId: value || undefined })
}

async function afterDeletion(payload: { target: DeletionTarget }) {
  deletionTarget.value = undefined
  if (payload.target.kind === 'class') {
    await router.push(center.value?.actions.class_list_url || `/teacher/courses/${courseId}/classes/`)
    return
  }
  await load({ page: 1 })
}

async function afterBatchDeletion() {
  deletionTarget.value = undefined
  await load({ page: 1 })
}

onMounted(() => { if (route.query.section !== 'manage') void load() })
</script>

<template>
  <TeacherClassManagement v-if="route.query.section === 'manage'" />
  <template v-else>
    <PageHeader :title="center?.class_group.name || '班级中心'">
      <template #kicker>{{ center?.class_group.academic_year || '班级教学分析' }}</template>
      <p v-if="center">{{ center.course.name }} · {{ center.class_group.active_student_count }} 名当前学生</p>
      <template #actions>
        <a v-if="center" class="kt-button kt-button--secondary" :href="center.actions.visibility_url">学生端显示设置</a>
        <button v-if="center && canManage" class="kt-button kt-button--danger" data-delete-class type="button" @click="deletionTarget = { kind: 'class', classId }">删除班级</button>
      </template>
    </PageHeader>

    <SidebarSection v-if="center">
      <nav class="kt-local-tabs kt-sidebar-secondary-nav" aria-label="班级中心">
        <button type="button" :aria-current="activeSection === 'overview' ? 'page' : undefined" @click="navigateSection('overview')">概览</button>
        <button type="button" :aria-current="activeSection === 'students' ? 'page' : undefined" @click="navigateSection('students')">学生表现</button>
        <button type="button" :aria-current="activeSection === 'experiments' ? 'page' : undefined" @click="navigateSection('experiments')">实验分析</button>
        <a :href="center.actions.manage_url">班级管理</a>
      </nav>
    </SidebarSection>

    <Alert v-if="error" tone="danger">{{ error }}</Alert>
    <p v-if="loading" role="status">正在加载班级教学事实…</p>

    <template v-else-if="center && activeSection === 'overview'">
      <ClassOverviewCharts :center="center" />
    </template>

    <template v-else-if="center && activeSection === 'students'">
      <form class="kt-card kt-class-filter" @submit.prevent="applyStudentFilters">
        <label>搜索学生<input v-model="search" type="search" placeholder="姓名或学号"></label>
        <label>状态<select v-model="status"><option value="">全部</option><option value="completed">已完成</option><option value="in_progress">进行中</option><option value="not_participated">未参与</option></select></label>
        <label>排序<select v-model="sort"><option value="student_number">学号</option><option value="name">姓名</option><option value="-correct_rate">正确率从高到低</option><option value="-completion_rate">完成率从高到低</option><option value="average_time">平均用时</option></select></label>
        <button class="kt-button kt-button--secondary" type="submit">应用筛选</button>
      </form>
      <EmptyState v-if="!center.students.items.length" title="没有匹配的学生"><p>调整搜索或状态条件。</p></EmptyState>
      <section v-else class="kt-card kt-table-card" aria-labelledby="student-table-title">
        <div class="kt-section-heading"><h2 id="student-table-title">学生表现</h2><span>{{ center.students.page.total_items }} 人</span></div>
        <div class="kt-table-scroll kt-desktop-only"><table class="kt-table"><thead><tr><th>学生</th><th>完成实验</th><th>正确率</th><th>平均用时</th><th>状态</th></tr></thead><tbody><tr v-for="student in center.students.items" :key="student.student_id"><td><a :href="student.student_url"><strong>{{ student.display_name }}</strong><span class="kt-cell-note">{{ student.student_number }}</span></a></td><td>{{ student.completed_experiments }}/{{ student.eligible_experiments }}</td><td>{{ student.correct_rate.percent_display }}</td><td>{{ student.average_answer_time_display }}</td><td><StatusBadge :tone="statusTone(student.status)" :label="statusLabel(student.status)" /></td></tr></tbody></table></div>
        <MobileCardList class="kt-mobile-only"><li v-for="student in center.students.items" :key="student.student_id"><div class="kt-section-heading"><a :href="student.student_url"><strong>{{ student.display_name }}</strong></a><StatusBadge :tone="statusTone(student.status)" :label="statusLabel(student.status)" /></div><p>{{ student.student_number }} · 完成 {{ student.completed_experiments }}/{{ student.eligible_experiments }} · 正确率 {{ student.correct_rate.percent_display }}</p></li></MobileCardList>
        <Paginator :page="center.students.page.number" :total-pages="center.students.page.total_pages" @navigate="navigateStudents" />
      </section>
    </template>

    <template v-else-if="center && activeSection === 'experiments'">
      <section class="kt-card kt-experiment-selector" aria-labelledby="experiment-analysis-title">
        <div class="kt-section-heading"><h2 id="experiment-analysis-title">实验分析</h2><div v-if="canManage && center.filters.experiment_id" class="kt-class-row-actions"><a class="kt-text-button" :href="`/teacher/courses/${courseId}/experiments/${center.filters.experiment_id}/`">查看实验</a><button class="kt-text-button kt-text-button--danger" type="button" :data-remove-class-experiment="center.filters.experiment_id" @click="deletionTarget = { kind: 'class_experiment', classId, experimentId: center.filters.experiment_id! }">从本班移除</button><button class="kt-text-button kt-text-button--danger" type="button" :data-delete-global-experiment="center.filters.experiment_id" @click="deletionTarget = { kind: 'experiment', experimentId: center.filters.experiment_id! }">彻底删除实验</button></div></div>
        <label for="experiment-selector">选择实验</label>
        <select id="experiment-selector" v-model="selectedExperiment" @change="selectExperiment">
          <option value="">全部实验摘要</option>
          <option v-for="experiment in center.experiment_options" :key="experiment.experiment_id" :value="experiment.experiment_id">{{ experiment.name }}</option>
        </select>
      </section>
      <EmptyState v-if="!center.experiment_rows.length" title="尚无已发布实验"><p>发布班级实验后显示参与和评分事实。</p></EmptyState>
      <section v-else-if="!center.filters.experiment_id" class="kt-card kt-table-card"><div class="kt-table-scroll"><table class="kt-table"><thead><tr><th>实验</th><th>参与</th><th>完成</th><th>正确率</th><th v-if="canManage"><span class="kt-visually-hidden">操作</span></th></tr></thead><tbody><tr v-for="experiment in center.experiment_rows" :key="experiment.experiment_id"><td><a :href="experiment.analysis_url"><strong>{{ experiment.name }}</strong></a></td><td>{{ experiment.participated_count }}/{{ experiment.eligible_count }}</td><td>{{ experiment.completed_count }}</td><td>{{ experiment.correct_rate.percent_display }}</td><td v-if="canManage"><div class="kt-class-row-actions" :data-class-experiment-actions="experiment.experiment_id"><a :href="`/teacher/courses/${courseId}/experiments/${experiment.experiment_id}/`">查看实验</a><button class="kt-text-button kt-text-button--danger" type="button" :data-remove-class-experiment="experiment.experiment_id" @click="deletionTarget = { kind: 'class_experiment', classId, experimentId: experiment.experiment_id }">从本班移除</button><button class="kt-text-button kt-text-button--danger" type="button" :data-delete-global-experiment="experiment.experiment_id" @click="deletionTarget = { kind: 'experiment', experimentId: experiment.experiment_id }">彻底删除实验</button></div></td></tr></tbody></table></div></section>
      <template v-else>
        <section class="kt-card kt-table-card" aria-labelledby="participant-title"><div class="kt-section-heading"><h2 id="participant-title">参与学生</h2></div><EmptyState v-if="!center.experiment_participants.length" title="暂无参与者"><p>当前实验没有可显示的参与事实。</p></EmptyState><div v-else class="kt-table-scroll"><table class="kt-table"><thead><tr><th>学生</th><th>参与</th><th>完成</th><th>正确率</th><th>得分率</th></tr></thead><tbody><tr v-for="student in center.experiment_participants" :key="student.student_id"><td><a :href="student.student_url">{{ student.display_name }}</a><span class="kt-cell-note">{{ student.student_number }}</span></td><td>{{ student.participated ? '是' : '否' }}</td><td>{{ student.completed ? '是' : '否' }}</td><td>{{ student.correct_rate.percent_display }}</td><td>{{ student.score_rate.percent_display }}</td></tr></tbody></table></div></section>
        <section class="kt-card kt-table-card" aria-labelledby="question-title"><div class="kt-section-heading"><h2 id="question-title">题目表现</h2></div><EmptyState v-if="!center.question_rows.length" title="暂无题目统计"><p>学生提交并形成评分后显示。</p></EmptyState><div v-else class="kt-table-scroll"><table class="kt-table"><thead><tr><th>题目</th><th>作答</th><th>正确</th><th>错误</th><th>正确率</th><th>平均用时</th></tr></thead><tbody><tr v-for="question in center.question_rows" :key="question.question_snapshot_id"><td><a :href="question.detail_url"><strong>{{ question.code }}</strong><span class="kt-cell-note">{{ question.text }}</span></a></td><td>{{ question.answer_count }}</td><td>{{ question.correct_count }}</td><td>{{ question.incorrect_count }}</td><td>{{ question.correct_rate.percent_display }}</td><td>{{ question.average_answer_time_display }}</td></tr></tbody></table></div></section>
      </template>
    </template>
    <DeletionDialog v-if="deletionTarget" :course-id="courseId" :target="deletionTarget" @cancelled="deletionTarget = undefined" @completed="afterDeletion" @batch-completed="afterBatchDeletion" />
  </template>
</template>
