<script setup lang="ts">
import type { ResearchResponse } from './api'
import { formatResearchTime as time } from './research-format'
defineProps<{ responses: ResearchResponse[] }>()
defineEmits<{ select: [questionRef: string] }>()
function times(values: string[]) { return values.length ? values.map(value => time(value)).join('\n') : '未记录' }
</script>
<template>
  <div class="kt-research-table-scroll">
    <table class="kt-research-table kt-clickable-table">
      <thead><tr><th aria-label="选择" /><th>课程内题目ID</th><th>分配顺序</th><th>首次访问顺序</th><th>访问次数</th><th>各次进入题目时间</th><th>各次离开题目时间</th><th>最终答案</th><th>答案状态</th><th>是否正确</th></tr></thead>
      <tbody>
        <tr v-for="response in responses" :key="response.question_ref" :data-question-ref="response.question_ref" tabindex="0" @click="$emit('select', response.question_ref)" @keydown.enter="$emit('select', response.question_ref)">
          <td><span class="kt-row-radio" /></td><td>{{ response.question_ref }}</td><td>{{ response.assigned_order }}</td><td>{{ response.first_visit_order }}</td><td>{{ response.visit_count }}</td><td class="kt-multiline">{{ times(response.entered_times) }}</td><td class="kt-multiline">{{ times(response.left_times) }}</td><td>{{ response.final_answer || '—' }}</td><td>{{ response.status_label }}</td><td><span :class="response.is_correct === true ? 'kt-correct' : response.is_correct === false ? 'kt-incorrect' : ''">{{ response.is_correct === null ? '—' : response.is_correct ? '● 正确' : '● 错误' }}</span></td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
