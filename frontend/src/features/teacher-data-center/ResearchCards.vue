<script setup lang="ts">
import ResearchDataIcon from './ResearchDataIcon.vue'
interface TableCard { key: string; label: string; description: string; total_items: number }
defineProps<{ tables: TableCard[]; activeKey: string; compact?: boolean }>()
defineEmits<{ select: [key: string] }>()
const copy: Record<string, { label: string; description: string; icon: string }> = {
  sessions: { label: '会话', description: '一名学生参加实验的一次会话', icon: 'session' },
  questions: { label: '题目', description: '课程题目固定库，详情页展示当前范围相关题目', icon: 'question' },
  knowledge_points: { label: '知识点', description: '课程知识点库，详情页展示当前范围相关知识点', icon: 'knowledge' },
  question_kc_mapping: { label: '题目和知识点映射', description: '题目与知识点的明确对应关系', icon: 'mapping' },
  responses: { label: '逐题作答', description: '某会话中学生对一道题的最终作答事实', icon: 'response' },
  behavior_events: { label: '行为过程', description: '一次已记录的研究行为或状态变化', icon: 'behavior' },
}
</script>
<template>
  <nav class="kt-research-cards" :class="{ 'kt-research-cards--compact': compact }" aria-label="研究数据表">
    <button v-for="table in tables" :key="table.key" type="button" data-testid="research-card" :data-card-key="table.key" :aria-current="table.key === activeKey ? 'page' : undefined" @click="$emit('select', table.key)">
      <ResearchDataIcon :name="copy[table.key]?.icon || 'question'" />
      <strong>{{ copy[table.key]?.label || table.label }}</strong>
      <b>{{ table.total_items }} 条</b>
      <span>{{ copy[table.key]?.description || table.description }}</span>
    </button>
  </nav>
</template>
