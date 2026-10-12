<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import { getBootstrapContext } from '@/app/bootstrap'
import WorkspaceShell from '@/design-system/components/WorkspaceShell.vue'

import type { TeacherCourseDetailResponse } from './api'
import { getTeacherCourseApi } from './api'
import './teacher-courses.css'

const bootstrap = getBootstrapContext()
const route = useRoute()
const courseId = Number(route.params.courseId)
const data = ref<TeacherCourseDetailResponse>()
const error = ref('')

function formatDate(value?: string) {
  if (!value) return '未记录'
  const date = new Date(value)
  return Number.isNaN(date.valueOf()) ? '未记录' : date.toLocaleString('zh-CN')
}

onMounted(async () => {
  try {
    data.value = await getTeacherCourseApi().detail(courseId)
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '课程概览加载失败。'
  }
})
</script>

<template>
  <div class="kt-teacher-courses">
    <WorkspaceShell
      :user-name="bootstrap.user.display_name"
      :user-meta="bootstrap.user.username"
      :csrf-token="bootstrap.csrf_token"
      :course-name="data?.course.name || '课程'"
      :course-meta="data ? `${data.course.code} · ${data.course.term}` : undefined"
      main-id="teacher-course-overview-main"
    >
      <template #navigation>
        <a href="/teacher/courses/">我的课程</a>
        <RouterLink :to="`/teacher/courses/${courseId}/`" aria-current="page">概览</RouterLink>
        <a :href="data?.course.banks_url || `/teacher/courses/${courseId}/banks/`">题库</a>
        <a :href="data?.course.classes_url || `/teacher/courses/${courseId}/classes/`">班级</a>
        <a :href="data?.course.experiments_url || `/teacher/courses/${courseId}/experiments/`">实验</a>
        <a :href="data?.course.data_url || `/teacher/courses/${courseId}/data/`">数据</a>
        <a :href="data?.course.settings_url || `/teacher/courses/${courseId}/settings/`">设置</a>
      </template>
      <p v-if="!data && !error" role="status">正在加载课程概览…</p>
      <p v-else-if="error" class="kt-alert kt-alert--danger" role="alert">{{ error }}</p>
      <template v-else-if="data">
        <header class="kt-teacher-course-overview-header">
          <div><p>{{ data.course.code }} · {{ data.course.term }}</p><h1>{{ data.course.name }}</h1><span>{{ data.course.description || '未填写课程说明' }}</span><small>负责人：{{ data.course.owner_name }} · 更新：{{ formatDate(data.course.updated_at) }}</small></div>
          <div class="kt-teacher-course-overview-header__actions"><a class="kt-button" :href="data.course.data_url">数据中心</a><a v-if="data.class_center_enabled" class="kt-button kt-button--secondary" :href="data.course.classes_url">班级教学分析</a><a v-if="data.is_course_owner" data-course-settings-action class="kt-button kt-button--secondary" :href="data.course.settings_url">课程设置</a></div>
        </header>
        <p v-if="data.course.status === 'archived'" class="kt-alert kt-alert--warning">课程已归档。课程资源和历史数据保持可读，但不能继续修改或开始新实验。</p>
        <section aria-labelledby="teacher-course-metrics"><h2 id="teacher-course-metrics">课程概况</h2><div class="kt-teacher-course-metrics" aria-label="课程资源概况"><article class="kt-card"><span>题库数量</span><strong>{{ data.metrics.bank_count }}</strong></article><article class="kt-card"><span>班级数量</span><strong>{{ data.metrics.class_count }}</strong></article><article class="kt-card"><span>课程学生</span><strong>{{ data.metrics.student_count }}</strong></article><article class="kt-card"><span>实验数量</span><strong>{{ data.metrics.experiment_count }}</strong></article></div></section>
        <div class="kt-teacher-course-overview-grid">
          <section><div class="kt-section-heading"><h2>主要工作</h2></div><div class="kt-teacher-course-workflow"><a class="kt-card" :href="data.course.banks_url"><strong>题库与 KC 映射</strong><span>创建、复制、导入并编辑课程题目与知识点关联。</span></a><a class="kt-card" :href="data.course.classes_url"><strong>{{ data.class_center_enabled ? '班级教学分析' : '班级与学生' }}</strong><span>维护班级、选课关系与学生名单。</span></a><a class="kt-card" :href="data.course.experiments_url"><strong>实验配置</strong><span>配置发布快照与学生分配。</span></a><a class="kt-card" :href="data.course.data_url"><strong>数据中心</strong><span>查看采集质量并导出研究数据。</span></a></div></section>
          <section><div class="kt-section-heading"><h2>近期实验</h2><a :href="data.course.experiments_url">查看全部</a></div><div class="kt-card kt-teacher-course-resource-list"><a v-for="experiment in data.recent_experiments" :key="experiment.id" :href="experiment.detail_url"><span><strong>{{ experiment.name }}</strong><small>{{ experiment.question_bank_name }}</small></span><span class="kt-status kt-status--neutral">{{ experiment.status_label }}</span></a><p v-if="!data.recent_experiments.length">尚无实验。题库准备完成后可创建第一个实验。</p></div></section>
        </div>
        <section><div class="kt-section-heading"><h2>最近题库</h2><a :href="data.course.banks_url">管理题库</a></div><div class="kt-card kt-teacher-course-table"><div v-if="data.recent_banks.length" class="kt-table-scroll"><table><thead><tr><th>名称</th><th>题目数</th><th>可见性</th><th>最近更新</th></tr></thead><tbody><tr v-for="bank in data.recent_banks" :key="bank.id"><td><a :href="bank.detail_url"><strong>{{ bank.name }}</strong><small>{{ bank.description || '未填写说明' }}</small></a></td><td>{{ bank.question_count }}</td><td>{{ bank.visibility_label }}</td><td>{{ formatDate(bank.updated_at) }}</td></tr></tbody></table></div><p v-else>尚未创建题库。进入题库页面创建或复制课程题库。</p></div></section>
      </template>
    </WorkspaceShell>
  </div>
</template>
