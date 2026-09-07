<script setup lang="ts">
import { computed } from 'vue'
import Icon from '@/design-system/Icon.vue'
import Alert from '@/design-system/components/Alert.vue'
import type { TeacherClassCenter } from './api'

const props = defineProps<{ center: TeacherClassCenter }>()
const participation = computed(() => [
  { key: 'completed', label: '已完成', count: props.center.participation.completed },
  { key: 'in_progress', label: '进行中', count: props.center.participation.in_progress },
  { key: 'not_participated', label: '未参与', count: props.center.participation.not_participated },
])
const participationTotal = computed(() => participation.value.reduce((sum, row) => sum + row.count, 0))
const distributionTotal = computed(() => props.center.correct_rate_distribution.reduce((sum, row) => sum + row.count, 0))
const distribution = computed(() => {
  let offset = 0
  return props.center.correct_rate_distribution.map((row, index) => {
    const percent = distributionTotal.value ? row.count / distributionTotal.value * 100 : 0
    const result = { ...row, percent, offset, color: row.key === 'no_data' ? 'var(--kt-chart-missing)' : `var(--kt-chart-bin-${index})` }
    offset += percent
    return result
  })
})
const percent = (count: number, total: number) => total ? `${(count / total * 100).toFixed(1)}%` : '—'
const width = (ratio: string | null) => ratio === null ? 0 : Number(ratio) * 100
const units = (value: string) => Number(value).toLocaleString('zh-CN')
</script>

<template>
  <div class="kt-class-dashboard">
    <section class="kt-dashboard-facts" aria-label="班级概览">
      <article class="kt-card kt-dashboard-fact"><span class="kt-dashboard-icon"><Icon name="classes" :size="26" /></span><div><span>班级学生</span><p><strong>{{ center.overview.roster_count }}</strong> 人</p></div></article>
      <article class="kt-card kt-dashboard-fact"><span class="kt-dashboard-icon"><Icon name="user" :size="26" /></span><div><span>参与学生</span><p><strong>{{ center.overview.participated_count }}</strong> 人</p></div></article>
      <article class="kt-card kt-dashboard-accuracy" data-accuracy-ring>
        <div class="kt-dashboard-ring kt-dashboard-ring--small"><svg viewBox="0 0 120 120" aria-hidden="true"><circle cx="60" cy="60" r="48" /><circle v-if="center.overview.correct_rate.ratio !== null" data-ring-value cx="60" cy="60" r="48" pathLength="100" :stroke-dasharray="`${width(center.overview.correct_rate.ratio)} 100`" class="kt-dashboard-ring__value" /></svg><strong>{{ center.overview.correct_rate.percent_display }}</strong></div>
        <div><span>总体正确率</span><p class="kt-muted">{{ center.overview.correct_rate.ratio === null ? '学生形成有效评分后显示' : '按有效正误判定计算' }}</p></div>
      </article>
      <article class="kt-card kt-dashboard-completion"><div class="kt-dashboard-fact"><span class="kt-dashboard-icon"><Icon name="check" :size="26" /></span><div><span>实验完成率</span><p><strong>{{ center.overview.completion_rate.percent_display }}</strong></p></div></div><div class="kt-dashboard-track" aria-hidden="true"><i :style="{ width: `${width(center.overview.completion_rate.ratio)}%` }" /></div><p data-completion-units>{{ units(center.overview.completion_rate.numerator) }} / {{ units(center.overview.completion_rate.denominator) }} 次完成</p><small>按学生 × 实验统计</small></article>
    </section>
    <Alert v-if="center.overview.correct_rate.state === 'no_data'" tone="info">暂无可计算的正确率；学生形成有效评分后显示。</Alert>
    <div class="kt-dashboard-analysis">
      <section class="kt-card" aria-labelledby="participation-title">
        <div class="kt-dashboard-heading"><span class="kt-dashboard-icon"><Icon name="classes" /></span><div><h2 id="participation-title">参与情况</h2><p>学生参与实验的完成情况</p></div><span class="kt-dashboard-total">总人数 {{ participationTotal }}</span></div>
        <dl class="kt-dashboard-participation"><div v-for="row in participation" :key="row.key"><dt><i class="kt-dashboard-dot" :class="`is-${row.key}`" />{{ row.label }}</dt><dd>{{ row.count }} 人</dd><div class="kt-dashboard-track" aria-hidden="true"><i :class="`is-${row.key}`" :style="{ width: `${participationTotal ? row.count / participationTotal * 100 : 0}%` }" /></div><dd>{{ percent(row.count, participationTotal) }}</dd></div></dl>
      </section>
      <section class="kt-card" aria-labelledby="distribution-title">
        <div class="kt-dashboard-heading"><span class="kt-dashboard-icon"><Icon name="analysis" /></span><div><h2 id="distribution-title">正确率分布</h2><p>按学生正确率分组，包含无数据学生</p></div></div>
        <div class="kt-dashboard-distribution">
          <div class="kt-dashboard-ring" data-distribution-ring><svg viewBox="0 0 120 120" aria-hidden="true"><circle cx="60" cy="60" r="48" /><template v-for="row in distribution" :key="row.key"><circle v-if="row.count > 0" data-sector cx="60" cy="60" r="48" pathLength="100" :stroke-dasharray="`${row.percent} ${100 - row.percent}`" :stroke-dashoffset="-row.offset" :style="{ stroke: row.color }" /></template></svg><div><strong data-distribution-total>{{ distributionTotal }}</strong><span>统计人数</span></div></div>
          <dl class="kt-dashboard-legend"><div v-for="row in distribution" :key="row.key" :data-bin="row.key"><dt><i class="kt-dashboard-dot" :style="{ background: row.color }" />{{ row.label }}</dt><dd>{{ row.count }} 人</dd><dd>{{ percent(row.count, distributionTotal) }}</dd></div></dl>
        </div>
      </section>
    </div>
    <section class="kt-card" aria-labelledby="kc-title">
      <div class="kt-dashboard-heading"><span class="kt-dashboard-icon"><Icon name="bank" /></span><div><h2 id="kc-title">知识点作答正确率</h2><p>按现有知识点统计展示有效作答结果</p></div></div>
      <div v-if="!center.kc_rows.length" class="kt-dashboard-empty"><Icon name="analysis" :size="36" /><strong>暂无知识点统计</strong><p>形成有效评分后按事实显示。</p></div>
      <dl v-else class="kt-dashboard-kcs"><div v-for="(row, index) in center.kc_rows" :key="index"><dt>{{ row.name }}</dt><dd><div class="kt-dashboard-track" aria-hidden="true"><i :style="{ width: `${width(row.correct_rate.ratio)}%` }" /></div><span>{{ row.correct_rate.percent_display }}</span></dd></div></dl>
    </section>
  </div>
