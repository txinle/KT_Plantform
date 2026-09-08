<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import Alert from '@/design-system/components/Alert.vue'
import PageHeader from '@/design-system/components/PageHeader.vue'

import { getTeacherStandaloneQuestionBankApi, type StandaloneQuestionImportPreview } from './standalone-api'

const route = useRoute()
const router = useRouter()
const api = getTeacherStandaloneQuestionBankApi()
const bankId = Number(route.params.bankId)
const fileInput = ref<HTMLInputElement>()
const preview = ref<StandaloneQuestionImportPreview>()
const error = ref('')
const busy = ref(false)

function classificationLabel(row: StandaloneQuestionImportPreview['preview']['rows'][number]) {
  return row.classification === 'draft' ? '保存为草稿' : '导入后可用'
}

async function selectFile(event: Event) {
  const input = event.currentTarget as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  busy.value = true
  error.value = ''
  try {
    preview.value = await api.previewQuestionImport(bankId, file)
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '题目文件校验失败。'
  } finally {
    busy.value = false
    input.value = ''
  }
}

async function confirm() {
  if (!preview.value) return
  busy.value = true
  error.value = ''
  try {
    const result = await api.confirmQuestionImport(bankId, preview.value.preview_id)
    await router.push(result.detail_url)
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '题目导入确认失败，请重新生成预览。'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <PageHeader title="导入题目文件">
    <template #kicker>独立题库 {{ bankId }}</template>
    <p>上传后只生成预览；确认导入前不会写入任何题目。</p>
    <template #actions><RouterLink class="kt-button kt-button--secondary" :to="`/teacher/banks/${bankId}/`">返回题库</RouterLink></template>
  </PageHeader>
  <Alert v-if="error" tone="danger">{{ error }}</Alert>
  <section class="kt-card">
    <h2>上传题目文件</h2>
    <p>预览将检查题型、答案格式和重复序号。</p>
    <div class="kt-inline-actions">
      <a class="kt-button kt-button--secondary" href="/teacher/import-templates/questions.csv" download="question_import_template.csv">下载题目模板</a>
      <button class="kt-button" type="button" :disabled="busy" @click="fileInput?.click()">{{ busy ? '正在校验…' : '选择文件并预览' }}</button>
      <input ref="fileInput" class="kt-visually-hidden" type="file" accept=".csv,.xlsx" @change="selectFile">
    </div>
  </section>
  <section v-if="preview" class="kt-card" role="dialog" aria-modal="true" aria-label="题目导入预览">
    <h2>导入预览</h2>
    <p>确认后将导入 {{ preview.preview.rows.length }} 道题；服务器会在事务中再次验证序号是否已被占用。</p>
    <div class="kt-table-scroll">
      <table class="kt-table">
        <thead><tr><th>序号</th><th>题目</th><th>题型</th><th>选项</th><th>难度</th><th>保存结果</th></tr></thead>
        <tbody><tr v-for="row in preview.preview.rows" :key="row.sequence"><td>{{ row.sequence }}</td><td>{{ row.text || '未填写题干' }}</td><td>{{ row.question_type_label }}</td><td>{{ row.option_count }}</td><td>{{ row.teacher_difficulty || '未设置' }}</td><td><strong>{{ classificationLabel(row) }}</strong><ul v-if="row.draft_reasons?.length" class="kt-draft-reasons"><li v-for="reason in row.draft_reasons" :key="reason">{{ reason }}</li></ul></td></tr></tbody>
      </table>
    </div>
    <div class="kt-inline-actions">
      <button class="kt-button" data-confirm-standalone-import type="button" :disabled="busy" @click="confirm">确认导入</button>
      <button class="kt-button kt-button--secondary" type="button" :disabled="busy" @click="preview = undefined">取消</button>
    </div>
  </section>
</template>
