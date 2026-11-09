<script setup lang="ts">
import { toDisplayString } from 'vue'
import QuestionGroupContent from './QuestionGroupContent.vue'
import QuestionContent from './QuestionContent.vue'
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
  <section v-if="scoreProgress || groups?.length" class="kt-group-results" aria-label="题目结果">
    <dl v-if="scoreProgress" data-score-progress class="kt-group-results__summary"><div><dt>得分 / 卷面满分</dt><dd>{{ scoreProgress.earned }} / {{ scoreProgress.paper_maximum }}</dd></div><div><dt>已评分满分</dt><dd>{{ scoreProgress.graded_maximum }}</dd></div><div><dt>待评小题</dt><dd>{{ scoreProgress.pending_count }}</dd></div></dl>
    <article v-for="group in groups" :key="group.group_id" class="kt-group-results__group">
      <QuestionGroupContent :group="group" :responses-by-item-key="responses(group)" readonly />
      <p v-if="group.score_progress" class="kt-group-results__score" :data-group-score="group.group_id">本题得分 {{ group.score_progress.earned }} / {{ group.score_progress.paper_maximum }}<span>已评分满分 {{ group.score_progress.graded_maximum }} · 待评 {{ group.score_progress.pending_count }}</span></p>
      <details open><summary>作答项明细</summary><div class="kt-table-scroll"><table class="kt-table"><thead><tr><th>作答项</th><th>你的答案</th><th>结果</th><th>得分</th><th>反馈</th></tr></thead><tbody><tr v-for="member in group.members" :key="member.item_key"><td>{{ member.group_order }}</td><td><QuestionContent :text="toDisplayString(member.submitted_answer ?? member.final_answer ?? member.response_value ?? '未答')" :images="{ ...group.images, ...member.images }" /></td><td>{{ member.is_correct === true ? '正确' : member.is_correct === false ? '错误' : '未提供判定' }}</td><td>{{ score(member) }}</td><td><template v-if="'correct_answer' in member">标准答案：<QuestionContent :text="toDisplayString(member.correct_answer)" :images="{ ...group.images, ...member.images }" /><p v-if="member.explanation"><QuestionContent :text="member.explanation" :images="{ ...group.images, ...member.images }" /></p></template></td></tr></tbody></table></div></details>
    </article>
  </section>
</template>
<style scoped>
.kt-group-results { margin-block: 1rem; min-width: 0; }
.kt-group-results__summary { display: flex; flex-wrap: wrap; gap: 28px 40px; align-items: center; margin: 0 0 24px; padding: 28px 30px; background: var(--kt-surface); border: 1px solid var(--kt-border); border-radius: 10px; }
.kt-group-results__summary dt { color: var(--kt-muted); font-size: .875rem; }
.kt-group-results__summary dd { color: var(--kt-brand-900); margin: 10px 0 0; font-size: 30px; font-weight: 500; font-variant-numeric: tabular-nums; }
.kt-group-results__group { margin-bottom: 1rem; border: 1px solid var(--kt-border); border-radius: 10px; background: var(--kt-surface); overflow: hidden; }
.kt-group-results__group > details { padding: 22px 24px; border-top: 1px solid var(--kt-border); }
.kt-group-results__score { display: flex; flex-wrap: wrap; gap: .5rem 1.5rem; margin: 0; padding: 1rem; font-variant-numeric: tabular-nums; }
.kt-group-results__score span { font-size: .875rem; color: var(--kt-muted); }
.kt-group-results__summary > div:first-child dd { font-size: 52px; font-weight: 400; line-height: 1.1; }
.kt-group-results :deep(.kt-table) { border-collapse: collapse; width: 100%; font-size: 15px; }
.kt-group-results :deep(.kt-table th) { padding: 12px 16px; font-size: 14px; font-weight: 500; color: var(--kt-muted); background: #fafbfd; text-align: left; }
.kt-group-results :deep(.kt-table td) { padding: 14px 16px; border-bottom: 1px solid var(--kt-border); overflow-wrap: anywhere; }
@media (max-width: 760px) { .kt-group-results__summary { padding: 24px; gap: 22px; } .kt-group-results__summary > div:first-child dd { font-size: 44px; } .kt-group-results__group > details { padding: 20px; } }
</style>
