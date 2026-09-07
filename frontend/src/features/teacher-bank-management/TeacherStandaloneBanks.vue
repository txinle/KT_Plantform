<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'

import { ApiError } from '@/api/client'
import Alert from '@/design-system/components/Alert.vue'
import EmptyState from '@/design-system/components/EmptyState.vue'
import FormField from '@/design-system/components/FormField.vue'
import PageHeader from '@/design-system/components/PageHeader.vue'
import StatusBadge from '@/design-system/components/StatusBadge.vue'

import {
  getTeacherStandaloneQuestionBankApi,
  isStandaloneCanonicalRedirect,
  type StandaloneBankCollection,
} from './standalone-api'

const api = getTeacherStandaloneQuestionBankApi()
const router = useRouter()
const collection = ref<StandaloneBankCollection>()
const draft = reactive({ name: '', description: '', visibility: 'private' })
const fieldErrors = ref<Record<string, string>>({})
const error = ref('')
const saving = ref(false)
const copyingId = ref<number>()

function setError(caught: unknown, fallback: string) {
  error.value = caught instanceof Error ? caught.message : fallback
  fieldErrors.value = caught instanceof ApiError
    ? Object.fromEntries(Object.entries(caught.fields).map(([key, value]) => [key, Array.isArray(value) ? String(value[0]) : String(value)]))
    : {}
}

async function load() {
  error.value = ''
  try {
    const response = await api.banks()
    if (isStandaloneCanonicalRedirect(response)) {
      await router.replace(response.redirect_url)
      return
    }
    collection.value = response
  } catch (caught) {
    setError(caught, '独立题库加载失败。')
  }
}

async function create() {
  saving.value = true
  error.value = ''
  fieldErrors.value = {}
  try {
    const bank = await api.createBank({ ...draft })
    await router.push(bank.detail_url)
  } catch (caught) {
    setError(caught, '创建题库失败。')
  } finally {
    saving.value = false
  }
}

async function copy(sourceBankId: number) {
  copyingId.value = sourceBankId
  error.value = ''
  try {
    const bank = await api.copySharedBank(sourceBankId)
    await router.push(bank.detail_url)
  } catch (caught) {
    setError(caught, '复制共享题库失败。')
  } finally {
    copyingId.value = undefined
  }
}

onMounted(load)
</script>

<template>
  <Alert v-if="error && !collection" tone="danger">{{ error }}</Alert>
  <template v-else-if="collection">
    <PageHeader title="独立题库">
      <template #kicker>未绑定课程</template>
      <p>这些题库不具备课程上下文。创建后仍可编辑题目和复制共享题库；课程 KC 映射仅在课程题库中维护。</p>
    </PageHeader>
    <Alert v-if="error" tone="danger">{{ error }}</Alert>

    <section class="kt-card kt-form-stack" aria-labelledby="standalone-bank-create-title">
      <h2 id="standalone-bank-create-title">创建题库</h2>
      <form novalidate @submit.prevent="create">
        <FormField for-id="standalone-bank-name" label="题库名称" :error="fieldErrors.name">
          <input id="standalone-bank-name" v-model="draft.name" maxlength="160" required>
        </FormField>
        <FormField for-id="standalone-bank-description" label="说明" :error="fieldErrors.description">
          <textarea id="standalone-bank-description" v-model="draft.description" rows="4" />
        </FormField>
        <FormField for-id="standalone-bank-visibility" label="可见性" :error="fieldErrors.visibility">
          <select id="standalone-bank-visibility" v-model="draft.visibility">
            <option v-for="choice in collection.visibility_choices" :key="choice.value" :value="choice.value">
              {{ choice.label }}
            </option>
          </select>
        </FormField>
        <button class="kt-button" type="submit" :disabled="saving">
          {{ saving ? '正在创建…' : '创建题库' }}
        </button>
      </form>
    </section>

    <section class="kt-section" aria-labelledby="standalone-bank-list-title">
      <div class="kt-section-heading">
        <h2 id="standalone-bank-list-title">我的题库</h2>
        <span>{{ collection.banks.length }} 个</span>
      </div>
      <EmptyState v-if="!collection.banks.length" title="还没有独立题库">
        <p>创建题库或复制共享题库后，它会显示在这里。</p>
      </EmptyState>
      <div v-else class="kt-card kt-table-card">
        <div class="kt-table-scroll">
          <table class="kt-table">
            <thead>
              <tr><th>题库</th><th>题目数</th><th>可见性</th><th>操作</th></tr>
            </thead>
            <tbody>
              <tr v-for="bank in collection.banks" :key="bank.id">
                <td><RouterLink :to="bank.detail_url"><strong>{{ bank.name }}</strong></RouterLink><small>{{ bank.description || '未填写说明' }}</small></td>
                <td>{{ bank.question_count }}</td>
                <td><StatusBadge :label="bank.visibility_label" :tone="bank.visibility === 'shared' ? 'success' : 'neutral'" /></td>
                <td><RouterLink :to="bank.detail_url">管理</RouterLink></td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </section>

    <section class="kt-section" aria-labelledby="standalone-shared-bank-title">
      <div class="kt-section-heading">
        <h2 id="standalone-shared-bank-title">共享题库</h2>
        <span>{{ collection.shared_banks.length }} 个</span>
      </div>
      <EmptyState v-if="!collection.shared_banks.length" title="没有可复制的共享题库">
        <p>其他教师共享的题库会显示在这里。</p>
      </EmptyState>
      <div v-else class="kt-card kt-table-card">
        <div class="kt-table-scroll">
          <table class="kt-table">
            <thead>
              <tr><th>题库</th><th>创建者</th><th>题目数</th><th>操作</th></tr>
            </thead>
            <tbody>
              <tr v-for="bank in collection.shared_banks" :key="bank.id">
                <td><strong>{{ bank.name }}</strong><small>{{ bank.description || '未填写说明' }}</small></td>
                <td>{{ bank.created_by_name }}</td>
                <td>{{ bank.question_count }}</td>
                <td><button class="kt-button kt-button--secondary" type="button" :disabled="copyingId === bank.id" @click="copy(bank.id)">{{ copyingId === bank.id ? '正在复制…' : '复制到我的题库' }}</button></td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </section>
  </template>
  <p v-else role="status">正在加载独立题库…</p>
</template>
