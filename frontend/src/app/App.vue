<script setup lang="ts">
import { ref } from 'vue'

import type { BootstrapContext } from './bootstrap'
import ResearchConsentDialog from './ResearchConsentDialog.vue'

const props = defineProps<{
  bootstrapContext: BootstrapContext | null
}>()

const consentRequired = ref(
  props.bootstrapContext?.user.role === 'student'
  && props.bootstrapContext.user.research_consent_required,
)
</script>

<template>
  <div
    data-vue-shell
    aria-label="KT Data Lab"
  >
    <ResearchConsentDialog
      v-if="consentRequired"
      data-research-consent-gate
      @accepted="consentRequired = false"
    />
    <RouterView v-else />
  </div>
</template>
