<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'

import { bootstrapContext } from '@/app/bootstrap'

import DeletionDialog from './DeletionDialog.vue'
import type { DeletionOperationInput, DeletionTarget } from './api'

const props = defineProps<{ courseId: number }>()
const operations = ref<{ key: string; target: DeletionTarget; label: string }[]>([])
const activeTarget = ref<DeletionTarget>()

function refresh() {
  const actor = bootstrapContext.value?.user.id
  const prefix = `kt-teacher-deletion:v1:${actor}:${props.courseId}:`
  const found: typeof operations.value = []
  for (let index = 0; index < sessionStorage.length; index += 1) {
    const key = sessionStorage.key(index)
    if (!actor || !key?.startsWith(prefix)) continue
    try {
      const saved = JSON.parse(sessionStorage.getItem(key) || 'null') as { input?: DeletionOperationInput; submitted?: boolean } | null
      const input = saved?.input
      if (!input?.operation_id || typeof saved?.submitted !== 'boolean' || !Number.isSafeInteger(input.object_id) || input.object_id <= 0) continue
      let target: DeletionTarget
      let label: string
      if (input.action === 'delete_experiment' && key === `${prefix}experiment:${input.object_id}`) {
        target = { kind: 'experiment', experimentId: input.object_id }
        label = `实验 #${input.object_id}`
      } else if (input.action === 'delete_class' && key === `${prefix}class:${input.object_id}`) {
        target = { kind: 'class', classId: input.object_id }
        label = `班级 #${input.object_id}`
      } else if (input.action === 'remove_class_experiment' && Number.isSafeInteger(input.class_id) && Number(input.class_id) > 0
          && key === `${prefix}class_experiment:${input.class_id}:${input.object_id}`) {
        target = { kind: 'class_experiment', classId: input.class_id!, experimentId: input.object_id }
        label = `班级 #${input.class_id} 中的实验 #${input.object_id}`
      } else continue
      found.push({ key, target, label })
    } catch { /* Ignore unrelated or incomplete browser records. */ }
  }
  operations.value = found
}
function closeRecovery() {
  activeTarget.value = undefined
  refresh()
}

onMounted(() => {
  refresh()
  window.addEventListener('kt-teacher-deletion-change', refresh)
})
onBeforeUnmount(() => window.removeEventListener('kt-teacher-deletion-change', refresh))
</script>

<template>
  <aside v-if="operations.length" class="kt-deletion-recovery" aria-label="待核实的删除操作">
    <p>有删除操作尚待核实或文件清理尚未完成，可查询已保存的操作回执。</p>
    <button
      v-for="operation in operations"
      :key="operation.key"
      class="kt-button kt-button--secondary"
      data-deletion-recover
      type="button"
      @click="activeTarget = operation.target"
    >
      查询{{ operation.label }}的删除结果
    </button>
  </aside>
  <DeletionDialog
    v-if="activeTarget"
    :course-id="courseId"
    :target="activeTarget"
    recovery-only
    @cancelled="closeRecovery"
    @completed="closeRecovery"
  />
</template>

<style scoped>
.kt-deletion-recovery { margin: 1rem; padding: 1rem; border: 1px solid var(--kt-color-border); color: var(--kt-color-text); background: var(--kt-color-surface); }
.kt-deletion-recovery p { margin: 0 0 .75rem; }
.kt-deletion-recovery button { margin: .25rem; max-width: 100%; white-space: normal; }
</style>
