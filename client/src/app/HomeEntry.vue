<script setup lang="ts">
import { onMounted } from 'vue'
import { getBootstrapContext } from './bootstrap'
import { authenticatedEntryRedirect } from './auth'

onMounted(() => {
  const destination = authenticatedEntryRedirect(getBootstrapContext().user, window.location.pathname)
  if (destination) window.location.assign(destination)
})
</script>

<template><p role="status">正在打开首页…</p></template>