</template>

<style scoped>
.kt-class-dashboard { --kt-chart-bin-0: #e98991; --kt-chart-bin-1: #eeb66b; --kt-chart-bin-2: #dcc86a; --kt-chart-bin-3: #9cb5f9; --kt-chart-bin-4: #7772fb; --kt-chart-missing: #d4d4e4; display: grid; gap: 20px; }
.kt-dashboard-facts { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 16px; }
.kt-dashboard-facts > .kt-card { min-width: 0; }
.kt-dashboard-fact { display: flex; align-items: center; gap: 16px; }
.kt-dashboard-icon { display: inline-flex; flex: none; align-items: center; justify-content: center; width: 40px; height: 40px; border-radius: 8px; background: #f0efff; color: var(--kt-action-600); }
.kt-dashboard-fact p { margin: 8px 0 0; color: var(--kt-muted); }
.kt-dashboard-fact strong { color: var(--kt-ink); font-size: 28px; font-variant-numeric: tabular-nums; }
.kt-dashboard-accuracy { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; }
.kt-dashboard-accuracy > div:last-child { flex: 1; min-width: 90px; }
.kt-dashboard-accuracy p, .kt-dashboard-completion p, .kt-dashboard-completion small { color: var(--kt-muted); font-size: 14px; }
.kt-dashboard-completion > p { text-align: right; margin: 8px 0 4px; }
.kt-dashboard-completion > .kt-dashboard-track { margin-top: 12px; }
.kt-dashboard-track { height: 12px; min-width: 0; background: #e9e9ef; border-radius: 6px; overflow: hidden; }
.kt-dashboard-track i { display: block; height: 100%; background: var(--kt-action-600); border-radius: inherit; }
.kt-dashboard-ring { position: relative; flex: none; width: 190px; height: 190px; }
.kt-dashboard-ring--small { width: 88px; height: 88px; }
.kt-dashboard-ring svg { width: 100%; height: 100%; transform: rotate(-90deg); }
.kt-dashboard-ring circle { fill: none; stroke: #e9e9ef; stroke-width: 16; }
.kt-dashboard-ring .kt-dashboard-ring__value { stroke: var(--kt-action-600); }
.kt-dashboard-ring > strong, .kt-dashboard-ring > div { position: absolute; inset: 0; display: flex; flex-direction: column; gap: 8px; align-items: center; justify-content: center; }
.kt-dashboard-ring > div strong { font-size: 28px; }
.kt-dashboard-ring span { font-size: 14px; color: var(--kt-muted); }
.kt-dashboard-analysis { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 20px; }
.kt-dashboard-analysis > section { min-width: 0; }
.kt-dashboard-heading { display: flex; align-items: start; gap: 12px; margin-bottom: 24px; }
.kt-dashboard-heading h2 { font-size: 18px; margin: 0; }
.kt-dashboard-heading p { margin: 6px 0 0; color: var(--kt-muted); font-size: 14px; }
.kt-dashboard-total { margin-left: auto; flex: none; font-size: 14px; }
.kt-dashboard-dot { display: inline-block; width: 12px; height: 12px; border-radius: 50%; flex: none; background: var(--kt-action-600); }
.kt-dashboard-dot.is-in_progress, .kt-dashboard-track i.is-in_progress { background: #9cb5f9; }
.kt-dashboard-dot.is-not_participated, .kt-dashboard-track i.is-not_participated { background: var(--kt-chart-missing); }
.kt-dashboard-participation, .kt-dashboard-legend, .kt-dashboard-kcs { margin: 0; }
.kt-dashboard-participation > div { display: grid; grid-template-columns: 88px 44px minmax(40px, 1fr) 60px; align-items: center; gap: 12px; padding: 20px 0; border-bottom: 1px solid var(--kt-border); }
.kt-dashboard-participation > div:last-child { border: 0; }
.kt-dashboard-participation dt, .kt-dashboard-legend dt { display: flex; align-items: center; gap: 8px; }
.kt-dashboard-participation dd, .kt-dashboard-legend dd { margin: 0; text-align: right; font-variant-numeric: tabular-nums; font-size: 14px; }
.kt-dashboard-distribution { display: flex; align-items: center; gap: 24px; }
.kt-dashboard-legend { flex: 1; min-width: 0; }
.kt-dashboard-legend > div { display: grid; grid-template-columns: minmax(85px, 1fr) auto 54px; gap: 12px; align-items: center; margin: 14px 0; font-size: 14px; }
.kt-dashboard-empty { display: grid; justify-items: center; gap: 12px; padding: 32px 16px; color: var(--kt-muted); background: var(--kt-surface-subtle); border-radius: 8px; }
.kt-dashboard-empty p { margin: 0; }
.kt-dashboard-kcs > div { padding: 12px 0; display: grid; grid-template-columns: minmax(140px, 1fr) minmax(0, 2fr); gap: 24px; }
.kt-dashboard-kcs dt { overflow-wrap: anywhere; }
.kt-dashboard-kcs dd { display: grid; grid-template-columns: minmax(0, 1fr) 65px; align-items: center; gap: 12px; margin: 0; }
.kt-dashboard-kcs span { text-align: right; }
@media (max-width: 1279px) { .kt-dashboard-facts { grid-template-columns: repeat(2, minmax(0, 1fr)); } .kt-dashboard-distribution { flex-direction: column; } .kt-dashboard-legend { width: 100%; } }
@media (max-width: 1023px) { .kt-dashboard-analysis { grid-template-columns: minmax(0, 1fr); } }
@media (max-width: 599px) {
  .kt-dashboard-facts { grid-template-columns: minmax(0, 1fr); }
  .kt-dashboard-heading { flex-wrap: wrap; }
  .kt-dashboard-participation > div { grid-template-columns: minmax(75px, 1fr) auto 60px; gap: 10px; }
  .kt-dashboard-participation .kt-dashboard-track { grid-column: 1 / -1; grid-row: 2; }
  .kt-dashboard-legend > div { gap: 8px; }
  .kt-dashboard-kcs > div { grid-template-columns: minmax(0, 1fr); gap: 12px; }
}
</style>
