<script setup lang="ts">
import { computed } from 'vue'
import QuestionContent from './QuestionContent.vue'
import { hasQuestionImage } from './question-content'
import type { GroupActivationIntent, GroupAnswerChange, QuestionGroupView } from './question-group-content'
import './question-group-content.css'

const props = withDefaults(defineProps<{
  group: QuestionGroupView
  responsesByItemKey: Record<string, unknown>
  activeItemKey?: string | null
  pendingItemKey?: string | null
  readonly?: boolean
  memberWriteAllowed?: Record<string, boolean>
}>(), { activeItemKey: null, pendingItemKey: null, readonly: false, memberWriteAllowed: () => ({}) })
const emit = defineEmits<{
  'request-activate': [intent: GroupActivationIntent]
  'answer-change': [change: GroupAnswerChange]
}>()
const members = computed(() => new Map(props.group.members.map(member => [member.item_key, member])))
const composing = new Set<string>()
function activate(itemKey: string) {
  if (editable(itemKey) && itemKey !== props.activeItemKey) emit('request-activate', { itemKey })
}
function editable(itemKey: string) { return !props.readonly && props.memberWriteAllowed?.[itemKey] !== false }
function change(itemKey: string, answer: unknown) {
  if (!editable(itemKey) || composing.has(itemKey)) return
  if (itemKey !== props.activeItemKey) emit('request-activate', { itemKey, answer, hasAnswer: true })
  else emit('answer-change', { itemKey, answer })
}
function value(itemKey: string) { return String(props.responsesByItemKey[itemKey] ?? '') }
function finishComposition(itemKey: string, event: Event) {
  composing.delete(itemKey)
  change(itemKey, (event.target as HTMLInputElement).value)
}
</script>

<template>
  <section class="kt-question-group" :class="`kt-question-group--${group.kind}`" :aria-busy="Boolean(pendingItemKey)" :data-group-id="group.group_id">
    <header v-if="group.instructions" class="kt-question-group__heading">
      <p v-if="group.instructions"><QuestionContent :text="group.instructions" :images="group.images" /></p>
    </header>
    <div class="kt-question-group__body">
      <article class="kt-question-group__material" :lang="group.content_language" aria-label="共享材料">
        <h2 v-if="group.title"><QuestionContent :text="group.title" :images="group.images" /></h2>
        <div v-if="group.kind === 'words'" class="kt-question-group__word-bank" aria-label="共享词库"><span v-for="word in group.word_bank" :key="word">{{ word }}</span></div>
        <template v-if="group.kind === 'cloze' || group.kind === 'words' || group.kind === 'fill_blank'">
          <template v-for="(segment, index) in group.material_segments" :key="index">
            <QuestionContent v-if="segment.kind === 'text'" :text="segment.text" :images="group.images" />
            <span v-else-if="group.kind === 'fill_blank'" :aria-label="`第 ${members.get(segment.item_key)?.group_order} 空`">____</span>
            <label v-else class="kt-question-group__blank" :class="{ 'is-active': activeItemKey === segment.item_key }">
              <span>{{ members.get(segment.item_key)?.group_order }}</span>
              <select v-if="group.kind === 'words'" :data-group-item="segment.item_key" :aria-label="`第 ${members.get(segment.item_key)?.group_order} 空`" :value="value(segment.item_key)" :disabled="!editable(segment.item_key)" @focus="activate(segment.item_key)" @change="change(segment.item_key, ($event.target as HTMLSelectElement).value)">
                <option value="">请选择</option><option v-for="word in group.word_bank" :key="word" :value="word">{{ word }}</option>
              </select>
              <input v-else :data-group-item="segment.item_key" :aria-label="`第 ${members.get(segment.item_key)?.group_order} 空`" :value="value(segment.item_key)" :disabled="!editable(segment.item_key)" autocomplete="off" autocapitalize="off" :spellcheck="false" @focus="activate(segment.item_key)" @input="change(segment.item_key, ($event.target as HTMLInputElement).value)" @compositionstart="composing.add(segment.item_key)" @compositionend="finishComposition(segment.item_key, $event)">
            </label>
          </template>
        </template>
        <QuestionContent v-else :text="group.material" :images="group.images" />
      </article>
      <section v-if="group.kind === 'fill_blank'" class="kt-question-group__answer-area" aria-label="作答区" data-group-answer-area>
        <h3>作答区</h3>
        <div v-for="member in group.members" :key="member.item_key" class="kt-question-group__answer-row">
          <div class="kt-question-group__answer-label">
            <label :for="`blank-answer-${group.group_id}-${member.item_key}`">第 {{ member.group_order }} 空</label>
            <button v-if="!readonly" type="button" :data-clear-group-item="member.item_key" :aria-label="`清除第 ${member.group_order} 空`" :disabled="!editable(member.item_key) || !responsesByItemKey[member.item_key]" @click="change(member.item_key, '')">清除</button>
          </div>
          <input :id="`blank-answer-${group.group_id}-${member.item_key}`" :data-group-item="member.item_key" :aria-label="`第 ${member.group_order} 空`" :value="value(member.item_key)" :disabled="!editable(member.item_key)" autocomplete="off" autocapitalize="off" :spellcheck="false" @focus="activate(member.item_key)" @input="change(member.item_key, ($event.target as HTMLInputElement).value)" @compositionstart="composing.add(member.item_key)" @compositionend="finishComposition(member.item_key, $event)">
        </div>
      </section>
      <div v-if="group.kind === 'reading'" class="kt-question-group__questions">
        <fieldset v-for="member in group.members" :key="member.item_key" :data-group-question="member.item_key" :class="{ 'is-active': activeItemKey === member.item_key }">
          <legend class="kt-question-group__legend">小题 {{ member.group_order }}</legend>
          <div class="kt-question-group__meta"><span>小题 {{ member.group_order }}<template v-if="member.max_score"> · {{ member.max_score }} 分</template></span><button v-if="!readonly" type="button" :data-clear-group-item="member.item_key" :disabled="!editable(member.item_key) || !responsesByItemKey[member.item_key]" @click="change(member.item_key, null)">清除</button></div>
          <h3><QuestionContent :text="member.text" :images="{ ...group.images, ...member.images }" /></h3>
          <div class="kt-question-group__options" :class="{ 'has-images': member.options.some(option => hasQuestionImage(option.text)) }">
            <label v-for="option in member.options" :key="option.key" class="kt-question-group__option" :class="{ 'is-selected': responsesByItemKey[member.item_key] === option.key }">
              <input type="radio" :name="`group-${group.group_id}-${member.item_key}`" :value="option.key" :disabled="!editable(member.item_key)" :checked="responsesByItemKey[member.item_key] === option.key" @focus="activate(member.item_key)" @change="change(member.item_key, option.key)">
              <strong>{{ option.key }}</strong><QuestionContent :text="option.text" :images="{ ...group.images, ...member.images }" option />
            </label>
          </div>
        </fieldset>
      </div>
    </div>
    <div v-if="!readonly && group.kind !== 'reading' && group.kind !== 'fill_blank'" class="kt-question-group__clear-blanks"><button v-for="member in group.members" :key="member.item_key" type="button" :data-clear-group-item="member.item_key" :disabled="!editable(member.item_key) || !responsesByItemKey[member.item_key]" @click="change(member.item_key, '')">清除第 {{ member.group_order }} 空</button></div>
  </section>
</template>
