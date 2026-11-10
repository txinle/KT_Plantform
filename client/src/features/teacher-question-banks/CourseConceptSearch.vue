<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { getTeacherQuestionBankApi, type CourseConceptCollection } from './api'

const props = defineProps<{ courseId: number; status: 'active' | 'archived'; initialQuery: string }>()
const emit = defineEmits<{ filter: [query: string, id?: number] }>()
const api = getTeacherQuestionBankApi()
const input = ref<HTMLInputElement>()
const text = ref(props.initialQuery)
const open = ref(false)
const results = ref<CourseConceptCollection>()
const message = ref('')
const highlighted = ref(-1)
const query = computed(() => text.value.trim())

watch([query, open], ([term, visible], _, onCleanup) => {
  results.value = undefined
  highlighted.value = -1
  message.value = ''
  if (!term || !visible) return
  let current = true
  message.value = '正在搜索…'
  const timer = setTimeout(async () => {
    try {
      const collection = await api.courseConcepts(props.courseId, { query: term, status: props.status, page: 1, pageSize: 5 })
      if (!current) return
      results.value = collection
      message.value = collection.items.length ? '' : '没有匹配的课程 KC。'
    } catch (error) {
      if (current) message.value = error instanceof Error ? error.message : 'KC 搜索失败。'
    }
  }, 200)
  onCleanup(() => { current = false; clearTimeout(timer) })
})

function clear() {
  text.value = ''
  open.value = false
  if (props.initialQuery) emit('filter', '')
  input.value?.focus()
}
function changed(event: Event) {
  if ((event as InputEvent).isComposing) return
  if (!query.value) clear()
  else open.value = true
}
function apply(index = -1) {
  const item = results.value?.items[index]
  open.value = false
  if (item) text.value = item.name
  emit('filter', item?.name ?? query.value, item?.id)
}
function move(step: number) {
  open.value = Boolean(query.value)
  const count = results.value?.items.length || 0
  if (!count) return
  if (highlighted.value < 0) highlighted.value = step > 0 ? 0 : count - 1
  else highlighted.value = (highlighted.value + step + count) % count
}
function keydown(event: KeyboardEvent) {
  if (event.isComposing || event.keyCode === 229) return
  if (event.key === 'Enter') apply(open.value ? highlighted.value : -1)
  else if (event.key === 'ArrowDown') move(1)
  else if (event.key === 'ArrowUp') move(-1)
  else if (event.key === 'Escape') open.value = false
  else return
  event.preventDefault()
}
</script>

<template>
  <div class="kc-search" @focusout="!($event.currentTarget as HTMLElement).contains($event.relatedTarget as Node) && (open = false)">
    <input
      ref="input" v-model="text" type="search" role="combobox" autocomplete="off" aria-autocomplete="list" aria-label="按 KC 名称或编码搜索" placeholder="搜索 KC 名称或编码"
      aria-controls="kc-search-options" :aria-expanded="open" :aria-activedescendant="open && highlighted >= 0 ? `kc-search-option-${highlighted}` : undefined"
      @input="changed" @focus="open = Boolean(query)" @keydown="keydown"
    >
    <button v-if="text" class="kc-search-clear" type="button" aria-label="清除 KC 搜索" @click="clear">×</button>
    <div v-if="open" class="kc-search-panel">
      <p v-if="message" role="status">{{ message }}</p>
      <ul id="kc-search-options" role="listbox" aria-label="匹配的 KC">
        <li
          v-for="(item, index) in results?.items" :id="`kc-search-option-${index}`" :key="item.id" role="option" :aria-selected="highlighted === index"
          @mousedown.prevent @click="apply(index)"
        >
          {{ item.name }} · {{ item.kc_code }}
        </li>
      </ul>
      <button v-if="results && results.page.total_pages > 1" type="button" @click="apply()">查看全部 {{ results.page.total_items }} 个匹配</button>
    </div>
  </div>
</template>

<style scoped>
.kc-search { position: relative; flex: 1 1 13rem; max-width: 24rem; min-width: 0; }
.kc-search input { box-sizing: border-box; width: 100%; min-height: 44px; padding: 8px 42px 8px 12px; border: 1px solid var(--kt-border); border-radius: 6px; color: var(--kt-ink); background: white; font: inherit; }
.kc-search input::-webkit-search-cancel-button { display: none; }
.kc-search-clear { position: absolute; top: 0; right: 0; width: 40px; height: 44px; border: 0; background: transparent; color: var(--kt-muted); font-size: 20px; cursor: pointer; }
.kc-search-panel { position: absolute; z-index: 4; top: 100%; left: 0; right: 0; margin-top: 3px; padding: 4px; border: 1px solid var(--kt-border); border-radius: 6px; background: white; box-shadow: 0 4px 12px #182d491a; }
.kc-search-panel ul { list-style: none; padding: 0; margin: 0; }
.kc-search-panel li, .kc-search-panel p, .kc-search-panel button { box-sizing: border-box; padding: 9px; margin: 0; overflow-wrap: anywhere; font-size: 14px; }
.kc-search-panel li, .kc-search-panel button { cursor: pointer; }
.kc-search-panel li[aria-selected=true], .kc-search-panel li:hover { background: #edf3ff; }
.kc-search-panel button { width: 100%; border: 0; background: white; color: var(--kt-action-600); }
@media (max-width: 760px) { .kc-search { flex-basis: 100%; max-width: none; order: 1; } }
</style>
