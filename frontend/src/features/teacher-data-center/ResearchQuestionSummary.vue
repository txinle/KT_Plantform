<script setup lang="ts">
import type { ResearchResponse, ResearchSession } from './api'
defineProps<{ session: ResearchSession; question: ResearchResponse }>()
</script>
<template>
  <section class="kt-research-panel kt-session-summary">
    <dl>
      <div><dt>学生</dt><dd>{{ session.name }}</dd></div><div><dt>sessionID</dt><dd>{{ session.session_ref }}</dd></div><div><dt>当前题目</dt><dd>{{ question.question_ref }}</dd></div><div><dt>题型</dt><dd>{{ question.question_type }}</dd></div><div><dt>访问次数</dt><dd>{{ question.visit_count }}</dd></div><div><dt>最终答案</dt><dd>{{ question.final_answer || '—' }}</dd></div><div><dt>是否正确</dt><dd><span :class="question.is_correct === true ? 'kt-correct' : question.is_correct === false ? 'kt-incorrect' : ''">{{ question.is_correct === null ? '未评分' : question.is_correct ? '是' : '否' }}</span></dd></div>
    </dl>
  </section>
</template>
