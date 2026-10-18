<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { ApiError } from '@/api/client'
import { bootstrapContext, getAuthenticationGeneration } from '@/app/bootstrap'

import {
  getTeacherDeletionApi,
  type DeletionAction,
  type DeletionOperationInput,
  type DeletionPreview,
  type DeletionResult,
  type DeletionTarget,
} from './api'

const props = defineProps<{ courseId: number; target: DeletionTarget; recoveryOnly?: boolean }>()
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
const executionMessage = ref('')
const stage = ref<'preview' | 'registering' | 'executing' | 'unknown' | 'deleted' | 'cleanup-failed' | 'complete'>('preview')
const pendingOperation = ref<SavedOperation>()
const registrationMissing = ref(false)
const checking = ref(false)
const remainingBatch = ref<DeletionTarget[]>([])
const batchMode = ref(false)
let pollTimer: ReturnType<typeof setTimeout> | undefined
let readController: AbortController | undefined
let generation = 0
let disposed = false
let pollingStopped = false
let operationAttempts = 0
let cleanupAttempts = 0

interface SavedOperation {
  input: DeletionOperationInput
  submitted: boolean
  result?: DeletionResult
}
interface Context {
  generation: number
  auth: number
  courseId: number
  target: DeletionTarget
  storageKey: string
}
let context: Context | undefined

function actionFor(target: DeletionTarget): DeletionAction {
  return target.kind === 'experiment' ? 'delete_experiment' : target.kind === 'class_experiment' ? 'remove_class_experiment' : 'delete_class'
}
const title = computed(() => {
  const name = preview.value?.object_name || '所选对象'
  if (activeTarget.value.kind === 'experiment') return `删除实验“${name}”`
  if (activeTarget.value.kind === 'class_experiment') return `从当前班级移除实验“${name}”`
  return `删除班级“${name}”`
})
const canConfirm = computed(() => Boolean(
  !props.recoveryOnly && preview.value?.allowed && preview.value.confirmation_token && !loading.value
  && !submitting.value && !pendingOperation.value && !stale.value && stage.value === 'preview',
))
const confirmLabel = computed(() => {
  if (stage.value === 'registering') return '正在登记操作…'
  if (submitting.value) return '正在提交…'
  if (stage.value === 'unknown') return '正在核实删除结果'
  if (stage.value === 'deleted' || stage.value === 'cleanup-failed') return '数据已删除'
  if (activeTarget.value.kind === 'class_experiment') return '确认仅从当前班级移除'
  if (activeTarget.value.kind === 'experiment') return '确认删除全部班级中的实验'
  return '确认删除班级'
})
const actionDescription = computed(() => {
  if (activeTarget.value.kind === 'experiment') return '将从所有班级删除该实验及其关联实验数据；其他实验的原始数据保留。'
  if (activeTarget.value.kind === 'class_experiment') return '仅从当前班级移除该实验及当前班级的关联实验数据；其他班级中的实验保持不变。'
  return '只删除当前班级；其他班级、课程账号和无关研究数据保持不变。'
})

