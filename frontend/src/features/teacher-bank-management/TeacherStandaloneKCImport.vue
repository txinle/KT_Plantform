<script setup lang="ts">
import { ref } from 'vue'
import { useRoute } from 'vue-router'

import Alert from '@/design-system/components/Alert.vue'
import PageHeader from '@/design-system/components/PageHeader.vue'

import { getTeacherStandaloneQuestionBankApi, type StandaloneKCImportPreview } from './standalone-api'

const route = useRoute()
const api = getTeacherStandaloneQuestionBankApi()
const bankId = Number(route.params.bankId)
const fileInput = ref<HTMLInputElement>()
const preview = ref<StandaloneKCImportPreview>()
const error = ref('')
const busy = ref(false)

async function selectFile(event: Event) {
  const input = event.currentTarget as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  busy.value = true
  error.value = ''
  try {
    preview.value = await api.previewKCImport(bankId, file)
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : 'KC 文件校验失败。'
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
    await api.confirmKCImport(bankId, preview.value.preview_id)
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : 'KC 导入确认失败。'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <PageHeader title="导入 KC 对应表">
    <template #kicker>独立题库 {{ bankId }}</template>
    <p>可以上传文件检查预览；知识点映射导入需要课程题库，独立题库无法确认写入。</p>
    <template #actions><RouterLink class="kt-button kt-button--secondary" :to="`/teacher/banks/${bankId}/`">返回题库</RouterLink></template>
  </PageHeader>
  <Alert tone="warning">KC 对应表需要课程 KC 总库。请将题库放在课程中后再完成 KC 映射。</Alert>
  <Alert v-if="error" tone="danger">{{ error }}</Alert>
  <section class="kt-card">
    <h2>上传 KC 文件</h2>
    <p>预览检查文件内容与题目序号。要正式导入知识点映射，请进入课程题库。</p>
    <div class="kt-inline-actions">
      <a class="kt-button kt-button--secondary" href="/teacher/import-templates/kcs.csv" download="kc_mapping_template.csv">下载 KC 模板</a>
      <button class="kt-button" type="button" :disabled="busy" @click="fileInput?.click()">{{ busy ? '正在校验…' : '选择文件并预览' }}</button>
      <input ref="fileInput" class="kt-visually-hidden" type="file" accept=".csv,.xlsx" @change="selectFile">
    </div>
  </section>
  <section v-if="preview" class="kt-card" role="dialog" aria-modal="true" aria-label="KC 导入预览">
    <h2>KC 导入预览</h2>
    <div class="kt-table-scroll">
      <table class="kt-table">
        <thead><tr><th>题目序号</th><th>主 KC</th><th>次 KC</th></tr></thead>
        <tbody><tr v-for="row in preview.preview.rows" :key="row.sequence"><td>{{ row.sequence }}</td><td>{{ row.primary }}</td><td>{{ row.secondary.join('、') || '无' }}</td></tr></tbody>
      </table>
    </div>
    <div class="kt-inline-actions">
      <button class="kt-button" type="button" :disabled="busy" @click="confirm">确认导入</button>
      <button class="kt-button kt-button--secondary" type="button" :disabled="busy" @click="preview = undefined">取消</button>
    </div>
  </section>
</template>
