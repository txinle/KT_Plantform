<script setup lang="ts">
import { computed } from 'vue'
import type { GroupActivationIntent, GroupAnswerChange, QuestionGroupView } from './question-group-content'
import './question-group-content.css'

const props = withDefaults(defineProps<{
  group: QuestionGroupView
  responsesByItemKey: Record<string, unknown>
  activeItemKey?: string | null
  pendingItemKey?: string | null
  readonly?: boolean
}>(), { activeItemKey: null, pendingItemKey: null, readonly: false })
const emit = defineEmits<{
  'request-activate': [intent: GroupActivationIntent]
  'answer-change': [change: GroupAnswerChange]
}>()
const members = computed(() => new Map(props.group.members.map(member => [member.item_key, member])))
const composing = new Set<string>()
function activate(itemKey: string) {
  if (!props.readonly && itemKey !== props.activeItemKey) emit('request-activate', { itemKey })
}
function change(itemKey: string, answer: unknown) {
  if (props.readonly || composing.has(itemKey)) return
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
    <header v-if="group.title || group.instructions" class="kt-question-group__heading">
      <h2 v-if="group.title" :lang="group.content_language">{{ group.title }}</h2>
      <p v-if="group.instructions">{{ group.instructions }}</p>
    </header>
    <p v-if="group.kind === 'words'" class="kt-question-group__word-bank" aria-label="共享词库">{{ group.word_bank?.join(' · ') }}</p>
    <div class="kt-question-group__body">
      <article class="kt-question-group__material" :lang="group.content_language" aria-label="共享材料">
        <template v-if="group.kind === 'cloze' || group.kind === 'words'">
          <template v-for="(segment, index) in group.material_segments" :key="index">
            <span v-if="segment.kind === 'text'">{{ segment.text }}</span>
            <label v-else class="kt-question-group__blank" :class="{ 'is-active': activeItemKey === segment.item_key }">
              <span>{{ members.get(segment.item_key)?.group_order }}</span>
              <select v-if="group.kind === 'words'" :data-group-item="segment.item_key" :aria-label="`第 ${members.get(segment.item_key)?.group_order} 空`" :value="value(segment.item_key)" :disabled="readonly" @focus="activate(segment.item_key)" @change="change(segment.item_key, ($event.target as HTMLSelectElement).value)">
                <option value="">请选择</option><option v-for="word in group.word_bank" :key="word" :value="word">{{ word }}</option>
              </select>
              <input v-else :data-group-item="segment.item_key" :aria-label="`第 ${members.get(segment.item_key)?.group_order} 空`" :value="value(segment.item_key)" :disabled="readonly" autocomplete="off" autocapitalize="off" :spellcheck="false" @focus="activate(segment.item_key)" @input="change(segment.item_key, ($event.target as HTMLInputElement).value)" @compositionstart="composing.add(segment.item_key)" @compositionend="finishComposition(segment.item_key, $event)">
            </label>
          </template>
        </template>
        <template v-else>{{ group.material }}</template>
      </article>
      <div v-if="group.kind === 'reading'" class="kt-question-group__questions">
        <fieldset v-for="member in group.members" :key="member.item_key" :data-group-question="member.item_key" :disabled="readonly" :class="{ 'is-active': activeItemKey === member.item_key }">
          <legend><span>{{ member.group_order }}.</span> {{ member.text }}</legend>
          <span v-if="member.max_score" class="kt-question-group__score">{{ member.max_score }} 分</span>
          <label v-for="option in member.options" :key="option.key" class="kt-question-group__option" :class="{ 'is-selected': responsesByItemKey[member.item_key] === option.key }">
            <input type="radio" :name="`group-${group.group_id}-${member.item_key}`" :value="option.key" :checked="responsesByItemKey[member.item_key] === option.key" @focus="activate(member.item_key)" @change="change(member.item_key, option.key)">
            <strong>{{ option.key }}</strong><span>{{ option.text }}</span>
          </label>
        </fieldset>
      </div>
    </div>
  </section>
</template>
