<script setup lang="ts">
import { nextTick, onMounted, ref } from 'vue'

import { markInitialPasswordReminderShown } from './auth'

const visible = ref(true)
const dismissButton = ref<HTMLButtonElement>()

function dismiss() {
  visible.value = false
}

onMounted(async () => {
  await nextTick()
  dismissButton.value?.focus()
  void markInitialPasswordReminderShown().catch(() => undefined)
})
</script>

<template>
  <div v-if="visible" class="kt-initial-password-reminder" @keydown.esc="dismiss">
    <section
      class="kt-initial-password-reminder__dialog kt-card kt-card--raised"
      role="dialog"
      aria-modal="true"
      aria-labelledby="initial-password-reminder-title"
      aria-describedby="initial-password-reminder-description"
    >
      <p class="kt-kicker">账号安全</p>
      <h2 id="initial-password-reminder-title">建议修改初始密码</h2>
      <p id="initial-password-reminder-description">
        你目前仍在使用初始密码。建议改为仅由你本人掌握的密码；也可以暂不修改并继续使用平台。
      </p>
      <div class="kt-initial-password-reminder__actions">
        <button
          ref="dismissButton"
          class="kt-button kt-button--secondary"
          data-reminder-dismiss
          type="button"
          @click="dismiss"
        >
          暂不修改
        </button>
        <a
          class="kt-button"
          data-reminder-change-password
          href="/password/change/"
        >立即修改</a>
      </div>
    </section>
  </div>
</template>

<style scoped>
.kt-initial-password-reminder {
  position: fixed;
  z-index: 1100;
  inset: 0;
  display: grid;
  place-items: center;
  padding: 24px;
  background: rgb(15 23 42 / 48%);
}

.kt-initial-password-reminder__dialog {
  width: min(100%, 480px);
  padding: 28px;
}

.kt-initial-password-reminder__dialog h2 {
  margin-block: 0 12px;
  font-size: 24px;
}

.kt-initial-password-reminder__dialog p:last-of-type {
  color: var(--kt-color-muted);
  line-height: 1.7;
}

.kt-initial-password-reminder__actions {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  margin-top: 24px;
}

@media (max-width: 420px) {
  .kt-initial-password-reminder {
    padding: 16px;
  }

  .kt-initial-password-reminder__dialog {
    padding: 22px;
  }

  .kt-initial-password-reminder__actions {
    flex-direction: column-reverse;
  }

  .kt-initial-password-reminder__actions > * {
    width: 100%;
  }
}
</style>
