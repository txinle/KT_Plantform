<script setup lang="ts">
import { useExperimentPageUrl } from './experiment-page-url'
const pageUrl = useExperimentPageUrl()
import { computed, reactive, ref, useId } from 'vue'
import { classTaskStatusLabel } from './class-task-labels'
import Alert from '@/design-system/components/Alert.vue'
import Button from '@/design-system/components/Button.vue'
import ModalDialog from '@/design-system/components/ModalDialog.vue'
import StatusBadge from '@/design-system/components/StatusBadge.vue'
import { getTeacherExperimentApi, type ClassTask, type ClassTaskProgress } from './api'
const props = defineProps<{ courseId: number; experimentId: number; tasks: ClassTask[]; progress: Record<number, ClassTaskProgress> }>()
const emit = defineEmits<{ changed: [] }>()
const api = getTeacherExperimentApi()
const working = ref(false)
const error = ref('')
const menuPrefix = `class-task-${useId()}`
const scheduleTaskId = ref<number>()
const selectedScheduleTask = computed(() => props.tasks.find(task => task.id === scheduleTaskId.value))
const scheduleForm = reactive({
  opens_at: '', closes_at: '', reason: '', total_minutes: '',
  total_extension_minutes: '',
})
const confirmation = reactive({ taskId: 0, action: '' as '' | 'cancel' | 'manual_close', reason: '' })

function displayDate(value: string | null | undefined) {
  return value ? new Date(value).toLocaleString('zh-CN') : '未设置'
}
function positionTaskMenu(event: MouseEvent, taskId: number) {
  const menu = document.getElementById(`${menuPrefix}-${taskId}`)
  if (!menu) return
  const bounds = (event.currentTarget as HTMLElement).getBoundingClientRect()
  menu.style.left = `${Math.max(8, Math.min(bounds.left, window.innerWidth - 206))}px`
  menu.style.top = `${Math.max(8, Math.min(bounds.bottom + 6, window.innerHeight - 300))}px`
}
function focusTaskMenu(taskId: number) {
  document.getElementById(`${menuPrefix}-${taskId}`)?.hidePopover?.()
  document.getElementById(`${menuPrefix}-trigger-${taskId}`)?.focus()
}
function dateTimeLocal(value: string | null) {
  if (!value) return ''
  const date = new Date(value)
  const offset = date.getTimezoneOffset() * 60_000
  return new Date(date.getTime() - offset).toISOString().slice(0, 16)
}

function isoOrNull(value: string) {
  return value ? new Date(value).toISOString() : null
}

function editSchedule(task: ClassTask) {
  focusTaskMenu(task.id)
  error.value = ''
  scheduleTaskId.value = task.id
  scheduleForm.opens_at = dateTimeLocal(task.opens_at)
  scheduleForm.closes_at = dateTimeLocal(task.closes_at)
  scheduleForm.reason = ''
  scheduleForm.total_minutes = task.total_time_limit_minutes == null ? '' : String(task.total_time_limit_minutes)
  scheduleForm.total_extension_minutes = ''
}

function timingInput(task: ClassTask) {
  const beforeStart = task.available_actions.includes('revise_schedule')
  return {
    expected_revision: task.schedule_revision,
    reopen: task.business_status === 'manual_closed' || task.business_status === 'closed',
    reason: scheduleForm.reason.trim(),
    ...(task.total_time_limit_minutes == null ? {} : beforeStart
      ? { total_time_limit_minutes: Number(scheduleForm.total_minutes) }
      : scheduleForm.total_extension_minutes
        ? { total_time_limit_minutes: task.total_time_limit_minutes + Number(scheduleForm.total_extension_minutes) }
        : {}),
  }
}

async function reviseSchedule(task: ClassTask) {
  const beforeStart = task.available_actions.includes('revise_schedule')
  const opensChanged = beforeStart && scheduleForm.opens_at !== dateTimeLocal(task.opens_at)
  const cutoffChanged = scheduleForm.closes_at !== dateTimeLocal(task.closes_at)
  const closesAt = cutoffChanged ? isoOrNull(scheduleForm.closes_at) : undefined
  working.value = true
  error.value = ''
  try {
    await api.reviseSchedule(task.id, {
      ...timingInput(task),
      ...(opensChanged ? { opens_at: isoOrNull(scheduleForm.opens_at) } : {}),
      ...(cutoffChanged ? { closes_at: closesAt } : {}),
    })
    scheduleTaskId.value = undefined
    emit('changed')
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '排期修订失败。'
  } finally {
    working.value = false
  }
}

