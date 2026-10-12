<script setup lang="ts">
import { inject, type Ref } from 'vue'
const target = inject<Ref<HTMLElement | null> | null>('workspace-secondary-nav', null)
</script>

<template>
  <Teleport :to="target || 'body'" :disabled="!target"><slot /></Teleport>
</template>
