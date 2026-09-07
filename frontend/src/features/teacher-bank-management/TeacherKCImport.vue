<script setup lang="ts">
import { onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'

const route = useRoute()
const router = useRouter()
const courseId = Number(route.params.courseId)
const bankId = Number(route.params.bankId)

onMounted(() => {
  void router.replace(`/teacher/courses/${courseId}/banks/${bankId}/import/`)
})
</script>

<template><p role="status">正在转到统一题库导入…</p></template>
