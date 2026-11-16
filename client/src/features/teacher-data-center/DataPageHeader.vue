<script setup lang="ts">
import PageHeader from '@/design-system/components/PageHeader.vue'

defineProps<{ title: string }>()
</script>

<template>
  <div class="kt-data-heading">
    <PageHeader :title="title">
      <template #actions>
        <details v-if="$slots.default" class="kt-data-help">
          <summary>使用说明</summary>
          <div class="kt-data-help-content"><slot /></div>
        </details>
        <slot name="actions" />
      </template>
    </PageHeader>
  </div>
</template>
