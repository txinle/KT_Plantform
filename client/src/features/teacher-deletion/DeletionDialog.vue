<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'

import { ApiError } from '@/api/client'

import {
  getTeacherDeletionApi,
  type DeletionAction,
  type DeletionPreview,
  type DeletionResult,
  type DeletionTarget,
} from './api'

const props = defineProps<{ courseId: number; target: DeletionTarget }>()
const emit = defineEmits<{
  cancelled: []
  completed: [payload: { target: DeletionTarget; result: DeletionResult }]
  'batch-completed': []
}>()

const api = getTeacherDeletionApi()
const dialog = ref<HTMLDialogElement>()
const activeTarget = ref<DeletionTarget>(props.target)
const preview = ref<DeletionPreview>()
const loading = ref(true)
const submitting = ref(false)
const cleanupPending = ref(false)
const acceptedCleanupId = ref('')
const stale = ref(false)
const error = ref('')
const remainingBatch = ref<DeletionTarget[]>([])
const batchMode = ref(false)
let cleanupTimer: ReturnType<typeof setTimeout> | undefined

const expectedAction = computed<DeletionAction>(() => {
  if (activeTarget.value.kind === 'experiment') return 'delete_experiment'
  if (activeTarget.value.kind === 'class_experiment') return 'remove_class_experiment'
  return 'delete_class'
})
const title = computed(() => {
  const name = preview.value?.object_name || '所选对象'
  if (activeTarget.value.kind === 'experiment') return `删除实验“${name}”`
  if (activeTarget.value.kind === 'class_experiment') return `从当前班级移除实验“${name}”`
  return `删除班级“${name}”`
})
const canConfirm = computed(() => Boolean(
  preview.value?.allowed
  && preview.value.confirmation_token
  && !loading.value
  && !submitting.value
  && !cleanupPending.value
  && !acceptedCleanupId.value
  && !stale.value,
))
const confirmLabel = computed(() => {
  if (submitting.value) return '正在提交…'
  if (cleanupPending.value) return '正在完成后台清理…'
  if (activeTarget.value.kind === 'class_experiment') return '确认仅从当前班级移除'
  if (activeTarget.value.kind === 'experiment') return '确认删除全部班级中的实验'
  return '确认删除班级'
})
const actionDescription = computed(() => {
  if (activeTarget.value.kind === 'experiment') return '将从所有班级删除该实验及其关联实验数据；其他实验的原始数据保留。'
  if (activeTarget.value.kind === 'class_experiment') return '仅从当前班级移除该实验及当前班级的关联实验数据；其他班级中的实验保持不变。'
  return '只删除当前班级；其他班级、课程账号和无关研究数据保持不变。'
})

function caughtMessage(caught: unknown, fallback: string) {
  return caught instanceof Error ? caught.message : fallback
}

async function openDialog() {
  await nextTick()
  if (!dialog.value || dialog.value.open) return
  if (typeof dialog.value.showModal === 'function') dialog.value.showModal()
  else dialog.value.setAttribute('open', '')
}

function requestPreview(target: DeletionTarget) {
  if (target.kind === 'experiment') return api.previewExperiment(props.courseId, target.experimentId)
  if (target.kind === 'class_experiment') return api.previewClassExperiment(props.courseId, target.classId, target.experimentId)
  return api.previewClass(props.courseId, target.classId)
}

async function loadPreview(target = activeTarget.value) {
  activeTarget.value = target
  loading.value = true
  stale.value = false
  acceptedCleanupId.value = ''
  error.value = ''
  preview.value = undefined
  await openDialog()
  try {
    const result = await requestPreview(target)
    if (result.action !== expectedAction.value) {
      error.value = '服务器返回的删除范围与当前操作不一致，请关闭后重试。'
      return
    }
    preview.value = result
    if (result.allowed && !result.confirmation_token) {
      error.value = '服务器未返回确认令牌，无法提交删除。请刷新预览后重试。'
    }
  } catch (caught) {
    error.value = caughtMessage(caught, '删除影响预览加载失败，请重试。')
  } finally {
    loading.value = false
  }
}

function requestDeletion(target: DeletionTarget, confirmationToken: string) {
  if (target.kind === 'experiment') return api.deleteExperiment(props.courseId, target.experimentId, confirmationToken)
  if (target.kind === 'class_experiment') return api.removeClassExperiment(props.courseId, target.classId, target.experimentId, confirmationToken)
  return api.deleteClass(props.courseId, target.classId, confirmationToken)
}

function closeDialog() {
  if (cleanupTimer) clearTimeout(cleanupTimer)
  if (dialog.value?.open && typeof dialog.value.close === 'function') dialog.value.close()
}

