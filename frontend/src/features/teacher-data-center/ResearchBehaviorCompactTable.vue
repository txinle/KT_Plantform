<script setup lang="ts">
import type { ResearchBehavior } from './api'
import { formatResearchTime } from './research-format'
defineProps<{ behavior: ResearchBehavior[] }>()
function time(value: string) {
  return formatResearchTime(value, true)
}
</script>
<template>
  <div class="kt-research-table-scroll">
    <table class="kt-research-table kt-research-compact-table">
      <thead><tr><th>序号</th><th>时间</th><th>行为类型</th><th>行为内容</th></tr></thead>
      <tbody><tr v-for="item in behavior" :key="item.question_ref + ':' + item.visit_index + ':' + item.sequence"><td>{{ item.sequence }}</td><td>{{ time(item.occurred_at) }}</td><td>{{ item.category }}</td><td>{{ item.action }}<span v-if="item.before || item.after" class="kt-multiline">：{{ item.before || '空' }} → {{ item.after || '空' }}</span><details v-if="item.details"><summary>查看记录</summary><p>{{ item.details }}</p></details></td></tr></tbody>
    </table>
  </div>
</template>
