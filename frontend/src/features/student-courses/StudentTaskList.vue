<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import type { StudentTask, StudentTaskCollection } from './api'
import { getStudentCourseApi } from './api'

const route = useRoute()
const router = useRouter()
const courseId = Number(route.params.courseId)
const response = ref<StudentTaskCollection>()
const loading = ref(true)
const error = ref('')
const pendingTaskId = ref<number>()

function actionLabel(action: string | null) {
  return { start: '开始实验', resume: '继续作答', view_result: '查看结果' }[action || ''] || '查看任务'
}

function statusLabel(status: string) {
  return { scheduled: '未开始', open: '进行中', completed: '已完成', closed: '已结束', cancelled: '已取消' }[status] || status
}

async function runAction(task: StudentTask) {
  if (!task.primary_action) return
  if (task.primary_action === 'view_result') {
    await router.push(`/student/courses/${courseId}/learning/experiments/${task.experiment_id}/`)
    return
  }
  pendingTaskId.value = task.task_id
  error.value = ''
  try {
    const entry = task.primary_action === 'resume'
      ? await getStudentCourseApi().resume(task.task_id)
      : await getStudentCourseApi().start(task.task_id)
    globalThis.location.assign(entry.question_url)
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '任务操作失败。'
  } finally {
    pendingTaskId.value = undefined
  }
}

onMounted(async () => {
  try {
    response.value = await getStudentCourseApi().tasks(courseId)
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '任务加载失败。'
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <section>
    <div class="kt-page-header">
      <div class="kt-page-header__copy">
        <p class="kt-kicker">Assigned experiments</p>
        <h1>实验任务</h1>
        <p>任务状态与可执行操作由服务器按你的参与者身份和发布时间统一判定。</p>
      </div>
    </div>
    <p v-if="error" class="kt-alert kt-alert--danger" role="alert">{{ error }}</p>
    <p v-if="loading" role="status">正在加载任务…</p>
    <div v-else-if="response?.items.length" class="kt-student-task-list">
      <article v-for="task in response.items" :key="task.task_id" class="kt-card kt-student-task-card">
        <div class="kt-student-task-card__heading">
          <div>
            <span class="kt-status kt-status--neutral">{{ statusLabel(task.business_status) }}</span>
            <h2>{{ task.experiment_name }}</h2>
            <p>{{ task.class_name }}</p>
          </div>
          <dl class="kt-student-progress">
            <div><dt>作答进度</dt><dd>{{ task.progress.completed }} / {{ task.progress.total }}</dd></div>
          </dl>
        </div>
        <div class="kt-student-task-card__footer">
          <span v-if="task.closes_at" class="kt-muted">截止：{{ new Date(task.closes_at).toLocaleString('zh-CN') }}</span>
          <span v-else class="kt-muted">以任务页面显示的服务器判定为准</span>
          <button v-if="task.primary_action" class="kt-button" type="button" :data-task-action="task.primary_action" :disabled="pendingTaskId === task.task_id" @click="runAction(task)">
            {{ pendingTaskId === task.task_id ? '处理中…' : actionLabel(task.primary_action) }}
          </button>
        </div>
      </article>
    </div>
    <div v-else-if="!loading" class="kt-card kt-empty-state">
      <h2>暂无实验任务</h2><p>教师正式发布且包含你的任务会显示在这里。</p>
    </div>
  </section>
</template>