function cancel() {
  if (submitting.value || cleanupPending.value) return
  closeDialog()
  emit('cancelled')
}

async function finish(result: DeletionResult) {
  const completedTarget = activeTarget.value
  const next = remainingBatch.value.shift()
  if (next) {
    await loadPreview(next)
    return
  }
  closeDialog()
  if (batchMode.value) {
    batchMode.value = false
    emit('batch-completed')
    return
  }
  emit('completed', { target: completedTarget, result })
}

async function pollCleanup(cleanupId: string, result: DeletionResult) {
  cleanupPending.value = true
  try {
    const status = await api.cleanup(props.courseId, cleanupId)
    if (status.error) {
      error.value = status.error
      cleanupPending.value = false
      return
    }
    if (!status.cleanup_pending && status.status === 'completed') {
      cleanupPending.value = false
      await finish(result)
      return
    }
    cleanupTimer = setTimeout(() => { void pollCleanup(cleanupId, result) }, 1500)
  } catch (caught) {
    error.value = caughtMessage(caught, '后台清理状态读取失败，请稍后重试。')
    cleanupPending.value = false
  }
}

async function confirmDeletion() {
  if (!canConfirm.value || !preview.value) return
  submitting.value = true
  error.value = ''
  try {
    const result = await requestDeletion(activeTarget.value, preview.value.confirmation_token)
    if (result.cleanup_pending) {
      if (!result.cleanup_id) {
        error.value = '服务器未返回后台清理编号，请刷新页面核对删除状态。'
        return
      }
      acceptedCleanupId.value = result.cleanup_id
      await pollCleanup(result.cleanup_id, result)
      return
    }
    await finish(result)
  } catch (caught) {
    if (caught instanceof ApiError && caught.status === 409) {
      stale.value = true
      error.value = '删除预览已过期，请刷新预览后再次确认。'
    } else {
      error.value = caughtMessage(caught, '删除未完成，请重试。')
    }
  } finally {
    submitting.value = false
  }
}

function openRelated(target: DeletionTarget) {
  batchMode.value = false
  remainingBatch.value = []
  void loadPreview(target)
}

function deleteAllClassExperiments() {
  if (activeTarget.value.kind !== 'class' || !preview.value?.experiments.length) return
  const targets = preview.value.experiments.map(experiment => ({
    kind: 'experiment' as const,
    experimentId: experiment.id,
  }))
  batchMode.value = true
  remainingBatch.value = targets.slice(1)
  void loadPreview(targets[0]!)
}

onMounted(() => { void loadPreview() })
onBeforeUnmount(() => { if (cleanupTimer) clearTimeout(cleanupTimer) })
</script>

