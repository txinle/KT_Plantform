<script setup lang="ts">
import type { ResearchTools } from './research-tools'
const model = defineModel<ResearchTools>({ required: true })
function addHint() {
  model.value.hints.push({ id: crypto.randomUUID(), question_code: '', level: 1, text: '', contains_answer: false })
}
function addRule() {
  model.value.check_enabled = true
  model.value.rules.push({ id: crypto.randomUUID(), tool: 'check', trigger: 'item_enter', question_code: '',
    after_seconds: 0, max_exposures: 1, required: false, hint_id: '' })
}
</script>

<template>
  <details class="kt-card kt-collapsible-step">
    <summary>可选研究工具</summary>
    <div class="kt-collapsible-step__body">
      <p>发布后配置冻结。启用工具不会默认要求学生使用。</p>
      <label><input v-model="model.doubt_enabled" type="checkbox">提供疑点标记</label>
      <label><input v-model="model.check_enabled" type="checkbox">提供待检查标记与检查操作</label>
      <label><input v-model="model.kc_visible" type="checkbox">向学生显示 KC 标签</label>
      <fieldset v-for="(hint, index) in model.hints" :key="hint.id" class="kt-form-grid">
        <legend>固定提示 {{ index + 1 }}</legend>
        <label>题目编号<input v-model="hint.question_code" required></label>
        <label>提示级别<input v-model.number="hint.level" type="number" min="1" required></label>
        <label>提示正文<textarea v-model="hint.text" rows="3" required /></label>
        <label><input v-model="hint.contains_answer" type="checkbox">包含正确答案或完整解答（受反馈权限限制）</label>
        <button class="kt-button kt-button--secondary" type="button" @click="model.hints.splice(index, 1)">移除此提示</button>
      </fieldset>
      <button class="kt-button kt-button--secondary" type="button" @click="addHint">添加固定提示</button>
      <fieldset v-for="(rule, index) in model.rules" :key="rule.id" class="kt-form-grid">
        <legend>出现规则 {{ index + 1 }}</legend>
        <label>工具<select v-model="rule.tool"><option value="check">检查提醒</option><option value="confidence">已分配的信心评价</option><option value="hint">固定提示</option></select></label>
        <label>出现节点<select v-model="rule.trigger"><option value="item_enter">进入指定题目</option><option value="elapsed_seconds">开始实验后固定时间</option></select></label>
        <label>题目编号（留空适用所有题目）<input v-model="rule.question_code"></label>
        <label v-if="rule.trigger === 'elapsed_seconds'">开始后秒数<input v-model.number="rule.after_seconds" type="number" min="0"></label>
        <label>最多出现次数<input v-model.number="rule.max_exposures" type="number" min="1"></label>
        <label v-if="rule.tool === 'hint'">提示<select v-model="rule.hint_id" required><option value="" disabled>选择提示</option><option v-for="hint in model.hints" :key="hint.id" :value="hint.id">{{ hint.question_code }} · 第 {{ hint.level }} 级</option></select></label>
        <label><input v-model="rule.required" type="checkbox">完成后才能正式提交本题</label>
        <button class="kt-button kt-button--secondary" type="button" @click="model.rules.splice(index, 1)">移除此规则</button>
      </fieldset>
      <button class="kt-button kt-button--secondary" type="button" @click="addRule">添加出现规则</button>
    </div>
  </details>
</template>
