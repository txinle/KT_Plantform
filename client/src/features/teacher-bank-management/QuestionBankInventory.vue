<script setup lang="ts">
import { ref } from 'vue'
import type { GroupEditorDocument } from './group-api'
import type { BankUnitSummary } from './api'

withDefaults(defineProps<{ summary?: BankUnitSummary; groups?: GroupEditorDocument[]; canEdit: boolean; compact?: boolean; resultCount?: number }>(), { compact: false, resultCount: undefined, summary: undefined, groups: undefined })
const emit = defineEmits<{ filter: [value: { query: string; type: string; status: string }]; edit: [id?: number] }>()
const query = ref('')
const type = ref('')
const status = ref('')
function filter() { emit('filter', { query: query.value.trim(), type: type.value, status: status.value }) }
</script>

<template>
  <section class="kt-bank-inventory" :class="{ 'is-compact': compact }" aria-label="完整题目与题型">
    <div v-if="!compact" class="kt-bank-inventory__facts">
      <span><strong>{{ summary?.complete_count ?? 0 }}</strong> 道完整题目</span>
      <span><strong>{{ summary?.types.length ?? 0 }}</strong> 种实际题型</span>
      <span><strong>{{ summary?.usable_count ?? 0 }}</strong> 道可用</span>
      <small>每篇阅读或填空文章计为一道题，组内保留各作答项。</small>
    </div>
    <form class="kt-bank-inventory__toolbar" @submit.prevent="filter">
      <input v-model="query" aria-label="搜索题目" placeholder="搜索标题、题干或作答说明" type="search" @change="filter">
      <select v-model="type" aria-label="按题型筛选" @change="filter"><option value="">全部题型</option><option v-for="item in summary?.types" :key="item.value" :value="item.value">{{ item.label }} · {{ item.count }} 道</option></select>
      <select v-model="status" aria-label="题目状态" @change="filter"><option value="">全部状态</option><option value="active">可用</option><option value="draft">草稿</option><option value="archived">归档</option></select><button :class="compact ? 'kt-visually-hidden' : 'kt-button kt-button--secondary'" type="submit">搜索</button>
      <button v-if="canEdit && !compact" class="kt-button" type="button" @click="emit('edit')">新建题目</button>
      <span v-if="compact" class="kt-bank-inventory__count">{{ resultCount ?? summary?.complete_count ?? '—' }} 道</span>
    </form>
  </section>
</template>

<style scoped>
.kt-bank-inventory { margin-block: 1.5rem; }
.kt-bank-inventory__facts { display: flex; align-items: center; flex-wrap: wrap; gap: 1.5rem; padding: 0 0 14px; border-bottom: 1px solid var(--kt-border); color: var(--kt-muted); }
.kt-bank-inventory__facts strong { margin-right: .3rem; font-size: 16px; color: var(--kt-ink); }
.kt-bank-inventory__facts small { margin-left: auto; }
.kt-bank-inventory__toolbar { display: flex; flex-wrap: wrap; gap: .75rem; padding-block: 1.25rem; }
.kt-bank-inventory__toolbar input, .kt-bank-inventory__toolbar select { min-height: 44px; padding: .65rem .8rem; border: 1px solid var(--kt-border); border-radius: 6px; color: var(--kt-ink); background: var(--kt-surface); font: inherit; }
.kt-bank-inventory__toolbar input { flex: 1 1 16rem; min-width: 0; }
.kt-bank-inventory__toolbar select { min-width: 12rem; }
@media (max-width: 48rem) { .kt-bank-inventory__facts { gap: .75rem 1.2rem; } .kt-bank-inventory__facts small { flex-basis: 100%; } .kt-bank-inventory__toolbar select { flex: 1; } }
.kt-bank-inventory.is-compact{margin:0 0 14px}
.kt-bank-inventory.is-compact .kt-bank-inventory__toolbar{padding:0;display:flex;gap:14px;align-items:center}
.kt-bank-inventory.is-compact .kt-bank-inventory__toolbar input{flex:0 1 300px;width:300px}
.kt-bank-inventory.is-compact .kt-bank-inventory__toolbar select{flex:0 0 auto;width:auto;min-width:100px;max-width:220px}
.kt-bank-inventory__count{margin-left:auto;font-size:13px;color:#637388;white-space:nowrap}
@media(max-width:760px){.kt-bank-inventory.is-compact .kt-bank-inventory__toolbar input{flex:1 1 100%;width:100%}.kt-bank-inventory.is-compact .kt-bank-inventory__toolbar select{max-width:calc(50% - 8px);min-width:0}}
</style>
