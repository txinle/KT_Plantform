<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import Alert from '@/design-system/components/Alert.vue'
import EmptyState from '@/design-system/components/EmptyState.vue'
import PageHeader from '@/design-system/components/PageHeader.vue'

import { downloadFile } from '@/api/download'

import { getTeacherAnalysisApi, type ExperimentSessionResolution } from './api'

const route = useRoute()
const api = getTeacherAnalysisApi()
const downloading = ref(false)
const courseId = Number(route.params.courseId)
const experimentId = Number(route.params.experimentId)
const sessionId = Number(route.params.sessionId)
const page = ref<ExperimentSessionResolution>()
const loading = ref(true)
const error = ref('')
const report = computed(() => page.value?.report)

async function load() {
  loading.value = true
  error.value = ''
  try {
    page.value = await api.experimentSessionResolution(courseId, experimentId, sessionId)
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '会话报告加载失败。'
  } finally {
    loading.value = false
  }
}

async function downloadWord() {
  if (downloading.value || !page.value?.actions.word_url) return
  downloading.value = true
  error.value = ''
  try { await downloadFile(page.value.actions.word_url, { method: 'POST' }) }
  catch (caught) { error.value = caught instanceof Error ? caught.message : '下载失败。' }
  finally { downloading.value = false }
}

onMounted(load)
</script>

<template>
  <PageHeader title="学生作答详情">
    <template #kicker>{{ report ? `v${report.version_number} · ${report.session_status}` : '会话报告' }}</template>
    <p>按该学生实际获分配的题目顺序展示；草稿不等同于正式提交。</p>
    <template #actions>
      <a v-if="page" class="kt-button kt-button--secondary" :href="page.actions.back_url">返回结果</a>
      <button v-if="page?.actions.word_url" class="kt-button" type="button" data-word-download :disabled="downloading" @click="downloadWord">下载 Word 报告</button>
    </template>
  </PageHeader>

  <Alert v-if="error" tone="danger">{{ error }}</Alert>
  <p v-if="loading" role="status">正在解析会话报告…</p>

  <template v-else-if="page">
    <Alert v-if="page.kind === 'canonical' && page.canonical_url" tone="info">该会话已解析到发布时冻结的班级任务。<a :href="page.canonical_url">进入学生实验事实</a></Alert>

    <template v-else-if="page.kind === 'legacy_report' && report">
      <section class="kt-card kt-session-section" aria-labelledby="session-identity-title">
        <div class="kt-section-heading"><h2 id="session-identity-title">会话身份与状态</h2></div>
        <dl class="kt-session-facts"><div><dt>学生姓名</dt><dd>{{ report.student_name }}</dd></div><div><dt>学号</dt><dd>{{ report.student_number }}</dd></div><div><dt>发布时班级</dt><dd>{{ report.class_snapshot_name }}</dd></div><div><dt>实验版本</dt><dd>v{{ report.version_number }}</dd></div><div><dt>会话状态</dt><dd>{{ report.session_status }}</dd></div></dl>
        <p><strong>实际题数：{{ report.question_count }}</strong></p>
      </section>

      <section class="kt-session-section" aria-labelledby="assigned-questions-title">
        <div class="kt-section-heading"><h2 id="assigned-questions-title">实际分配题目</h2><span>{{ report.question_count }} 题</span></div>
        <EmptyState v-if="!report.questions.length" title="该会话没有实际分配题目"><p>此会话的题目数量为 0，因此没有可展示的作答记录。</p></EmptyState>
        <article v-for="question in report.questions" v-else :key="question.assigned_sequence_index" class="kt-card kt-session-question">
          <div class="kt-section-heading"><h3>第 {{ question.assigned_sequence_index }} 题 · {{ question.stable_code }}</h3><span>{{ question.question_type_label }}</span></div>
          <section><h4>题干</h4><p class="kt-session-text">{{ question.text }}</p></section>
          <section><h4>题目选项</h4><ol v-if="question.options.length"><li v-for="option in question.options" :key="`${option.key}-${option.text}`" class="kt-session-text">{{ option.display }}</li></ol><p v-else>无</p></section>
          <dl class="kt-session-facts"><div><dt>学生答案</dt><dd class="kt-session-text">{{ question.student_answer }}</dd></div><div><dt>标准答案</dt><dd class="kt-session-text">{{ question.correct_answer }}</dd></div><div><dt>作答状态</dt><dd><strong>{{ question.answer_status }}</strong></dd></div></dl>
        </article>
      </section>
    </template>
  </template>
</template>

<style scoped>
.kt-session-section { margin-top: 1.25rem; }
.kt-session-question { margin-top: 1rem; }
.kt-session-facts { display: grid; gap: .6rem; margin: 0; }
.kt-session-facts > div { display: grid; gap: .25rem; grid-template-columns: minmax(8rem, 12rem) 1fr; }
.kt-session-facts dt { color: var(--kt-text-muted); }
.kt-session-facts dd { margin: 0; }
.kt-session-text { overflow-wrap: anywhere; white-space: pre-wrap; }
</style>
