<script setup lang="ts">
import type { ResearchSession } from './api'
defineProps<{ session: ResearchSession }>()
function time(value: string) {
  const compact = value.match(/^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})/)
  if (compact) return compact[1] + '-' + compact[2] + '-' + compact[3] + ' ' + compact[4] + ':' + compact[5] + ':' + compact[6]
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value || '—' : new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).format(date).replaceAll('/', '-')
}
</script>
<template>
  <section class="kt-research-panel kt-session-summary">
    <dl>
      <div><dt>学生</dt><dd>{{ session.name }}</dd></div><div><dt>学号</dt><dd>{{ session.student_number }}</dd></div><div><dt>sessionID</dt><dd>{{ session.session_ref }}</dd></div><div><dt>实验名称</dt><dd>{{ session.experiment_name }}</dd></div><div><dt>提交时间</dt><dd>{{ time(session.submitted_at) }}</dd></div><div><dt>题目数</dt><dd>{{ session.question_count }}</dd></div><div><dt>行为记录</dt><dd>{{ session.behavior_count }}</dd></div>
    </dl>
  </section>
</template>
