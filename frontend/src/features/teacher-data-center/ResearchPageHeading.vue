<script setup lang="ts">
defineProps<{ title: string; description: string; step: string }>()
defineEmits<{ back: [] }>()
</script>
<template>
  <header class="kt-research-heading kt-research-heading--detail">
    <div><p class="kt-breadcrumbs">研究数据范围 <span>›</span> 研究数据预览 <span>›</span> <strong>{{ title }}</strong></p><h1>{{ title }}</h1><p>{{ description }}</p></div>
    <div><button class="kt-research-back" type="button" @click="$emit('back')">‹ 返回上一级</button><strong class="kt-step-pill">{{ step }}</strong></div>
  </header>
</template>
