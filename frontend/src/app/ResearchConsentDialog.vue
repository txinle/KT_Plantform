<script setup lang="ts">
import { nextTick, onMounted, ref } from 'vue'

import {
  acceptResearchConsent,
  getResearchConsent,
  logout,
  type ResearchConsentDocument,
} from './auth'

const emit = defineEmits<{ accepted: [] }>()
const props = defineProps<{ navigateToLogin?: () => void }>()

const consent = ref<ResearchConsentDocument>()
const accepted = ref(false)
const loading = ref(true)
const submitting = ref(false)
const exiting = ref(false)
const error = ref('')
const checkbox = ref<HTMLInputElement>()

async function loadConsent() {
  loading.value = true
  error.value = ''
  try {
    consent.value = await getResearchConsent()
    await nextTick()
    checkbox.value?.focus()
  } catch {
    error.value = '说明加载失败，请检查网络后重试。'
  } finally {
    loading.value = false
  }
}

async function submit() {
  if (!accepted.value || !consent.value || submitting.value) return
  submitting.value = true
  error.value = ''
  try {
    await acceptResearchConsent(consent.value.version)
    emit('accepted')
  } catch {
    error.value = '同意记录保存失败，请检查网络后重试。'
  } finally {
    submitting.value = false
  }
}

async function exit() {
  if (exiting.value) return
  exiting.value = true
  error.value = ''
  try {
    await logout()
    if (props.navigateToLogin) props.navigateToLogin()
    else window.location.assign('/login/')
  } catch {
    error.value = '退出失败，请检查网络后重试。'
    exiting.value = false
  }
}

onMounted(loadConsent)
</script>

<template>
  <main class="kt-consent-gate">
    <section
      data-consent-dialog
      class="kt-consent-dialog"
      role="dialog"
      aria-modal="true"
      aria-labelledby="research-consent-title"
      aria-describedby="research-consent-body"
    >
      <header class="kt-consent-dialog__header">
        <span class="kt-consent-dialog__icon" aria-hidden="true">
          <svg viewBox="0 0 24 24" focusable="false">
            <path d="M7 3.5h7l4 4V20H7z" />
            <path d="M14 3.5V8h4M9.5 12h6M9.5 15.5h6" />
          </svg>
        </span>
        <h1 id="research-consent-title">
          {{ consent?.title || '研究参与说明与知情同意' }}
        </h1>
        <button
          data-consent-close
          class="kt-consent-dialog__close"
          type="button"
          aria-label="退出登录"
          :disabled="exiting"
          @click="exit"
        >
          ×
        </button>
      </header>

      <div id="research-consent-body" class="kt-consent-dialog__body">
        <p v-if="loading" class="kt-consent-dialog__status">正在加载研究参与说明…</p>
        <template v-else-if="consent">
          <p v-for="paragraph in consent.paragraphs" :key="paragraph">
            {{ paragraph }}
          </p>
          <p class="kt-consent-dialog__researcher">
            <strong>研究者：{{ consent.researcher_name }}</strong>
            （<a :href="`mailto:${consent.researcher_email}`">{{ consent.researcher_email }}</a>）
          </p>
        </template>
        <p v-if="error" class="kt-alert kt-alert--danger" role="alert">{{ error }}</p>
        <button
          v-if="!loading && !consent"
          class="kt-button kt-button--secondary"
          type="button"
          @click="loadConsent"
        >
          重新加载
        </button>
      </div>

      <template v-if="consent">
        <label class="kt-consent-dialog__choice">
          <input ref="checkbox" v-model="accepted" type="checkbox">
          <span>{{ consent.checkbox_label }}</span>
        </label>
        <footer class="kt-consent-dialog__actions">
          <button
            data-consent-exit
            class="kt-button kt-button--secondary"
            type="button"
            :disabled="exiting || submitting"
            @click="exit"
          >
            {{ exiting ? '正在退出…' : consent.exit_label }}
          </button>
          <button
            data-consent-accept
            class="kt-button"
            type="button"
            :disabled="!accepted || submitting || exiting"
            @click="submit"
          >
            {{ submitting ? '正在保存…' : consent.accept_label }}
          </button>
        </footer>
      </template>
    </section>
  </main>
</template>

<style scoped>
.kt-consent-gate {
  display: grid;
  min-height: 100dvh;
  place-items: center;
  padding: 24px;
  background: rgb(15 23 42 / 48%);
}

.kt-consent-dialog {
  width: min(100%, 600px);
  max-height: calc(100dvh - 48px);
  overflow: auto;
  border: 1px solid var(--kt-border);
  border-radius: 12px;
  background: var(--kt-surface);
  box-shadow: var(--kt-shadow-raised);
  color: var(--kt-ink);
}

.kt-consent-dialog__header {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 16px;
  padding: 28px 30px 18px;
}

.kt-consent-dialog__header h1 {
  margin: 0;
  font-size: 24px;
  letter-spacing: -0.02em;
}

.kt-consent-dialog__icon {
  display: grid;
  width: 48px;
  height: 48px;
  place-items: center;
  border-radius: 50%;
  background: var(--kt-action-050);
  color: var(--kt-action-600);
}

.kt-consent-dialog__icon svg {
  width: 27px;
  height: 27px;
  fill: none;
  stroke: currentColor;
  stroke-linecap: round;
  stroke-linejoin: round;
  stroke-width: 1.8;
}

.kt-consent-dialog__close {
  display: grid;
  width: 44px;
  height: 44px;
  place-items: center;
  border: 0;
  background: transparent;
  color: var(--kt-muted);
  cursor: pointer;
  font-size: 30px;
  line-height: 1;
}

.kt-consent-dialog__body {
  padding: 0 30px 20px;
  color: var(--kt-ink);
  line-height: 1.75;
}

.kt-consent-dialog__body p {
  margin-bottom: 12px;
}

.kt-consent-dialog__body p:last-child {
  margin-bottom: 0;
}

.kt-consent-dialog__researcher a {
  overflow-wrap: anywhere;
}

.kt-consent-dialog__status {
  color: var(--kt-muted);
}

.kt-consent-dialog__choice {
  display: grid;
  grid-template-columns: 22px minmax(0, 1fr);
  gap: 12px;
  align-items: start;
  margin: 0 30px;
  border-top: 1px solid var(--kt-border);
  padding: 20px 4px 18px;
  cursor: pointer;
  line-height: 1.55;
}

.kt-consent-dialog__choice input {
  width: 20px;
  height: 20px;
  margin: 2px 0 0;
  accent-color: var(--kt-action-600);
}

.kt-consent-dialog__actions {
  display: grid;
  grid-template-columns: 1fr 1.1fr;
  gap: 16px;
  padding: 0 30px 28px;
}

.kt-consent-dialog__actions .kt-button {
  min-height: 50px;
}

.kt-consent-dialog__actions .kt-button:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

@media (max-width: 480px) {
  .kt-consent-gate { align-items: stretch; padding: 0; }
  .kt-consent-dialog { max-height: 100dvh; border-radius: 0; }
  .kt-consent-dialog__header { gap: 10px; padding: 20px 18px 14px; }
  .kt-consent-dialog__header h1 { font-size: 20px; }
  .kt-consent-dialog__icon { width: 42px; height: 42px; }
  .kt-consent-dialog__body { padding: 0 18px 16px; }
  .kt-consent-dialog__choice { margin-inline: 18px; }
  .kt-consent-dialog__actions { padding: 0 18px 20px; }
}
</style>
