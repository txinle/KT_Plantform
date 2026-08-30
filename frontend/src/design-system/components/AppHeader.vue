<script setup lang="ts">
defineProps<{ userName: string; userMeta?: string | null }>()
</script>

<template>
  <header class="kt-app-header">
    <div class="kt-shell kt-app-header__inner">
      <a class="kt-wordmark" href="/" aria-label="KT Data Lab 首页">
        <span class="kt-brand-mark" aria-hidden="true">KT</span>
        <span class="kt-wordmark__name">KT Data Lab</span>
      </a>
      <nav class="kt-global-nav" aria-label="全局导航"><slot /></nav>
      <span class="kt-user-name">
        <strong>{{ userName }}</strong>
        <small v-if="userMeta">{{ userMeta }}</small>
      </span>
    </div>
  </header>
</template>
