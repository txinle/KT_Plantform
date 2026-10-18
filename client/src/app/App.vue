<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'

import DeletionRecovery from '@/features/teacher-deletion/DeletionRecovery.vue'

import { bootstrapContext, getAuthenticationGeneration } from './bootstrap'
import ResearchConsentDialog from './ResearchConsentDialog.vue'
import SiteRegistrationFooter from './SiteRegistrationFooter.vue'

const route = useRoute()
const consentRequired = computed(() => route.path !== '/login/'
  && bootstrapContext.value?.user.role === 'student'
  && bootstrapContext.value.user.research_consent_required)
const deletionCourseId = computed(() => ['teacher', 'admin'].includes(bootstrapContext.value?.user.role || '')
  && route.path.startsWith('/teacher/courses/') ? Number(route.params.courseId) || undefined : undefined)
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
        :key="getAuthenticationGeneration()"
        data-research-consent-gate
      />
      <template v-else>
        <DeletionRecovery
          v-if="deletionCourseId"
          :key="`${getAuthenticationGeneration()}:${deletionCourseId}`"
          :course-id="deletionCourseId"
        />
        <RouterView />
      </template>
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
