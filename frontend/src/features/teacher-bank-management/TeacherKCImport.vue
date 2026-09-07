<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import Alert from '@/design-system/components/Alert.vue'
import PageHeader from '@/design-system/components/PageHeader.vue'

import { getTeacherBankManagementApi, type KCImportPreview } from './api'

const route = useRoute()
const router = useRouter()
const api = getTeacherBankManagementApi()
const courseId = Number(route.params.courseId)
const bankId = Number(route.params.bankId)
const fileInput = ref<HTMLInputElement>()
const preview = ref<KCImportPreview>()
const error = ref('')
const busy = ref(false)

async function selectFile(event: Event) {
  const input = event.currentTarget as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  busy.value = true
  error.value = ''
  try {
    preview.value = await api.previewKCImport(courseId, bankId, file)
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
    await api.confirmKCImport(courseId, bankId, preview.value.batch_id)
    await router.push(`/teacher/courses/${courseId}/banks/${bankId}/`)
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : 'KC 导入确认失败，请重新生成预览。'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <PageHeader title="导入 KC 对应表"><template #kicker>题库 {{ bankId }}</template><p>上传后先检查预览，确认后写入当前题库；同一预览只需确认一次。</p><template #actions><RouterLink class="kt-button kt-button--secondary" :to="`/teacher/courses/${courseId}/banks/${bankId}/`">返回题库</RouterLink></template></PageHeader>
  <Alert v-if="error" tone="danger">{{ error }}</Alert>
  <section class="kt-card"><h2>上传 KC 文件</h2><p>支持四列 KC 树 CSV、旧版 KC 对应表以及三工作表 Excel 模板。请按对应模板填写题目序号和知识点。</p><div class="kt-inline-actions"><a class="kt-button kt-button--secondary" href="/teacher/import-templates/kcs.csv" download="kc_mapping_template.csv">下载 KC 模板</a><button class="kt-button" type="button" :disabled="busy" @click="fileInput?.click()">{{ busy ? '正在校验…' : '选择文件并预览' }}</button><input ref="fileInput" class="kt-visually-hidden" type="file" accept=".csv,.xlsx" @change="selectFile"></div></section>
  <section v-if="preview" class="kt-card" role="dialog" aria-modal="true" aria-label="KC 导入预览"><h2>KC 导入预览</h2><p v-if="preview.preview.mode === 'tree_v2'">将按每道题的三级 KC 树完整替换映射。</p><p v-else-if="preview.preview.mode === 'workbook'">将按稳定题号校验节点与关系后更新映射。</p><p v-else>将按主 KC 与次 KC 对应关系更新题目映射。</p><div v-if="preview.preview.trees" class="kt-table-scroll"><table class="kt-table"><thead><tr><th>题目序号</th><th>根 KC</th><th>节点数</th></tr></thead><tbody><tr v-for="tree in preview.preview.trees" :key="tree.sequence"><td>{{ tree.sequence }}</td><td>{{ tree.root_kc_code }}</td><td>{{ tree.nodes.length }}</td></tr></tbody></table></div><div v-else-if="preview.preview.rows" class="kt-table-scroll"><table class="kt-table"><thead><tr><th>题目序号</th><th>主 KC</th><th>次 KC</th></tr></thead><tbody><tr v-for="row in preview.preview.rows" :key="row.sequence"><td>{{ row.sequence }}</td><td>{{ row.primary }}</td><td>{{ row.secondary.join('、') || '无' }}</td></tr></tbody></table></div><div v-else-if="preview.preview.questions" class="kt-table-scroll"><table class="kt-table"><thead><tr><th>稳定题号</th><th>根 KC</th></tr></thead><tbody><tr v-for="row in preview.preview.questions" :key="row.stable_question_code"><td>{{ row.stable_question_code }}</td><td>{{ row.root_kc_code }}</td></tr></tbody></table></div><div class="kt-inline-actions"><button class="kt-button" type="button" :disabled="busy || !preview.preview.confirmable" @click="confirm">确认导入</button><button class="kt-button kt-button--secondary" type="button" :disabled="busy" @click="preview = undefined">取消</button></div></section>
</template>
