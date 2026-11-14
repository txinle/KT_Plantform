<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import ModalDialog from '@/design-system/components/ModalDialog.vue'
import Button from '@/design-system/components/Button.vue'
import Paginator from '@/design-system/components/Paginator.vue'
import { getTeacherExperimentApi, type ClassTask, type CollectionParticipantCollection, type AssignmentReturnPreview } from './api'

const props = defineProps<{ experimentId: number; task: Pick<ClassTask, 'id' | 'class_id' | 'class_name'> }>()
const emit = defineEmits<{ close: []; returned: [] }>()
const api = getTeacherExperimentApi()
const query = ref('')
const status = ref('')
const statusChoices = ref<CollectionParticipantCollection['status_choices']>([])
const collection = ref<CollectionParticipantCollection>()
const selected = ref<AssignmentReturnPreview>()
const loading = ref(false)
const sending = ref(false)
const error = ref('')
const success = ref('')
let requestId = 0

async function search(page = 1) {
  const request = ++requestId
  loading.value = true
  selected.value = undefined
  collection.value = undefined
  error.value = ''
  try {
    const result = await api.collectionParticipants(props.experimentId, { classId: props.task.class_id, query: query.value.trim(), status: status.value, page })
    if (request === requestId) {
      collection.value = result
      statusChoices.value = result.status_choices
    }
  } catch (caught) {
    if (request === requestId) error.value = caught instanceof Error ? caught.message : '学生列表加载失败。'
  } finally { if (request === requestId) loading.value = false }
}

async function select(participantId: number) {
  const request = ++requestId
  loading.value = true
  error.value = ''
  success.value = ''
  try {
    const result = await api.previewReturn(props.task.id, participantId)
    if (request === requestId) selected.value = result
  } catch (caught) {
    if (request === requestId) error.value = caught instanceof Error ? caught.message : '无法核对该学生的作答。'
  } finally { if (request === requestId) loading.value = false }
}

async function confirm() {
  const target = selected.value
  if (sending.value || !target?.can_return || !target.confirmation_token) return
  sending.value = true
  error.value = ''
  try {
    await api.returnAssignment(props.task.id, target.participant_id, target.confirmation_token)
    success.value = `${target.display_name}（${target.student_number}）已恢复为未开始。请让学生刷新后从作业入口重新进入。`
    selected.value = undefined
    emit('returned')
    await search()
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '打回结果未确认，请重试核验。'
  } finally { sending.value = false }
}

onMounted(() => search())
onBeforeUnmount(() => { requestId++ })
</script>

<template>
  <ModalDialog open title="作业打回" @close="!sending && emit('close')">
    <div class="kt-assignment-return">
      <p class="kt-picker-note">{{ task.class_name }} · 仅处理本次作业中选定学生的记录。</p>
      <p v-if="error" role="alert" class="kt-picker-error">{{ error }}</p>
      <p v-if="success" role="status" class="kt-return-success">{{ success }}</p>
      <template v-if="selected">
        <div class="kt-return-person"><strong>{{ selected.display_name }}</strong><span>{{ selected.student_number }} · {{ selected.class_name }}</span></div>
        <template v-if="selected.can_return">
          <p>将清除作答、提交及评分记录，不保留答案备份。此操作不可撤销。</p>
          <p>保留原来的 {{ selected.question_count }} 道题及顺序，恢复为“未开始”。学生重新进入后按原限时计时，班级截止时间不变。</p>
          <p class="kt-picker-note">当前已提交 {{ selected.submission_count }} 题。</p>
        </template>
        <p v-else class="kt-picker-note">{{ selected.blocked_reason }}</p>
      </template>
      <template v-else>
        <form class="kt-return-search" data-return-search @submit.prevent="search()">
          <label class="kt-picker-search">搜索学生<input v-model="query" placeholder="姓名或学号" :disabled="sending"></label>
          <label class="kt-return-status">学生状态<select v-model="status" data-return-status :disabled="sending" @change="search()"><option value="">全部状态</option><option v-for="choice in statusChoices" :key="choice.value" :value="choice.value">{{ choice.label }}</option></select></label>
          <Button type="submit" :disabled="loading || sending">搜索</Button>
        </form>
        <p v-if="loading" role="status">正在核对学生记录…</p>
        <template v-else-if="collection">
          <p v-if="!collection.items.length" class="kt-picker-note">没有找到匹配的学生。</p>
          <ul v-else class="kt-return-students">
            <li v-for="student in collection.items" :key="student.participant_id">
              <div><strong>{{ student.display_name }}</strong><span>{{ student.student_number }} · {{ student.status_label }}</span></div>
              <Button class="kt-button--secondary" :data-return-select="student.participant_id" :disabled="sending" @click="select(student.participant_id)">选择</Button>
            </li>
          </ul>
          <Paginator :page="collection.page.number" :total-pages="collection.page.total_pages" @navigate="search" />
        </template>
      </template>
    </div>
    <template #footer>
      <Button v-if="selected" class="kt-button--secondary" :disabled="sending" @click="selected = undefined; error = ''">重新选择</Button>
      <Button class="kt-button--secondary" :disabled="sending" @click="emit('close')">{{ success ? '完成' : '取消' }}</Button>
      <Button v-if="selected?.can_return" class="kt-button--danger" :disabled="sending" data-return-confirm @click="confirm">{{ sending ? '正在打回…' : '确认打回并清除' }}</Button>
    </template>
  </ModalDialog>
</template>

<style scoped>
.kt-return-search{display:flex;align-items:end;gap:12px;margin-bottom:18px}
.kt-return-search .kt-picker-search{margin:0;min-width:0}
.kt-return-status{display:grid;gap:6px;flex:0 0 140px;font-size:13px;min-width:0}
.kt-return-status select{box-sizing:border-box;width:100%;min-height:44px;border:1px solid #d9e1ec;border-radius:6px;padding:10px 12px;font:inherit;color:inherit;background:#fff}
@media(max-width:600px){.kt-return-search{flex-wrap:wrap}.kt-return-search .kt-picker-search{flex:1 1 100%}.kt-return-status{flex:1}}
.kt-return-students{list-style:none;margin:0;padding:0}
.kt-return-students li{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:14px 0;border-bottom:1px solid #e3e9f0}
.kt-return-students li>div,.kt-return-person{display:grid;gap:6px;min-width:0;overflow-wrap:anywhere}
.kt-return-students span,.kt-return-person span{color:#637388;font-size:14px}
.kt-return-person{padding:16px;border:1px solid #e3e9f0;border-radius:8px;margin-bottom:18px}
.kt-assignment-return p{line-height:1.7;overflow-wrap:anywhere}
.kt-return-success{color:#256045;background:#f1f8f3;border:1px solid #cfe5d5;border-radius:8px;padding:12px}
</style>