<template>
  <dialog ref="dialog" class="kt-deletion-dialog" role="dialog" aria-modal="true" aria-labelledby="teacher-deletion-title" @cancel.prevent="cancel">
    <h2 id="teacher-deletion-title">{{ title }}</h2>
    <p v-if="loading" role="status">正在读取服务器删除影响…</p>
    <template v-else-if="preview">
      <p class="kt-deletion-warning">此操作不可撤销。请核对服务器列出的完整影响范围。</p>
      <p>{{ actionDescription }}</p>
      <p v-if="!preview.allowed" class="kt-deletion-blocked">{{ preview.blocked_reason }}</p>
      <dl class="kt-deletion-counts">
        <div><dt>关联班级</dt><dd>{{ preview.counts.class_count }}</dd></div>
        <div><dt>实验会话</dt><dd>{{ preview.counts.session_count }}</dd></div>
        <div><dt>导出记录</dt><dd>{{ preview.counts.export_count }}</dd></div>
        <div v-if="preview.action === 'delete_class'"><dt>班级成员</dt><dd>{{ preview.counts.member_count }}</dd></div>
      </dl>
      <section v-if="preview.experiments.length" aria-labelledby="teacher-deletion-impact-title">
        <h3 id="teacher-deletion-impact-title">受影响实验</h3>
        <ul class="kt-deletion-experiments">
          <li v-for="experiment in preview.experiments" :key="experiment.id">
            <div class="kt-deletion-experiment-heading"><strong>{{ experiment.name }}</strong><span>{{ experiment.export_count }} 个导出记录</span></div>
            <p v-if="preview.action === 'delete_class'">当前班级 {{ experiment.class_session_count }} 次会话</p>
            <ul v-if="experiment.classes.length" class="kt-deletion-classes">
              <li v-for="classGroup in experiment.classes" :key="classGroup.id"><span>{{ classGroup.name }}</span><span>{{ classGroup.session_count }} 次会话</span></li>
            </ul>
            <div v-if="preview.action === 'delete_class' && activeTarget.kind === 'class'" class="kt-deletion-related-actions">
              <a class="kt-button kt-button--secondary" :href="experiment.detail_url">查看实验</a>
              <button class="kt-button kt-button--secondary" type="button" :data-remove-class-experiment="experiment.id" @click="openRelated({ kind: 'class_experiment', classId: activeTarget.classId, experimentId: experiment.id })">仅从当前班级移除</button>
              <button class="kt-button kt-button--danger" type="button" :data-delete-global-experiment="experiment.id" @click="openRelated({ kind: 'experiment', experimentId: experiment.id })">删除全部班级中的实验</button>
            </div>
          </li>
        </ul>
      </section>
    </template>
    <p v-if="cleanupPending" role="status">删除已受理，正在等待后台清理完成…</p>
    <p v-if="error" class="kt-alert kt-alert--danger" role="alert">{{ error }}</p>
    <div class="kt-deletion-actions">
      <button v-if="stale" class="kt-button kt-button--secondary" data-deletion-refresh type="button" :disabled="loading" @click="loadPreview()">刷新删除预览</button>
      <button v-if="acceptedCleanupId && !cleanupPending" class="kt-button kt-button--secondary" data-cleanup-refresh type="button" @click="pollCleanup(acceptedCleanupId, { deleted: true, cleanup_pending: true, cleanup_id: acceptedCleanupId })">重新查询后台清理状态</button>
      <button v-if="preview?.action === 'delete_class' && !preview.allowed && preview.experiments.length" class="kt-button kt-button--danger" data-remove-all-class-experiments type="button" @click="deleteAllClassExperiments">删除该班级全部实验</button>
      <button class="kt-button kt-button--danger" data-deletion-confirm type="button" :disabled="!canConfirm" @click="confirmDeletion">{{ confirmLabel }}</button>
      <button class="kt-button kt-button--secondary" data-deletion-cancel type="button" :disabled="submitting || cleanupPending" @click="cancel">取消</button>
    </div>
  </dialog>
</template>

<style scoped>
.kt-deletion-dialog { width: min(calc(100% - 2rem), 46rem); max-height: min(46rem, calc(100vh - 2rem)); overflow: auto; border: 1px solid var(--kt-color-border); border-radius: var(--kt-radius-surface); padding: 1.35rem; color: var(--kt-color-text); background: var(--kt-color-surface); box-shadow: 0 1rem 3rem rgb(15 36 72 / 18%); }
.kt-deletion-dialog::backdrop { background: rgb(15 36 72 / 46%); }
.kt-deletion-dialog h2 { margin: 0 0 .75rem; }
.kt-deletion-dialog h3 { margin: 1rem 0 .5rem; font-size: 1rem; }
.kt-deletion-warning,
.kt-deletion-blocked { border-left: 3px solid var(--kt-color-danger); padding: .65rem .8rem; background: var(--kt-color-danger-bg); }
.kt-deletion-counts { display: grid; grid-template-columns: repeat(auto-fit, minmax(7rem, 1fr)); gap: .5rem; margin: 1rem 0; }
.kt-deletion-counts div { border: 1px solid var(--kt-color-border); border-radius: var(--kt-radius-control); padding: .65rem; }
.kt-deletion-counts dt { color: var(--kt-color-muted); font-size: .75rem; }
.kt-deletion-counts dd { margin: .2rem 0 0; font-weight: 750; font-variant-numeric: tabular-nums; }
.kt-deletion-experiments,
.kt-deletion-classes { margin: 0; padding: 0; list-style: none; }
.kt-deletion-experiments > li { border-top: 1px solid var(--kt-color-border); padding: .85rem 0; }
.kt-deletion-experiment-heading,
.kt-deletion-classes li { display: flex; justify-content: space-between; gap: 1rem; }
.kt-deletion-experiment-heading span,
.kt-deletion-experiments p,
.kt-deletion-classes { color: var(--kt-color-muted); font-size: .875rem; }
.kt-deletion-experiments p { margin: .35rem 0; }
.kt-deletion-classes { display: grid; gap: .25rem; }
.kt-deletion-related-actions,
.kt-deletion-actions { display: flex; flex-wrap: wrap; gap: .6rem; }
.kt-deletion-related-actions { margin-top: .75rem; }
.kt-deletion-actions { justify-content: flex-end; margin-top: 1rem; }
@media (max-width: 40rem) {
  .kt-deletion-counts { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .kt-deletion-related-actions .kt-button,
  .kt-deletion-actions .kt-button { width: 100%; }
}
</style>
