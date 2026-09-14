<script setup lang="ts">
import { ref } from 'vue'

import type { BootstrapContext } from './bootstrap'
import ResearchConsentDialog from './ResearchConsentDialog.vue'
import SiteRegistrationFooter from './SiteRegistrationFooter.vue'

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
    class="kt-site-root"
    aria-label="KT Data Lab"
  >
    <div class="kt-site-root__content">
      <ResearchConsentDialog
        v-if="consentRequired"
        data-research-consent-gate
        @accepted="consentRequired = false"
      />
      <RouterView v-else />
    </div>
    <SiteRegistrationFooter />
  </div>
</template>

<style scoped>
.kt-site-root {
  --kt-site-registration-height: 3.5rem;

  display: flex;
  min-height: 100dvh;
  flex-direction: column;
}

.kt-site-root__content {
  min-width: 0;
  flex: 1;
}

@media (max-width: 480px) {
  .kt-site-root {
    --kt-site-registration-height: 5.25rem;
  }
}
</style>
