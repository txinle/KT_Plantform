<script setup lang="ts">
import QuestionGroupContent from './QuestionGroupContent.vue'
import type { ResultGroup, ResultMember, ScoreProgress } from './question-group-results'
defineProps<{ groups?: ResultGroup[]; scoreProgress?: ScoreProgress }>()
function responses(group: ResultGroup) { return Object.fromEntries(group.members.map(member => [member.item_key, member.response_value])) }
function score(member: ResultMember) {
  if (member.score === undefined) return '—'
  if (typeof member.score === 'object') return `${member.score.value} / ${member.score.maximum}`
  return `${member.score} / ${member.max_score ?? '—'}`
}
</script>
<template>
  <section v-if="scoreProgress || groups?.length" class="kt-group-results" aria-label="题组结果">
    <dl v-if="scoreProgress" data-score-progress class="kt-group-results__summary"><div><dt>得分 / 卷面满分</dt><dd>{{ scoreProgress.earned }} / {{ scoreProgress.paper_maximum }}</dd></div><div><dt>已评分满分</dt><dd>{{ scoreProgress.graded_maximum }}</dd></div><div><dt>待评小题</dt><dd>{{ scoreProgress.pending_count }}</dd></div></dl>
    <article v-for="group in groups" :key="group.group_id" class="kt-group-results__group">
      <QuestionGroupContent :group="group" :responses-by-item-key="responses(group)" readonly />
      <p v-if="group.score_progress" class="kt-group-results__score" :data-group-score="group.group_id">本题组得分 {{ group.score_progress.earned }} / {{ group.score_progress.paper_maximum }}<span>已评分满分 {{ group.score_progress.graded_maximum }} · 待评 {{ group.score_progress.pending_count }}</span></p>
      <details open><summary>小题明细</summary><div class="kt-table-scroll"><table class="kt-table"><thead><tr><th>小题</th><th>你的答案</th><th>结果</th><th>得分</th><th>反馈</th></tr></thead><tbody><tr v-for="member in group.members" :key="member.item_key"><td>{{ member.group_order }}</td><td>{{ member.submitted_answer ?? member.final_answer ?? member.response_value ?? '未答' }}</td><td>{{ member.is_correct === true ? '正确' : member.is_correct === false ? '错误' : '未提供判定' }}</td><td>{{ score(member) }}</td><td><template v-if="'correct_answer' in member">标准答案：{{ member.correct_answer }}<p v-if="member.explanation">{{ member.explanation }}</p></template></td></tr></tbody></table></div></details>
    </article>
  </section>
</template>
<style scoped>
.kt-group-results { margin-block: 1rem; min-width: 0; }
.kt-group-results__summary { display: flex; flex-wrap: wrap; gap: 1rem 2rem; margin: 0 0 1rem; padding: 1rem; background: var(--kt-surface); border: 1px solid var(--kt-border); border-radius: 10px; }
.kt-group-results__summary dt { color: var(--kt-muted); font-size: .875rem; }
.kt-group-results__summary dd { margin: .25rem 0 0; font-size: 1.5rem; font-variant-numeric: tabular-nums; }
.kt-group-results__group { margin-bottom: 1rem; border: 1px solid var(--kt-border); border-radius: 10px; background: var(--kt-surface); overflow: hidden; }
.kt-group-results__group > details { padding: 1rem; border-top: 1px solid var(--kt-border); }
.kt-group-results__score { display: flex; flex-wrap: wrap; gap: .5rem 1.5rem; margin: 0; padding: 1rem; font-variant-numeric: tabular-nums; }
.kt-group-results__score span { font-size: .875rem; color: var(--kt-muted); }
</style>
