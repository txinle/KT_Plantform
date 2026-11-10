<script setup lang="ts">
defineProps<{ courseId: number; active: 'shared' | 'archived' }>()
</script>

<template>
  <h1 class="kt-visually-hidden">{{ active === 'shared' ? '共享题库' : '已归档题库' }}</h1>
  <div class="bank-navigation">
    <nav class="kt-bank-module-nav" aria-label="题库模块">
      <RouterLink :to="`/teacher/courses/${courseId}/banks/`">题库管理</RouterLink>
      <RouterLink :to="`/teacher/courses/${courseId}/banks/?tab=course-kcs`">课程 KC 总库</RouterLink>
      <RouterLink :to="`/teacher/courses/${courseId}/banks/shared/`" :aria-current="active === 'shared' ? 'page' : undefined">共享题库</RouterLink>
      <RouterLink :to="`/teacher/courses/${courseId}/banks/archived/`" :aria-current="active === 'archived' ? 'page' : undefined">已归档</RouterLink>
    </nav>
    <slot />
  </div>
</template>

<style scoped>
.bank-navigation { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 16px; margin-bottom: 16px; }
.bank-navigation .kt-bank-module-nav { flex: 1 1 max-content; min-width: 0; margin-bottom: 0; }
</style>
