<script setup lang="ts">
import type { ResearchBehavior } from './api'
import { formatResearchTime as time } from './research-format'
defineProps<{ behavior: ResearchBehavior[]; generatedAt: string }>()
</script>
<template>
  <section class="kt-research-panel">
    <div class="kt-research-section-heading"><div><h2>行为过程</h2><p>该题目在所选访问范围内的详细行为日志，按发生时间排序。</p></div><div class="kt-research-panel-meta"><strong>共 {{ behavior.length }} 条</strong><span>数据生成于 {{ time(generatedAt) }}</span></div></div>
    <div class="kt-research-table-scroll">
      <table class="kt-research-table kt-research-behavior-table">
        <thead><tr><th>课程内题目ID</th><th>第几次访问</th><th>题内行为序号</th><th>行为类别</th><th>具体行为</th><th>发生时间</th><th>变化前内容</th><th>变化后内容</th><th>补充信息</th></tr></thead>
        <tbody><tr v-for="item in behavior" :key="item.question_ref + ':' + item.visit_index + ':' + item.sequence"><td>{{ item.question_ref }}</td><td>{{ item.visit_index ?? '—' }}</td><td>{{ item.sequence }}</td><td>{{ item.category }}</td><td>{{ item.action }}</td><td>{{ time(item.occurred_at) }}</td><td class="kt-multiline">{{ item.before || '—' }}</td><td class="kt-multiline">{{ item.after || '—' }}</td><td><details v-if="item.details"><summary>查看记录</summary><p>{{ item.details }}</p></details><span v-else>—</span></td></tr></tbody>
      </table>
    </div>
  </section>
</template>