function requestTerminalAction(taskId: number, action: 'cancel' | 'manual_close') {
  focusTaskMenu(taskId)
  error.value = ''
  confirmation.taskId = taskId
  confirmation.action = action
  confirmation.reason = ''
}

async function applyTerminalAction() {
  working.value = true
  try {
    if (confirmation.action === 'cancel') await api.cancel(confirmation.taskId, confirmation.reason)
    if (confirmation.action === 'manual_close') await api.manualClose(confirmation.taskId, confirmation.reason)
    confirmation.action = ''
    emit('changed')
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '任务状态更新失败。'
  } finally {
    working.value = false
  }
}

async function releaseFeedback(taskId: number) {
  working.value = true
  error.value = ''
  try {
    await api.releaseFeedback(taskId)
    emit('changed')
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '反馈发布失败。'
  } finally {
    working.value = false
  }
}

</script>

<template>
  <Alert v-if="error && !selectedScheduleTask && !confirmation.action" tone="danger">{{ error }}</Alert>
  <div class="kt-table-scroll">
    <table class="kt-table">
      <thead><tr><th>班级</th><th>状态</th><th>参与者</th><th>开始时间</th><th>截止时间</th><th>实验限时</th><th>未开始</th><th>进行中</th><th>已完成</th><th>操作</th></tr></thead>
      <tbody>
        <template v-for="task in tasks" :key="task.id">
          <tr>
            <td><strong>{{ task.class_name }}</strong><small class="kt-class-task-grading"><a v-if="task.available_actions.includes('grade_short_answers')" :href="pageUrl(`/teacher/courses/${courseId}/experiments/${experimentId}/class-tasks/${task.id}/grading/`)">待批改：{{ task.grading?.pending ?? 0 }}</a><span v-else>待批改：{{ task.grading?.pending ?? 0 }}</span></small></td><td><StatusBadge :label="classTaskStatusLabel(task.business_status)" tone="neutral" /></td>
            <td>{{ progress[task.id]?.total_participants ?? task.participant_count }}</td><td>{{ task.opens_at ? displayDate(task.opens_at) : '立即开放' }}</td><td>{{ task.closes_at ? displayDate(task.closes_at) : '无截止' }}</td><td><span v-if="task.total_time_limit_minutes != null">{{ task.total_time_limit_minutes }} 分钟</span><span v-else-if="task.per_question_time_limit_seconds != null">每题 {{ task.per_question_time_limit_seconds }} 秒</span><span v-else>不限时</span><span v-if="task.recoverable_count" class="kt-cell-note">可恢复 {{ task.recoverable_count }} 人</span></td>
            <td>{{ progress[task.id]?.not_started ?? '—' }}</td><td>{{ progress[task.id]?.in_progress ?? '—' }}</td><td>{{ progress[task.id]?.completed ?? '—' }}</td>
            <td>
              <button :id="`${menuPrefix}-trigger-${task.id}`" type="button" class="kt-button kt-button--secondary" :popovertarget="`${menuPrefix}-${task.id}`" :aria-label="`${task.class_name}操作`" @click="positionTaskMenu($event, task.id)">操作 ⌄</button>
              <div :id="`${menuPrefix}-${task.id}`" popover="auto" class="kt-experiment-row-menu">
                <a
                  v-if="task.available_actions.includes('grade_short_answers')"
                  data-action="grade-short-answers"
                  class="kt-text-button"
                  :href="pageUrl(`/teacher/courses/${courseId}/experiments/${experimentId}/class-tasks/${task.id}/grading/`)"
                >待批改：{{ task.grading?.pending ?? 0 }}</a>
                <button v-if="task.available_actions.includes('revise_schedule')" data-action="revise-schedule" class="kt-text-button" type="button" @click="editSchedule(task)">修订排期</button>
                <button v-else-if="task.available_actions.includes('adjust_time')" data-action="adjust-time" class="kt-text-button" type="button" @click="editSchedule(task)">调整时间</button>
                <button v-if="task.available_actions.includes('cancel')" data-action="cancel-task" class="kt-text-button kt-text-button--danger" type="button" @click="requestTerminalAction(task.id, 'cancel')">取消任务</button>
                <button v-if="task.available_actions.includes('manual_close')" data-action="manual-close" class="kt-text-button kt-text-button--danger" type="button" @click="requestTerminalAction(task.id, 'manual_close')">提前结束</button>
                <button v-if="task.available_actions.includes('release_feedback')" data-action="release-feedback" class="kt-text-button" type="button" :disabled="working" @click="releaseFeedback(task.id)">发布反馈</button>
              </div>
            </td>
          </tr>
        </template>
      </tbody>
    </table>
  </div>
  <Teleport to="body">
    <div class="kt-teacher-experiments">
      <ModalDialog :open="Boolean(selectedScheduleTask)" title="调整班级时间" @close="!working && (scheduleTaskId = undefined)">
        <form v-if="selectedScheduleTask" :id="`${menuPrefix}-schedule`" class="kt-time-adjust-panel" @submit.prevent="reviseSchedule(selectedScheduleTask)">
          <h3>调整该班时间：{{ selectedScheduleTask.class_name }} · 第 {{ selectedScheduleTask.schedule_revision }} 版</h3>
          <p v-if="selectedScheduleTask.business_status === 'manual_closed' || selectedScheduleTask.business_status === 'closed'">保存后重新开放；已提交答案不会解锁。</p>
          <div class="kt-inline-form-grid">
            <label v-if="selectedScheduleTask.available_actions.includes('revise_schedule')">开放时间<input v-model="scheduleForm.opens_at" type="datetime-local"></label>
            <label>{{ selectedScheduleTask.available_actions.includes('revise_schedule') ? '本班实验截止时间（调整后）' : selectedScheduleTask.closes_at ? `延长本班实验截止时间（当前：${displayDate(selectedScheduleTask.closes_at)}）` : '设置本班实验截止时间' }}<input v-model="scheduleForm.closes_at" data-action="class-cutoff" type="datetime-local" :required="selectedScheduleTask.business_status === 'closed' || selectedScheduleTask.business_status === 'manual_closed' || (selectedScheduleTask.total_time_limit_minutes == null && selectedScheduleTask.per_question_time_limit_seconds == null)"></label>
            <label v-if="selectedScheduleTask.total_time_limit_minutes != null && selectedScheduleTask.available_actions.includes('revise_schedule')">学生作答总时长（调整后，当前：{{ selectedScheduleTask.total_time_limit_minutes }} 分钟）<input v-model="scheduleForm.total_minutes" data-action="total-time-limit" type="number" min="1" step="1" required></label>
            <label v-else-if="selectedScheduleTask.total_time_limit_minutes != null">学生作答时间延长（当前时长：{{ selectedScheduleTask.total_time_limit_minutes }} 分钟）<input v-model="scheduleForm.total_extension_minutes" data-action="total-time-extension" type="number" min="1" step="1" placeholder="填写增加的分钟数"><small v-if="scheduleForm.total_extension_minutes">调整后：{{ selectedScheduleTask.total_time_limit_minutes + Number(scheduleForm.total_extension_minutes) }} 分钟</small></label>
            <p v-if="selectedScheduleTask.per_question_time_limit_seconds != null">历史单题时限：{{ selectedScheduleTask.per_question_time_limit_seconds }} 秒（只读）。</p>
            <label>调整原因<input v-model="scheduleForm.reason" maxlength="500" required></label>
          </div>
          <p v-if="selectedScheduleTask.total_time_limit_minutes != null || selectedScheduleTask.per_question_time_limit_seconds != null">实际可答至个人时限与本班截止时间中较早者。</p>
          <Alert v-if="error" tone="danger">{{ error }}</Alert>
        </form>
        <template #footer><button type="button" class="kt-button kt-button--secondary" :disabled="working" @click="scheduleTaskId = undefined">取消</button><Button type="submit" :form="`${menuPrefix}-schedule`" :disabled="working">保存调整</Button></template>
      </ModalDialog>
      <ModalDialog :open="Boolean(confirmation.action)" :title="confirmation.action === 'cancel' ? '确认取消该班任务' : '确认提前结束该班任务'" @close="!working && (confirmation.action = '')">
        <form :id="`${menuPrefix}-terminal`" class="kt-time-adjust-panel" @submit.prevent="applyTerminalAction">
          <p>{{ tasks.find(task => task.id === confirmation.taskId)?.class_name }}</p><p>此操作会立即改变学生端可用状态，请填写可审计原因后确认。</p>
          <label>原因<input v-model="confirmation.reason" maxlength="500" required></label>
          <Alert v-if="error" tone="danger">{{ error }}</Alert>
        </form>
        <template #footer><button type="button" class="kt-button kt-button--secondary" :disabled="working" @click="confirmation.action = ''">返回</button><button class="kt-button kt-button--danger" type="submit" :form="`${menuPrefix}-terminal`" :disabled="working">确认执行</button></template>
      </ModalDialog>
    </div>
  </Teleport>
</template>
