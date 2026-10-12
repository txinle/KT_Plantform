<script setup lang="ts">
import type { ResearchResponse } from './api'
defineProps<{ responses: ResearchResponse[] }>()
</script>
<template>
  <div class="kt-research-table-scroll">
    <table class="kt-research-table">
      <thead><tr><th>题目</th><th>题型</th><th>访问次数</th><th>最终答案</th><th>状态</th><th>是否正确</th></tr></thead>
      <tbody><tr v-for="response in responses" :key="response.question_ref"><td>{{ response.question_ref }}</td><td>{{ response.question_type }}</td><td>{{ response.visit_count }}</td><td class="kt-multiline">{{ response.final_answer || '—' }}</td><td>{{ response.status_label }}</td><td><span :class="response.is_correct === true ? 'kt-correct' : response.is_correct === false ? 'kt-incorrect' : ''">{{ response.is_correct === null ? '未评分' : response.is_correct ? '✓ 正确' : '✕ 错误' }}</span></td></tr></tbody>
    </table>
  </div>
</template>
