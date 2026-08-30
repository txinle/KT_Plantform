<script setup lang="ts">
withDefaults(defineProps<{ name: string; size?: number; label?: string }>(), { size: 20, label: undefined })
</script>

<template>
  <svg
    class="kt-icon"
    :width="size"
    :height="size"
    :role="label ? 'img' : undefined"
    :aria-label="label"
    :aria-hidden="label ? undefined : 'true'"
    focusable="false"
  >
    <use :href="`/static/icons/kt-sprite.svg#kt-${name}`" />
  </svg>
</template>
