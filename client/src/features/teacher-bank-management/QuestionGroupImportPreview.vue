<script setup lang="ts">
import { computed, ref } from 'vue'
import QuestionGroupContent from '@/question-content/QuestionGroupContent.vue'
import type { QuestionGroupView } from '@/question-content/question-group-content'
import type { QuestionImportPreviewGroup, QuestionImportPreviewRow } from './api'

const props = withDefaults(defineProps<{
  groups: QuestionImportPreviewGroup[]
  rows: Array<Partial<QuestionImportPreviewRow>>
  modelValue: string[]
  disabled?: boolean
}>(), { disabled: false })
const emit = defineEmits<{ 'update:modelValue': [ids: string[]] }>()
const activeId = ref<string>()
const student = ref(false)
const responses = ref<Record<string, unknown>>({})
const active = computed(() => props.groups.find(group => group.group_id === activeId.value) || props.groups[0])
const groupRows = computed(() => props.rows.filter(row => row.row_id && active.value?.member_row_ids.includes(row.row_id)))
const view = computed<QuestionGroupView | undefined>(() => active.value ? {
  ...active.value,
  members: groupRows.value.map((row, index) => ({ item_key: row.row_id!, question_type: row.question_type || '', text: row.text || '', options: row.options || [], group_order: row.group_order || index + 1, blank_id: row.blank_id || '', max_score: row.max_score || '1.00' })),
} : undefined)
function select(id: string, checked: boolean) {
  emit('update:modelValue', checked ? [...new Set([...props.modelValue, id])] : props.modelValue.filter(value => value !== id))
}
function inspect(id: string) { activeId.value = id; responses.value = {} }
const labels = { ready: '导入后可用', draft: '保存为草稿', invalid: '需修正', duplicate: '重复' }
</script>

<template>
  <section v-if="groups.length" class="qimp-groups" aria-label="题组预览">
    <div class="qimp-groups__list">
      <h2>{{ groups.length }} 个题组</h2>
      <div v-for="group in groups" :key="group.group_id" class="qimp-groups__row" :class="{ 'is-active': active?.group_id === group.group_id }">
        <input type="checkbox" :aria-label="`选择整个题组 ${group.source_group_code}`" :checked="modelValue.includes(group.group_id)" :disabled="disabled || !['ready', 'draft'].includes(group.classification)" @change="select(group.group_id, ($event.target as HTMLInputElement).checked)">
        <button type="button" @click="inspect(group.group_id)"><strong>{{ group.source_group_code }} {{ group.title }}</strong><span>{{ group.item_count }} 小题 · {{ group.total_max_score }} 分 · {{ labels[group.classification] }}</span></button>
      </div>
    </div>
    <div v-if="active && view" class="qimp-groups__detail">
      <nav aria-label="题组核对视图"><button class="kt-button" :class="{ 'kt-button--secondary': student }" type="button" @click="student = false">教师核对</button><button data-student-group-preview class="kt-button" :class="{ 'kt-button--secondary': !student }" type="button" @click="student = true">学生效果</button></nav>
      <ul v-if="!student && active.errors.length" role="alert"><li v-for="issue in active.errors" :key="issue">{{ issue }}</li></ul>
      <QuestionGroupContent v-if="student" :group="view" :responses-by-item-key="responses" @request-activate="intent => { if (intent.hasAnswer) responses[intent.itemKey] = intent.answer }" @answer-change="change => responses[change.itemKey] = change.answer" />
      <template v-else>
        <h3>{{ active.title }}</h3><p v-if="active.word_bank?.length">共享词库：{{ active.word_bank.join(' · ') }}</p><p class="qimp-groups__material" :lang="active.content_language">{{ active.material }}</p>
        <section v-for="row in groupRows" :key="row.row_id" class="qimp-groups__member">
          <h4>{{ row.group_order }}. {{ row.text || `第 ${row.group_order} 空` }} <small>{{ row.max_score || '1.00' }} 分</small></h4>
          <p v-for="option in row.options" :key="option.key">{{ option.key }}. {{ option.text }}</p>
          <p>答案：{{ row.answer }}<span v-if="row.accepted_answers?.length">；可接受：{{ row.accepted_answers.join('；') }}</span></p>
          <p>匹配：{{ row.text_match_case_sensitive ? '区分大小写' : '忽略大小写' }}，归一化空白</p>
          <p v-if="row.explanation">解析：{{ row.explanation }}</p>
          <p v-if="row.kc_mapping">KC：{{ row.kc_mapping.nodes.map(node => node.kc_code).join('、') }}</p>
          <p v-if="row.source_label">来源：{{ row.source_label }}</p>
          <ul v-if="row.errors?.length"><li v-for="issue in row.errors" :key="issue">{{ issue }}</li></ul>
        </section>
      </template>
    </div>
  </section>
</template>

<style scoped>
.qimp-groups { display: grid; grid-template-columns: minmax(240px, 22%) minmax(0, 1fr); margin-block: 1rem; border: 1px solid var(--kt-border); border-radius: 10px; background: var(--kt-surface); overflow: hidden; }
.qimp-groups__list { padding: 1rem; border-right: 1px solid var(--kt-border); }
.qimp-groups__row { display: flex; align-items: flex-start; gap: .5rem; margin-top: .5rem; padding: .75rem; border-radius: 6px; }
.qimp-groups__row.is-active { background: var(--kt-action-050); }
.qimp-groups__row button { min-width: 0; padding: 0; border: 0; background: none; text-align: left; cursor: pointer; }
.qimp-groups__row strong, .qimp-groups__row span { display: block; overflow-wrap: anywhere; }
.qimp-groups__row span, .qimp-groups__member small { font-size: .875rem; color: var(--kt-muted); }
.qimp-groups__detail { min-width: 0; padding: 1rem; }
.qimp-groups__detail > nav { display: flex; gap: .5rem; margin-bottom: 1rem; }
.qimp-groups__material { white-space: pre-wrap; font: 1.25rem/1.8 var(--kt-font-article); }
.qimp-groups__member { padding-block: 1rem; border-top: 1px solid var(--kt-border); }
.qimp-groups__member p { margin-bottom: .5rem; overflow-wrap: anywhere; }
@media (max-width: 63.99rem) { .qimp-groups { grid-template-columns: 1fr; } .qimp-groups__list { border-right: 0; border-bottom: 1px solid var(--kt-border); } }
</style>
