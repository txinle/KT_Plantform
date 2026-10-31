<script setup lang="ts">
import { computed, ref } from 'vue'
import type { GroupEditorDocument } from './group-api'
import type { BankUnitSummary } from './api'

const props = defineProps<{ summary?: BankUnitSummary; groups?: GroupEditorDocument[]; canEdit: boolean }>()
const emit = defineEmits<{ filter: [value: { query: string; type: string }]; edit: [id?: number] }>()
const query = ref('')
const type = ref('')
const labels = { reading: '阅读理解', words: '选词填空', cloze: '短文填空' }
const shownGroups = computed(() => (props.groups || []).filter(group => (!type.value || group.kind === type.value) && `${group.title} ${group.instructions} ${group.material}`.toLocaleLowerCase().includes(query.value.trim().toLocaleLowerCase())))
function filter() { emit('filter', { query: query.value.trim(), type: type.value }) }
</script>

<template>
  <section class="kt-bank-inventory" aria-label="完整题目与题型">
    <div class="kt-bank-inventory__facts">
      <span><strong>{{ summary?.complete_count ?? 0 }}</strong> 道完整题目</span>
      <span><strong>{{ summary?.types.length ?? 0 }}</strong> 种实际题型</span>
      <span><strong>{{ summary?.usable_count ?? 0 }}</strong> 道可用</span>
      <small>每篇阅读或填空文章计为一道题，组内保留各作答项。</small>
    </div>
    <form class="kt-bank-inventory__toolbar" @submit.prevent="filter">
      <input v-model="query" aria-label="搜索题目" placeholder="搜索标题、题干或作答说明" type="search" @change="filter">
      <select v-model="type" aria-label="按题型筛选" @change="filter"><option value="">全部题型</option><option v-for="item in summary?.types" :key="item.value" :value="item.value">{{ item.label }} · {{ item.count }} 道</option></select>
      <button class="kt-button kt-button--secondary" type="submit">搜索</button>
      <button v-if="canEdit" class="kt-button" type="button" @click="emit('edit')">新建篇章题</button>
    </form>
    <div v-if="shownGroups.length" class="kt-bank-inventory__groups" aria-label="篇章题目">
      <article v-for="group in shownGroups" :key="group.id" class="kt-bank-inventory__group">
        <div><span class="kt-bank-inventory__kind">{{ labels[group.kind] }}</span><span class="kt-bank-inventory__state" :data-status="group.status">{{ group.status === 'active' ? '可用' : '草稿' }}</span></div>
        <h3>{{ group.title || labels[group.kind] }}</h3>
        <p>{{ group.instructions || group.material }}</p>
        <div class="kt-bank-inventory__group-foot"><span>1 道完整题 · {{ group.members.length }} 作答项 · {{ group.total_max_score }} 分</span><button v-if="canEdit" type="button" @click="emit('edit', group.id)">编辑整题 →</button></div>
        <details><summary>查看完整材料、作答项与知识点</summary><p class="kt-bank-inventory__material">{{ group.material }}</p><p v-if="group.word_bank?.length">词库：{{ group.word_bank.join(' · ') }}</p><ol><li v-for="member in group.members" :key="member.item_key">{{ member.text || `第 ${member.group_order} 空` }} · {{ member.max_score }} 分 <span>{{ member.kc_mapping?.nodes.map(node => node.kc_code).join('、') || '未映射 KC' }}</span></li></ol></details>
      </article>
    </div>
  </section>
</template>

<style scoped>
.kt-bank-inventory { margin-block: 1.5rem; }
.kt-bank-inventory__facts { display: flex; align-items: center; flex-wrap: wrap; gap: 1.5rem; padding: 1rem 0; border-top: 1px solid var(--kt-border); border-bottom: 1px solid var(--kt-border); color: var(--kt-muted); }
.kt-bank-inventory__facts strong { margin-right: .3rem; font-size: 1.35rem; color: var(--kt-ink); }
.kt-bank-inventory__facts small { margin-left: auto; }
.kt-bank-inventory__toolbar { display: flex; flex-wrap: wrap; gap: .75rem; padding-block: 1.25rem; }
.kt-bank-inventory__toolbar input, .kt-bank-inventory__toolbar select { min-height: 44px; padding: .65rem .8rem; border: 1px solid var(--kt-border); border-radius: 6px; color: var(--kt-ink); background: var(--kt-surface); font: inherit; }
.kt-bank-inventory__toolbar input { flex: 1 1 16rem; min-width: 0; }
.kt-bank-inventory__toolbar select { min-width: 12rem; }
.kt-bank-inventory__groups { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 22rem), 1fr)); gap: 1rem; }
.kt-bank-inventory__group { min-width: 0; border: 1px solid var(--kt-border); border-radius: 10px; padding: 1.25rem; background: var(--kt-surface); }
.kt-bank-inventory__kind { color: var(--kt-action-600); font-size: .8rem; background: var(--kt-action-050); padding: .25rem .5rem; border-radius: 4px; }
.kt-bank-inventory__state { float: right; color: var(--kt-muted); font-size: .8rem; }
.kt-bank-inventory__state[data-status="active"] { color: #28704b; }
.kt-bank-inventory__group h3 { margin: .9rem 0 .5rem; font-size: 1.05rem; }
.kt-bank-inventory__group p { margin: 0 0 1.2rem; color: var(--kt-muted); line-height: 1.7; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.kt-bank-inventory__group-foot { display: flex; flex-wrap: wrap; justify-content: space-between; gap: .5rem; font-size: .85rem; }
.kt-bank-inventory__group-foot button { border: 0; background: none; color: var(--kt-action-600); cursor: pointer; }
.kt-bank-inventory details { margin-top: .9rem; font-size: .85rem; color: var(--kt-muted); }
.kt-bank-inventory .kt-bank-inventory__material { display: block; overflow: visible; white-space: pre-wrap; overflow-wrap: anywhere; margin-top: 1rem; }
.kt-bank-inventory li { margin-block: .5rem; overflow-wrap: anywhere; }
@media (max-width: 48rem) { .kt-bank-inventory__facts { gap: .75rem 1.2rem; } .kt-bank-inventory__facts small { flex-basis: 100%; } .kt-bank-inventory__toolbar select { flex: 1; } }
</style>
