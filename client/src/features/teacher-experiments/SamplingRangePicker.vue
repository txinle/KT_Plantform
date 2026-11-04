<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import ModalDialog from '@/design-system/components/ModalDialog.vue'
import Paginator from '@/design-system/components/Paginator.vue'
import ExperimentQuestionPreview from './ExperimentQuestionPreview.vue'
import type { ExperimentQuestionType, SamplingInventory } from './api'

const props = defineProps<{
  questionType: ExperimentQuestionType; label: string; courseId: number; quota: number
  selected: number[] | null; questions: NonNullable<SamplingInventory['questions']>
  banks: Array<{ id: number; name: string }>; loading: boolean; error: string
}>()
const emit = defineEmits<{ close: []; apply: [ids: number[] | null] }>()
const mode = ref(props.selected === null ? 'all' : 'custom')
const pending = ref([...(props.selected || [])])
const query = ref('')
const selectedOnly = ref(false)
const page = ref(1)
const previewIds = ref<number[]>([])
const validation = ref('')
const source = computed(() => props.questions.filter(q => q.question_type === props.questionType))
const invalid = computed(() => pending.value.filter(id => !source.value.some(q => q.question_id === id)))
const filtered = computed(() => source.value.filter(q => (!selectedOnly.value || pending.value.includes(q.question_id))
  && `${q.code} ${q.title} ${q.text}`.toLocaleLowerCase().includes(query.value.trim().toLocaleLowerCase())))
const pages = computed(() => Math.max(1, Math.ceil(filtered.value.length / 12)))
const rows = computed(() => filtered.value.slice((page.value - 1) * 12, page.value * 12))
watch([query, selectedOnly], () => { page.value = 1 })
watch(pages, count => { page.value = Math.min(page.value, count) })
function selectPage() {
  pending.value = [...new Set([...pending.value, ...rows.value.map(q => q.question_id)])]
}
function apply() {
  validation.value = ''
  if (props.loading || props.error) return
  if (mode.value === 'all') { emit('apply', null); return }
  if (!pending.value.length || pending.value.length < props.quota) {
    validation.value = `至少选择 ${Math.max(1, props.quota)} 道候选题目；候选范围不能少于抽取数量。`
    return
  }
  if (invalid.value.length) { validation.value = '存在已失效的候选题目，请先移除或恢复全题库随机。'; return }
  emit('apply', [...pending.value])
}
</script>

<template>
  <ModalDialog open :title="`抽题范围 · ${label}`" wide @close="emit('close')">
    <p class="kt-picker-note">设置可参与随机抽取的候选题目，本题型每位学生抽取 {{ quota }} 道。选入候选范围不代表必出。</p>
    <div class="kt-range-modes">
      <label :class="{ active: mode === 'all' }"><input v-model="mode" data-range-all type="radio" value="all"><span><strong>全题库随机</strong><small>使用当前题库与知识点范围内的全部可用题目</small></span></label>
      <label :class="{ active: mode === 'custom' }"><input v-model="mode" data-range-custom type="radio" value="custom"><span><strong>自定义候选范围</strong><small>仅从选定的完整题目中随机抽取</small></span></label>
    </div>
    <p v-if="loading" role="status">正在读取候选题目…</p>
    <p v-else-if="error" class="kt-picker-error" role="alert">{{ error }}</p>
    <p v-if="mode === 'all'" class="kt-range-all-note">当前范围共 {{ source.length }} 道可用题目。确认后恢复全题库随机；题库或知识点变化后，按新的有效范围抽取。</p>
    <template v-else>
      <div class="kt-range-search"><label class="kt-picker-search">搜索题目<input v-model="query" type="search" placeholder="编号、标题或题干"></label><label><input v-model="selectedOnly" data-range-selected-only type="checkbox">仅看已选</label></div>
      <div class="kt-range-batch"><button class="kt-text-button" type="button" data-range-select-page @click="selectPage">选择本页</button><button class="kt-text-button" type="button" @click="pending = pending.filter(id => !rows.some(q => q.question_id === id))">取消本页</button><span>已选 {{ pending.length }} 道候选 / 抽取 {{ quota }} 道</span></div>
      <p v-if="invalid.length" role="alert" class="kt-picker-error">{{ invalid.length }} 道候选题目已不在当前有效范围内。<button type="button" class="kt-text-button" data-clear-invalid-ranges @click="pending = pending.filter(id => !invalid.includes(id))">移除失效候选</button></p>
      <div v-for="item in rows" :key="item.question_id" class="kt-picker-question" :class="{ selected: pending.includes(item.question_id) }">
        <label class="kt-picker-choice"><input v-model="pending" type="checkbox" :value="item.question_id" :data-range-question="item.question_id"><span><strong>{{ item.title || item.code }}</strong><small>{{ banks.find(bank => bank.id === item.question_bank_id)?.name }} · {{ item.item_count }} 个作答项 · {{ item.code }}</small></span></label>
        <details @toggle="($event.target as HTMLDetailsElement).open && !previewIds.includes(item.question_id) && previewIds.push(item.question_id)"><summary>查看题目</summary><ExperimentQuestionPreview v-if="previewIds.includes(item.question_id)" :course-id="courseId" :bank-id="item.question_bank_id" :question-id="item.question_id" /></details>
      </div>
      <p v-if="!rows.length && !loading" class="kt-muted">没有匹配的可用题目。</p>
      <Paginator :page="page" :total-pages="pages" @navigate="page = $event" />
    </template>
    <p v-if="validation" class="kt-picker-error" role="alert">{{ validation }}</p>
    <template #footer><button class="kt-button kt-button--secondary" type="button" data-cancel-range @click="emit('close')">取消</button><button class="kt-button" type="button" data-apply-range :disabled="loading || Boolean(error)" @click="apply">确认抽题范围</button></template>
  </ModalDialog>
</template>

<style scoped>
.kt-range-modes{display:grid;grid-template-columns:1fr 1fr;gap:12px}.kt-range-modes label{display:flex;gap:10px;padding:14px;border:1px solid var(--kt-border);border-radius:7px}.kt-range-modes label.active{background:#f5f8ff;border-color:#8eafe9}.kt-range-modes small{display:block;color:#637388;margin-top:5px}.kt-range-all-note{padding:16px;background:#f8faff;margin-top:20px;line-height:1.7}.kt-range-search{display:flex;align-items:center;gap:16px;margin-top:20px}.kt-range-search>.kt-picker-search{flex:1;min-width:0}.kt-range-search>label:last-child{white-space:nowrap}.kt-range-batch{display:flex;align-items:center;gap:16px;flex-wrap:wrap;margin:12px 0}.kt-range-batch>span{margin-left:auto;font-size:14px}@media(max-width:600px){.kt-range-modes{grid-template-columns:1fr}.kt-range-search{align-items:stretch;flex-direction:column}.kt-range-batch>span{width:100%;margin:0}}
</style>
