<script setup lang="ts">
const props = defineProps<{ page: number; totalPages: number }>()
const emit = defineEmits<{ navigate: [page: number] }>()
</script>
<template><nav v-if="totalPages > 1" class="kt-paginator" aria-label="分页"><button type="button" :disabled="page <= 1" @click="emit('navigate', page - 1)">上一页</button><span>第 {{ page }}/{{ totalPages }} 页</span><button type="button" data-next :disabled="page >= props.totalPages" @click="emit('navigate', page + 1)">下一页</button></nav></template>
