<script setup lang="ts">
import { computed, inject, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { beginAssessmentOperation } from '@/assessment-runtime/operation-timing'
import { markPreparedStartupPhase } from '@/assessment-runtime/prepared-paper'
import { prepareOfflineCommonResources } from '@/assessment-runtime/offline-cache'
import { getSessionController } from '@/assessment-runtime/session-controller'
import { getBootstrapContext } from '@/app/bootstrap'
import type { StudentTask } from './api'
import { getStudentCourseApi } from './api'
import { studentTaskContext } from './task-context'
import Icon from '@/design-system/Icon.vue'
import StatusBadge from '@/design-system/components/StatusBadge.vue'

const route = useRoute()
const router = useRouter()
const courseId = computed(() => Number(route.params.courseId))
const tasks = inject(studentTaskContext)
if (!tasks) throw new Error('StudentTaskList requires its course layout.')
const { response, loading, reload } = tasks
const error = ref('')
const pendingTaskId = ref<number>()
const search = ref('')
const status = ref('all')
const visibleTasks = computed(() => (response.value?.items || []).filter(task =>
  (status.value === 'all' || task.business_status === status.value)
  && task.experiment_name.toLocaleLowerCase().includes(search.value.trim().toLocaleLowerCase()),
))
function statusTone(value: string) {
  if (value === 'open' || value === 'completed') return 'success' as const
  if (value === 'scheduled') return 'warning' as const
  return 'neutral' as const
}
function displayDate(value: string) {
  return new Date(value).toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

function actionLabel(action: string | null) {
  return { start: '开始作业', resume: '继续作答', view_result: '查看作答详情' }[action || ''] || '查看作业'
}

function statusLabel(status: string) {
  return { scheduled: '未开始', open: '进行中', completed: '已完成', closed: '已结束', cancelled: '已取消' }[status] || status
}

async function runAction(task: StudentTask) {
  if (!task.primary_action) return
  if (task.primary_action === 'view_result') {
    await router.push(`/student/courses/${courseId.value}/learning/experiments/${task.experiment_id}/`)
    return
  }
  beginAssessmentOperation('entry')
  markPreparedStartupPhase('click')
  pendingTaskId.value = task.task_id
  error.value = ''
  try {
    const entry = task.primary_action === 'resume'
      ? await getStudentCourseApi().resume(task.task_id)
      : await getStudentCourseApi().start(task.task_id)
    if (entry.runtime) {
      const controller = await getSessionController(entry.session_id, getBootstrapContext().csrf_token)
      await controller.primeStart(entry.runtime)
    }
    await router.push(entry.question_url)
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '任务操作失败。'
  } finally {
    pendingTaskId.value = undefined
  }
}

onMounted(() => {
  void prepareOfflineCommonResources().catch(() => undefined)
})
</script>

<template>
  <section class="kt-student-tasks-page">
    <header class="kt-student-page-heading kt-student-page-heading--split">
      <div>
        <p class="kt-student-page-heading__eyebrow">{{ response?.course.course_code || '课程' }}</p>
        <div class="kt-student-task-title"><h1>作业</h1><span v-if="response" aria-live="polite">{{ visibleTasks.length }} 个作业</span></div>
        <p>{{ response?.course.course_name || '正在读取课程作业。' }}</p>
      </div>
      <p v-if="response?.course.class_name" class="kt-student-page-heading__context">{{ response.course.class_name }}</p>
    </header>
    <div class="kt-student-task-toolbar">
      <div class="kt-student-task-filters">
        <input v-model="search" type="search" aria-label="搜索作业名称" placeholder="搜索作业名称">
        <select v-model="status" aria-label="按作业状态筛选"><option value="all">全部状态</option><option value="scheduled">未开始</option><option value="open">进行中</option><option value="completed">已完成</option><option value="closed">已结束</option><option value="cancelled">已取消</option></select>
      </div>
      <button class="kt-button kt-button--secondary" type="button" data-refresh-tasks :disabled="loading" @click="reload">刷新</button>
    </div>
    <p v-if="error" class="kt-alert kt-alert--danger" role="alert">{{ error }}</p>
    <p v-if="loading" role="status">正在加载任务…</p>
    <div v-else-if="visibleTasks.length" class="kt-student-task-list">
      <article v-for="task in visibleTasks" :key="task.task_id" class="kt-card kt-student-task-row" :aria-label="task.experiment_name">
        <div class="kt-student-task-card-head">
          <h2 :title="task.experiment_name">{{ task.experiment_name }}</h2>
          <StatusBadge :tone="statusTone(task.business_status)" :label="statusLabel(task.business_status)" />
        </div>
        <div class="kt-student-task-card-details">
          <p class="kt-student-task-card-class"><Icon name="classes" :size="17" />{{ task.class_name }}</p>
          <p v-if="task.opens_at">开始：<time :datetime="task.opens_at">{{ displayDate(task.opens_at) }}</time></p>
          <p v-if="task.closes_at">截止：<time :datetime="task.closes_at">{{ displayDate(task.closes_at) }}</time></p>
          <p v-else>未设置截止时间</p>
        </div>
        <div class="kt-student-task-card-foot">
          <p>已完成 <strong>{{ task.progress.completed }} / {{ task.progress.total }}</strong></p>
          <button v-if="task.primary_action" class="kt-button" type="button" :data-task-action="task.primary_action" :disabled="pendingTaskId === task.task_id" @click="runAction(task)">
            {{ pendingTaskId === task.task_id ? '处理中…' : actionLabel(task.primary_action) }}
          </button>
        </div>
      </article>
    </div>
    <div v-else-if="!loading" class="kt-card kt-empty-state">
      <template v-if="response?.items.length"><h2>没有匹配的作业</h2><p>试试其他名称或状态。</p><button class="kt-button kt-button--secondary" type="button" @click="search = ''; status = 'all'">清除筛选</button></template>
      <template v-else><h2>暂无实验任务</h2><p>教师正式发布且包含你的任务会显示在这里。</p></template>
    </div>
  </section>
</template>
