<script setup lang="ts">
import { useExperimentPageUrl } from './experiment-page-url'
const pageUrl = useExperimentPageUrl()
import { computed, nextTick, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { getBootstrapContext } from '@/app/bootstrap'
import AppHeader from '@/design-system/components/AppHeader.vue'
import QuestionGroupContent from '@/question-content/QuestionGroupContent.vue'
import type { GroupActivationIntent, GroupAnswerChange } from '@/question-content/question-group-content'
import { getTeacherExperimentApi, type StudentPreviewUnit, type TeacherStudentPreview } from './api'
import { useQuestionLayout } from '@/assessment-runtime/question-layout'
import '@/assessment-runtime/student-assessment.css'

const route = useRoute()
const bootstrap = getBootstrapContext()
const courseId = Number(route.params.courseId)
const experimentId = Number(route.params.experimentId)
const paper = ref<TeacherStudentPreview>()
const loading = ref(true)
const error = ref('')
const index = ref(0)
const answers = ref<Record<string, unknown>>({})
const activeItemKey = ref<string | null>(null)
const finished = ref(false)
const completionHeading = ref<HTMLElement | null>(null)
const questionHeading = ref<HTMLElement | null>(null)
const { questionBody, questionTabs, questionNumbers, syncQuestionLayout } = useQuestionLayout(index)
const current = computed(() => paper.value?.units[index.value])
const question = computed(() => current.value?.question)
const answer = computed(() => question.value ? answers.value[question.value.item_key] : undefined)
const layout = computed(() => current.value?.kind === 'reading' ? 'reading' : current.value?.group ? 'passage' : question.value?.question_type === 'short_answer' ? 'writing' : 'compact')
const labels: Record<string, string> = { single_choice: '单选题', multiple_choice: '多选题', true_false: '判断题', numeric: '数值题', fill_blank: '填空题', short_answer: '简答题', reading: '阅读理解', words: '选词填空', cloze: '短文填空', translation: '英汉互译', essay: '作文' }
function hasAnswer(value: unknown) { return Array.isArray(value) ? value.length > 0 : value != null && String(value).trim() !== '' }
function complete(unit: StudentPreviewUnit) { return (unit.group?.members || (unit.question ? [unit.question] : [])).every(member => hasAnswer(answers.value[member.item_key])) }
const completedCount = computed(() => paper.value?.units.filter(complete).length || 0)
function go(next: number) {
  if (!paper.value || next < 0 || next >= paper.value.units.length) return
  index.value = next
  activeItemKey.value = null
}
function update(value: unknown) { if (question.value && !finished.value) answers.value[question.value.item_key] = value }
function toggle(key: string, checked: boolean) {
  const values = Array.isArray(answer.value) ? answer.value.map(String) : []
  update(checked ? [...values.filter(value => value !== key), key] : values.filter(value => value !== key))
}
function groupAnswer(change: GroupAnswerChange) { if (!finished.value) answers.value[change.itemKey] = change.answer }
function activate(intent: GroupActivationIntent) {
  activeItemKey.value = intent.itemKey
  if (intent.hasAnswer) groupAnswer({ itemKey: intent.itemKey, answer: intent.answer })
}
async function finish() {
  finished.value = true
  await nextTick()
  completionHeading.value?.focus()
}
async function reset() {
  answers.value = {}
  finished.value = false
  go(0)
  await nextTick()
  syncQuestionLayout()
  questionHeading.value?.focus()
}
onMounted(async () => {
  try { paper.value = await getTeacherExperimentApi().studentPreview(courseId, experimentId) }
  catch (caught) { error.value = caught instanceof Error ? caught.message : '模拟题目加载失败。' }
  finally { loading.value = false }
})
</script>

<template>
  <div class="kt-assessment-focus kt-student-preview">
    <AppHeader :user-name="bootstrap.user.display_name" :user-meta="bootstrap.user.username" :csrf-token="bootstrap.csrf_token" />
    <main aria-label="教师模拟学生作答">
      <div class="kt-preview-notice" role="status"><span><strong>教师模拟学生作答</strong> · 作答仅保存在当前页面，不生成正式提交，不进入研究统计。按无个人作答历史预览，不模拟班级计时、研究工具或反馈发布。</span><a class="kt-button kt-button--secondary" :href="pageUrl(`/teacher/courses/${courseId}/experiments/`)">返回实验</a></div>
      <p v-if="loading" role="status">正在加载模拟题目…</p>
      <p v-else-if="error" class="kt-alert kt-alert--danger" role="alert">{{ error }}</p>
      <section v-else-if="finished && paper" class="kt-preview-complete" data-preview-complete aria-labelledby="preview-complete-title">
        <h1 id="preview-complete-title" ref="completionHeading" tabindex="-1">模拟已结束</h1>
        <p>本次作答未提交，也不会计入学生或研究数据。</p>
        <p>已体验作答 {{ completedCount }} / {{ paper.units.length }} 道题。本次答案仅保留在当前页面，离开或重新体验后清空。</p>
        <div class="kt-preview-complete__actions">
          <a class="kt-button" data-preview-return :href="pageUrl(`/teacher/courses/${courseId}/experiments/`)">返回实验</a>
          <button class="kt-button kt-button--secondary" data-reset-preview type="button" @click="reset">重新体验</button>
        </div>
      </section>
      <div v-else-if="paper && current" class="kt-assessment-layout" :data-question-layout="layout">
        <header class="kt-assessment-topbar"><div><h1>{{ paper.experiment_name }}</h1><p>共 {{ paper.units.length }} 道题 · {{ paper.source === 'frozen' ? '已发布冻结内容' : '当前草稿内容' }} · 学生视角体验</p></div><button class="kt-button kt-button--secondary" data-reset-preview type="button" @click="reset">重新体验</button></header>
        <nav ref="questionTabs" class="kt-group-tabs" aria-label="模拟题目导航"><button v-for="(unit, position) in paper.units" :key="unit.key" type="button" :aria-current="position === index ? 'step' : undefined" @click="go(position)">{{ String(position + 1).padStart(2, '0') }} {{ labels[unit.kind] || '题目' }}</button></nav>
        <section class="kt-assessment-question" aria-labelledby="preview-question-title">
          <header class="kt-assessment-question__header"><h2 id="preview-question-title" ref="questionHeading" tabindex="-1">第 {{ index + 1 }} 题 · {{ labels[current.kind] || '题目' }}</h2><span class="kt-assessment-score">{{ current.maximum_score }} 分</span></header>
          <div ref="questionBody" class="kt-assessment-question-body" tabindex="0" role="region" aria-label="题目内容">
            <QuestionGroupContent v-if="current.group" :group="current.group" :responses-by-item-key="answers" :active-item-key="activeItemKey" :readonly="finished" @request-activate="activate" @answer-change="groupAnswer" />
            <div v-else-if="question" class="kt-assessment-article">
              <h2 v-if="question.title">{{ question.title }}</h2><p v-if="question.instructions" data-question-instructions>{{ question.instructions }}</p><p class="kt-assessment-stem">{{ question.text }}</p>
              <fieldset v-if="['single_choice', 'multiple_choice', 'true_false'].includes(question.question_type)"><legend class="kt-visually-hidden">请选择答案</legend><label v-for="option in question.options" :key="option.key" class="kt-assessment-option"><input name="preview-answer" :type="question.question_type === 'multiple_choice' ? 'checkbox' : 'radio'" :value="option.key" :disabled="finished" :checked="question.question_type === 'multiple_choice' ? Array.isArray(answer) && answer.includes(option.key) : answer === option.key" @change="question.question_type === 'multiple_choice' ? toggle(option.key, ($event.target as HTMLInputElement).checked) : update(option.key)"><strong>{{ option.key }}</strong><span>{{ option.text }}</span></label></fieldset>
              <label v-else class="kt-assessment-text-answer"><span>{{ question.educational_type === 'essay' ? '你的作文' : question.educational_type === 'translation' ? '你的译文' : '你的答案' }}</span><textarea v-if="question.question_type === 'short_answer'" :rows="question.educational_type === 'essay' ? 12 : question.educational_type === 'translation' ? 6 : 4" :value="String(answer ?? '')" :disabled="finished" @input="update(($event.target as HTMLTextAreaElement).value)" /><input v-else :inputmode="question.question_type === 'numeric' ? 'decimal' : undefined" :value="String(answer ?? '')" :disabled="finished" @input="update(($event.target as HTMLInputElement).value)"></label>
              <button class="kt-text-button kt-preview-clear" type="button" :disabled="finished || !hasAnswer(answer)" @click="update('')">清除作答</button>
            </div>
          </div>
          <footer class="kt-assessment-paper-actions"><span>第 {{ index + 1 }} / {{ paper.units.length }} 题</span><div><button class="kt-button kt-button--secondary" data-previous-question type="button" :disabled="index === 0" @click="go(index - 1)">上一题</button><button class="kt-button kt-button--secondary" data-next-question type="button" :disabled="index === paper.units.length - 1" @click="go(index + 1)">下一题 →</button></div></footer>
        </section>
        <aside class="kt-assessment-nav" aria-label="模拟答题卡"><section class="kt-assessment-nav-card"><div class="kt-assessment-nav__header"><h2>答题卡</h2><span>{{ completedCount }} / {{ paper.units.length }}</span></div><div class="kt-assessment-progress"><i :style="{ width: `${completedCount / paper.units.length * 100}%` }" /></div><p class="kt-assessment-nav-note">按完整题目统计作答进度</p><nav ref="questionNumbers" aria-label="答题卡题号"><button v-for="(unit, position) in paper.units" :key="unit.key" class="kt-assessment-nav__item" type="button" :data-answer-status="complete(unit) ? 'answered' : 'unvisited'" :aria-current="position === index ? 'step' : undefined" @click="go(position)">{{ position + 1 }}</button></nav></section><section class="kt-assessment-submit-panel"><p class="kt-assessment-save-status">模拟作答未提交到服务器</p><p class="kt-assessment-nav-note">刷新或离开页面会清空本次模拟；不会影响任何学生数据。</p><button class="kt-button" data-finish-preview type="button" :disabled="finished" @click="finish">结束模拟</button></section></aside>
      </div>
      <p v-else>当前没有可模拟的题目，请先完善实验定义。</p>
    </main>
  </div>
</template>

<style scoped>
.kt-preview-notice { display: flex; justify-content: space-between; align-items: center; gap: 20px; margin: 20px auto 0; width: 100%; max-width: 1440px; padding: 14px 18px; background: var(--kt-action-050); border: 1px solid var(--kt-border); border-radius: 8px; font-size: 14px; line-height: 1.7; }
.kt-preview-notice a { flex: none; }
.kt-preview-complete { max-width: 760px; width: 100%; margin: 32px auto; padding: 32px; border: 1px solid var(--kt-border); border-radius: 10px; background: var(--kt-surface); }
.kt-preview-complete h1 { margin: 0 0 20px; font-size: 24px; }
.kt-preview-complete p { color: var(--kt-muted); line-height: 1.8; }
.kt-preview-complete__actions { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 24px; }
@media (min-width: 1001px) and (min-height: 720px) {
  .kt-student-preview > main { display: flex; flex-direction: column; flex: 1; min-height: 0; }
  .kt-preview-notice { flex: none; }
}
.kt-preview-clear { margin-top: 16px; min-height: 44px; padding: 6px 12px; border: 0; background: transparent; color: var(--kt-action-600); font: inherit; cursor: pointer; }
.kt-preview-clear:disabled { color: var(--kt-muted); cursor: default; }
@media (max-width: 760px) { .kt-preview-notice { align-items: flex-start; flex-direction: column; gap: 10px; } }
</style>