function targetKey(target: DeletionTarget) {
  return target.kind === 'class' ? `class:${target.classId}` : target.kind === 'experiment'
    ? `experiment:${target.experimentId}` : `class_experiment:${target.classId}:${target.experimentId}`
}
function current(captured: Context) {
  return !disposed && captured.generation === context?.generation && captured.auth === getAuthenticationGeneration()
    && captured.courseId === props.courseId && targetKey(captured.target) === targetKey(activeTarget.value)
}
function stopReads() {
  if (pollTimer) clearTimeout(pollTimer)
  pollTimer = undefined
  readController?.abort()
  readController = undefined
  checking.value = false
}
function save(captured: Context, operation: SavedOperation) {
  sessionStorage.setItem(captured.storageKey, JSON.stringify(operation))
  pendingOperation.value = operation
  window.dispatchEvent(new Event('kt-teacher-deletion-change'))
}
function forget(captured: Context) {
  sessionStorage.removeItem(captured.storageKey)
  pendingOperation.value = undefined
  window.dispatchEvent(new Event('kt-teacher-deletion-change'))
}
function caughtMessage(caught: unknown, fallback: string) {
  return caught instanceof Error ? caught.message : fallback
}
function isAbort(caught: unknown) {
  return caught instanceof Error && caught.name === 'AbortError'
}
async function openDialog() {
  await nextTick()
  if (!dialog.value || dialog.value.open || disposed) return
  if (typeof dialog.value.showModal === 'function') dialog.value.showModal()
  else dialog.value.setAttribute('open', '')
}
function requestPreview(captured: Context, signal: AbortSignal) {
  const target = captured.target
  if (target.kind === 'experiment') return api.previewExperiment(captured.courseId, target.experimentId, signal)
  if (target.kind === 'class_experiment') return api.previewClassExperiment(captured.courseId, target.classId, target.experimentId, signal)
  return api.previewClass(captured.courseId, target.classId, signal)
}
async function loadPreview(target = activeTarget.value) {
  stopReads()
  activeTarget.value = { ...target }
  const actor = bootstrapContext.value?.user.id
  const captured: Context = { generation: ++generation, auth: getAuthenticationGeneration(), courseId: props.courseId,
    target: { ...target }, storageKey: `kt-teacher-deletion:v1:${actor}:${props.courseId}:${targetKey(target)}` }
  context = captured
  loading.value = true
  submitting.value = false
  stale.value = false
  error.value = ''
  executionMessage.value = ''
  preview.value = undefined
  acceptedCleanupId.value = ''
  cleanupPending.value = false
  pendingOperation.value = undefined
  registrationMissing.value = false
  stage.value = 'preview'
  operationAttempts = 0
  cleanupAttempts = 0
  pollingStopped = document.visibilityState !== 'visible'
  await openDialog()
  if (!current(captured) || !actor) { loading.value = false; return }
  try {
    const raw = sessionStorage.getItem(captured.storageKey)
    if (raw) {
      const saved = JSON.parse(raw) as SavedOperation
      if (saved.input?.operation_id && saved.input.action === actionFor(target)) {
        pendingOperation.value = saved
        stage.value = 'unknown'
        loading.value = false
        if (saved.result) await acceptResult(captured, saved.result)
        else await checkOperation(captured)
        return
      }
    }
    if (props.recoveryOnly) {
      error.value = '未找到可恢复的操作记录，请关闭后刷新页面。'
      return
    }
    const controller = new AbortController()
    readController = controller
    const result = await requestPreview(captured, controller.signal)
    if (!current(captured) || controller.signal.aborted) return
    const objectId = target.kind === 'class' ? target.classId : target.experimentId
    if (result.action !== actionFor(target) || result.object_id !== objectId) {
      error.value = '服务器返回的删除范围与当前操作不一致，请关闭后重试。'
      return
    }
    preview.value = result
    if (result.allowed && !result.confirmation_token) error.value = '服务器未返回确认令牌，无法提交删除。请刷新预览后重试。'
  } catch (caught) {
    if (current(captured) && !isAbort(caught)) error.value = caughtMessage(caught, '删除影响预览加载失败，请重试。')
  } finally {
    if (current(captured)) loading.value = false
  }
}
function requestDeletion(captured: Context, operation: SavedOperation) {
  const target = captured.target
  const input = operation.input
  if (target.kind === 'experiment') return api.deleteExperiment(captured.courseId, target.experimentId, input.confirmation_token, input.operation_id)
  if (target.kind === 'class_experiment') return api.removeClassExperiment(captured.courseId, target.classId, target.experimentId, input.confirmation_token, input.operation_id)
  return api.deleteClass(captured.courseId, target.classId, input.confirmation_token, input.operation_id)
}
function closeDialog() {
  stopReads()
  if (dialog.value?.open && typeof dialog.value.close === 'function') dialog.value.close()
}
function cancel() {
  if (submitting.value) return
  pollingStopped = true
  closeDialog()
  generation += 1
  context = undefined
  emit('cancelled')
}
async function finish(captured: Context, result: DeletionResult) {
  if (!current(captured)) return
  forget(captured)
  registrationMissing.value = false
  const next = remainingBatch.value.shift()
  if (next) { await loadPreview(next); return }
  stage.value = 'complete'
  closeDialog()
  if (batchMode.value) { batchMode.value = false; emit('batch-completed'); return }
  emit('completed', { target: captured.target, result })
}
function validResult(result: DeletionResult | null | undefined, operationId: string): result is DeletionResult {
  return !!result && result.deleted === true && typeof result.cleanup_pending === 'boolean'
    && result.operation_id === operationId && (!result.cleanup_pending || (typeof result.cleanup_id === 'string' && !!result.cleanup_id))
}
async function acceptResult(captured: Context, result: DeletionResult) {
  const operation = pendingOperation.value
  if (!current(captured) || !operation || !validResult(result, operation.input.operation_id)) throw new Error('服务器删除回执不完整，正在核实操作结果。')
  save(captured, { ...operation, result })
  error.value = ''
  stage.value = 'deleted'
  cleanupPending.value = result.cleanup_pending
  if (!result.cleanup_pending) { await finish(captured, result); return }
  acceptedCleanupId.value = result.cleanup_id!
  if (!pollingStopped) await pollCleanup(captured)
}
function scheduleCheck(captured: Context, kind: 'operation' | 'cleanup') {
  const attempts = kind === 'operation' ? operationAttempts : cleanupAttempts
  if (!current(captured) || pollingStopped || document.visibilityState !== 'visible' || attempts >= 3) return
  if (pollTimer) clearTimeout(pollTimer)
  pollTimer = setTimeout(() => {
    pollTimer = undefined
    if (kind === 'operation') void checkOperation(captured)
    else void pollCleanup(captured)
  }, 1500)
}
function rejection(captured: Context, code: string, message: string) {
  forget(captured)
  registrationMissing.value = false
  stage.value = 'preview'
  stale.value = ['DELETION_SCOPE_CHANGED', 'SCOPE_CHANGED', 'STALE_CONFIRMATION'].includes(code)
  error.value = stale.value ? '删除预览已过期，请刷新预览后再次确认。' : message || '操作未执行，请核对后重试。'
}
async function checkOperation(captured = context, manual = false) {
  if (!captured || !current(captured) || !pendingOperation.value || checking.value || document.visibilityState !== 'visible') return
  if (pollTimer) clearTimeout(pollTimer)
  pollTimer = undefined
  if (!manual) operationAttempts += 1
  checking.value = true
  const controller = new AbortController()
  readController = controller
  const id = pendingOperation.value.input.operation_id
  try {
    const operation = await api.operation(captured.courseId, id, controller.signal)
    if (!current(captured) || controller.signal.aborted) return
    if (operation.operation_id !== id || !['pending', 'succeeded', 'rejected'].includes(operation.state)) throw new Error('操作状态回执不完整，请继续核实。')
    registrationMissing.value = false
    if (operation.state === 'succeeded') {
      if (!validResult(operation.result, id)) throw new Error('服务器删除回执不完整，请继续核实。')
      checking.value = false
      await acceptResult(captured, operation.result)
      return
    }
    if (operation.state === 'rejected') { rejection(captured, operation.error_code, operation.message); return }
    if (!props.recoveryOnly && !pendingOperation.value?.submitted) {
      checking.value = false
      await submitRegistered(captured)
      return
    }
    error.value = executionMessage.value
  } catch (caught) {
    if (!current(captured) || controller.signal.aborted || isAbort(caught)) return
    registrationMissing.value = !props.recoveryOnly && caught instanceof ApiError && caught.status === 404 && !pendingOperation.value?.submitted
    error.value = [executionMessage.value, caughtMessage(caught, '操作结果尚未核实，请稍后查询。')].filter(Boolean).join(' ')
  } finally {
    if (current(captured) && readController === controller) {
      checking.value = false
      if (stage.value === 'unknown' && !manual) scheduleCheck(captured, 'operation')
    }
  }
}
async function pollCleanup(captured = context, manual = false) {
  const result = pendingOperation.value?.result
  if (!captured || !current(captured) || !result?.cleanup_id || checking.value || document.visibilityState !== 'visible') return
  if (pollTimer) clearTimeout(pollTimer)
  pollTimer = undefined
  if (!manual) cleanupAttempts += 1
  checking.value = true
  const controller = new AbortController()
  readController = controller
  try {
    const status = await api.cleanup(captured.courseId, result.cleanup_id, controller.signal)
    if (!current(captured) || controller.signal.aborted) return
    if (status.state === 'failed') {
      stage.value = 'cleanup-failed'
      error.value = status.error || '文件清理失败，请联系管理员核对清理记录。'
      return
    }
    if (!status.cleanup_pending && status.status === 'completed' && status.state === 'completed') {
      cleanupPending.value = false
      await finish(captured, { ...result, cleanup_pending: false })
      return
    }
    if (!['pending', 'running'].includes(status.state)) throw new Error('文件清理回执不完整，请重新查询。')
    stage.value = 'deleted'
    error.value = status.error || ''
  } catch (caught) {
    if (current(captured) && !controller.signal.aborted && !isAbort(caught)) error.value = caughtMessage(caught, '文件清理状态读取失败，请稍后查询。')
  } finally {
    if (current(captured) && readController === controller) {
      checking.value = false
      if (stage.value === 'deleted' && cleanupPending.value && !manual) scheduleCheck(captured, 'cleanup')
    }
  }
}
async function submitRegistered(captured: Context) {
  const operation = pendingOperation.value
  if (!operation || operation.submitted || !current(captured)) return
  save(captured, { ...operation, submitted: true })
  stage.value = 'executing'
  submitting.value = true
  error.value = ''
  try {
    const result = await requestDeletion(captured, operation)
    if (current(captured)) await acceptResult(captured, result)
  } catch (caught) {
    if (!current(captured)) return
    stage.value = 'unknown'
    executionMessage.value = caught instanceof ApiError ? caught.message : '删除结果待核实，正在查询持久操作回执。'
    error.value = executionMessage.value
    await checkOperation(captured)
  } finally { if (current(captured)) submitting.value = false }
}
async function admit(captured: Context) {
  const operation = pendingOperation.value
  if (!operation || !current(captured)) return
  stage.value = 'registering'
  try {
    const admitted = await api.registerOperation(captured.courseId, operation.input)
    if (!current(captured)) return
    registrationMissing.value = false
    if (admitted.operation_id !== operation.input.operation_id) throw new Error('登记回执与操作编号不一致。')
    if (admitted.state === 'succeeded') {
      if (!validResult(admitted.result, operation.input.operation_id)) throw new Error('登记回执缺少删除结果。')
      await acceptResult(captured, admitted.result)
    } else if (admitted.state === 'rejected') rejection(captured, admitted.error_code, admitted.message)
    else if (admitted.state === 'pending') await submitRegistered(captured)
    else throw new Error('登记回执状态不完整。')
  } catch (caught) {
    if (!current(captured)) return
    if (caught instanceof ApiError && caught.status < 500) rejection(captured, caught.code, caught.message)
    else {
      stage.value = 'unknown'
      error.value = '操作登记结果待核实，尚未重新提交删除。'
      await checkOperation(captured)
    }
  }
}
async function confirmDeletion() {
  const captured = context
  if (!captured || !current(captured) || !canConfirm.value || !preview.value) return
  submitting.value = true
  error.value = ''
  try {
    const target = captured.target
    save(captured, { input: { operation_id: crypto.randomUUID(), action: actionFor(target),
      object_id: target.kind === 'class' ? target.classId : target.experimentId,
      class_id: target.kind === 'class_experiment' ? target.classId : null,
      confirmation_token: preview.value.confirmation_token }, submitted: false })
    await admit(captured)
  } catch (caught) {
    if (current(captured)) error.value = caughtMessage(caught, '无法保存操作编号，未提交删除。')
  } finally { if (current(captured)) submitting.value = false }
}
async function retryAdmission() {
  const captured = context
  if (props.recoveryOnly || !captured || !registrationMissing.value || pendingOperation.value?.submitted || submitting.value) return
  submitting.value = true
  try { await admit(captured) }
  finally { if (current(captured)) submitting.value = false }
}
function openRelated(target: DeletionTarget) {
  if (pendingOperation.value || submitting.value) return
  batchMode.value = false
  remainingBatch.value = []
  void loadPreview(target)
}
function deleteAllClassExperiments() {
  if (pendingOperation.value || activeTarget.value.kind !== 'class' || !preview.value?.experiments.length) return
  const targets = preview.value.experiments.map(experiment => ({ kind: 'experiment' as const, experimentId: experiment.id }))
  batchMode.value = true
  remainingBatch.value = targets.slice(1)
  void loadPreview(targets[0]!)
}
function visibilityChanged() {
  if (document.visibilityState === 'visible') return
  pollingStopped = true
  stopReads()
}
watch(() => [props.courseId, targetKey(props.target), getAuthenticationGeneration()], () => {
  batchMode.value = false
  remainingBatch.value = []
  void loadPreview(props.target)
})
onMounted(() => { document.addEventListener('visibilitychange', visibilityChanged); void loadPreview() })
onBeforeUnmount(() => {
  disposed = true
  generation += 1
  stopReads()
  document.removeEventListener('visibilitychange', visibilityChanged)
})
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
        <div v-if="preview.counts.coverage_run_count"><dt>整体失效的 Coverage 运行</dt><dd>{{ preview.counts.coverage_run_count }}</dd></div>
        <div v-if="preview.counts.coverage_session_count"><dt>涉及 Coverage 会话</dt><dd>{{ preview.counts.coverage_session_count }}</dd></div>
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
    <p v-if="stage === 'unknown'" role="status">删除结果待核实。请查询操作回执；此时不会再次提交删除。</p>
    <p v-if="stage === 'deleted'" role="status">数据已删除，文件清理中。可稍后查询清理结果。</p>
    <p v-if="stage === 'cleanup-failed'" role="status">数据已删除，文件清理失败。请核对清理记录。</p>
    <p v-if="stage === 'complete'" role="status">数据删除及文件清理已完成。</p>
    <p v-if="error" class="kt-alert kt-alert--danger" role="alert">{{ error }}</p>
    <div class="kt-deletion-actions">
      <button v-if="stale" class="kt-button kt-button--secondary" data-deletion-refresh type="button" :disabled="loading" @click="loadPreview()">刷新删除预览</button>
      <button v-if="stage === 'unknown'" class="kt-button kt-button--secondary" data-operation-refresh type="button" :disabled="checking || submitting" @click="checkOperation(undefined, true)">查询删除操作结果</button>
      <button v-if="registrationMissing" class="kt-button kt-button--secondary" data-operation-register type="button" :disabled="submitting || checking" @click="retryAdmission">核实并重新登记同一操作</button>
      <button v-if="acceptedCleanupId" class="kt-button kt-button--secondary" data-cleanup-refresh type="button" :disabled="checking || submitting" @click="pollCleanup(undefined, true)">重新查询文件清理状态</button>
      <button v-if="preview?.action === 'delete_class' && !preview.allowed && preview.experiments.length" class="kt-button kt-button--danger" data-remove-all-class-experiments type="button" @click="deleteAllClassExperiments">删除该班级全部实验</button>
      <button class="kt-button kt-button--danger" data-deletion-confirm type="button" :disabled="!canConfirm" @click="confirmDeletion">{{ confirmLabel }}</button>
      <button class="kt-button kt-button--secondary" data-deletion-cancel type="button" :disabled="submitting" @click="cancel">{{ pendingOperation ? '关闭，稍后核实' : '取消' }}</button>
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
