<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { ApiError } from '@/api/client'
import Alert from '@/design-system/components/Alert.vue'
import FormField from '@/design-system/components/FormField.vue'
import PageHeader from '@/design-system/components/PageHeader.vue'

import { getTeacherBankManagementApi, type BankEditor } from './api'

const route = useRoute()
const router = useRouter()
const api = getTeacherBankManagementApi()
const courseId = Number(route.params.courseId)
const bankId = Number(route.params.bankId)
const editor = ref<BankEditor>()
const draft = reactive({ name: '', description: '', visibility: '' })
const fieldErrors = ref<Record<string, string>>({})
const error = ref('')
const saving = ref(false)

function setError(caught: unknown, fallback: string) {
  error.value = caught instanceof Error ? caught.message : fallback
  fieldErrors.value = caught instanceof ApiError
    ? Object.fromEntries(Object.entries(caught.fields).map(([key, value]) => [key, Array.isArray(value) ? String(value[0]) : String(value)]))
    : {}
}

onMounted(async () => {
  try {
    editor.value = await api.bankEditor(courseId, bankId)
    Object.assign(draft, {
      name: editor.value.name,
      description: editor.value.description,
      visibility: editor.value.visibility,
    })
  } catch (caught) {
    setError(caught, '题库编辑信息加载失败。')
  }
})

async function save() {
  saving.value = true
  error.value = ''
  fieldErrors.value = {}
  try {
    const saved = await api.updateBank(courseId, bankId, { ...draft })
    await router.push(saved.detail_url)
  } catch (caught) {
    setError(caught, '题库信息保存失败。')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <Alert v-if="error && !editor" tone="danger">{{ error }}</Alert>
  <template v-else-if="editor">
    <PageHeader title="编辑题库"><template #kicker>{{ editor.course.code }} · {{ editor.course.term }}</template><p>题目和 KC 映射保持不变；此处只修改题库名称、说明和可见性。</p><template #actions><RouterLink class="kt-button kt-button--secondary" :to="editor.detail_url">取消</RouterLink></template></PageHeader>
    <Alert v-if="error" tone="danger">{{ error }}</Alert>
    <form class="kt-card kt-form-stack" novalidate @submit.prevent="save">
      <FormField for-id="bank-name" label="题库名称" :error="fieldErrors.name"><input id="bank-name" v-model="draft.name" maxlength="160" required></FormField>
      <FormField for-id="bank-description" label="说明" :error="fieldErrors.description"><textarea id="bank-description" v-model="draft.description" rows="5" /></FormField>
      <FormField for-id="bank-visibility" label="可见性" :error="fieldErrors.visibility"><select id="bank-visibility" v-model="draft.visibility"><option v-for="choice in editor.visibility_choices" :key="choice.value" :value="choice.value">{{ choice.label }}</option></select></FormField>
      <div class="kt-inline-actions"><button class="kt-button" type="submit" :disabled="saving">{{ saving ? '正在保存…' : '保存题库信息' }}</button><RouterLink class="kt-button kt-button--secondary" :to="editor.detail_url">取消</RouterLink></div>
    </form>
  </template>
  <p v-else role="status">正在加载题库编辑信息…</p>
</template>
