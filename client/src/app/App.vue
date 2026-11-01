<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'

import { bootstrapContext, getAuthenticationGeneration } from './bootstrap'
import ResearchConsentDialog from './ResearchConsentDialog.vue'
import SiteRegistrationFooter from './SiteRegistrationFooter.vue'

const route = useRoute()
const showPageRegistration = computed(() => route.path === '/login/')
const courseBoundary = computed(() => route.params?.courseId
  ? `${route.path.startsWith('/teacher/') ? 'teacher' : 'student'}:${route.params.courseId}` : undefined)
const consentRequired = computed(() => route.path !== '/login/'
  && bootstrapContext.value?.user.role === 'student'
  && bootstrapContext.value.user.research_consent_required)
</script>

<template>
  <div
    data-vue-shell
    class="kt-site-root"
    :class="{ 'kt-site-root--login': showPageRegistration }"
    aria-label="KT Data Lab"
  >
    <div class="kt-site-root__content">
      <ResearchConsentDialog
        v-if="consentRequired"
        :key="getAuthenticationGeneration()"
        data-research-consent-gate
      />
      <template v-else>
        <RouterView :key="courseBoundary" />
      </template>
    </div>
    <SiteRegistrationFooter v-if="showPageRegistration" />
  </div>
</template>

<style scoped>
.kt-site-root {
  --kt-site-registration-height: 0px;

  display: flex;
  min-height: 100dvh;
  flex-direction: column;
}

.kt-site-root--login {
  --kt-site-registration-height: 3.5rem;
}

.kt-site-root__content {
  min-width: 0;
  flex: 1;
}

@media (max-width: 480px) {
  .kt-site-root--login {
    --kt-site-registration-height: 5.25rem;
  }
}
</style>
