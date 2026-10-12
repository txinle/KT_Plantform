<script setup lang="ts">
import { getBootstrapContext } from './bootstrap'
const home = getBootstrapContext().user.home_url
</script>
<template>
  <main class="kt-shell kt-page-unavailable">
    <h1>找不到这个页面</h1>
    <p>链接可能已失效，请从课程入口继续。</p>
    <a class="kt-button kt-button--primary" :href="home">返回课程</a>
  </main>
</template>
<style scoped>
.kt-page-unavailable { padding-block: 64px; }
</style>
