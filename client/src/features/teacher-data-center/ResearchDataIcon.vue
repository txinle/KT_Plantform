<script setup lang="ts">
defineProps<{ name: string }>()
</script>
<template>
  <svg class="kt-research-icon" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <template v-if="name === 'search'"><circle cx="10.5" cy="10.5" r="6.5" /><path d="m15.5 15.5 4 4" /></template>
    <template v-else-if="name === 'info'"><circle cx="12" cy="12" r="9" /><path d="M12 11v5M12 8h.01" /></template>
    <template v-else-if="name === 'student'"><circle cx="12" cy="8" r="4" /><path d="M4.5 21a7.5 7.5 0 0 1 15 0" /></template>
    <template v-else-if="name === 'session'"><path d="M5 4h14a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-8l-4.5 3v-3H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2Z" /><path d="M8 10h.01M12 10h.01M16 10h.01" /></template>
    <template v-else-if="name === 'question'"><path d="M6 2h9l4 4v16H6z" /><path d="M15 2v5h4M9 11h6M9 15h6" /></template>
    <template v-else-if="name === 'knowledge'"><path d="m2 9 10-5 10 5-10 5Z" /><path d="M6 11.2V17c3.5 2.7 8.5 2.7 12 0v-5.8M22 9v6" /></template>
    <template v-else-if="name === 'mapping'"><circle cx="12" cy="4" r="2.5" /><circle cx="5" cy="19" r="2.5" /><circle cx="19" cy="19" r="2.5" /><path d="M12 6.5V12M5 16.5V14h14v2.5M12 12H5" /></template>
    <template v-else-if="name === 'response'"><path d="M8 6h12M8 12h12M8 18h12" /><circle cx="3.5" cy="6" r="1" fill="currentColor" stroke="none" /><circle cx="3.5" cy="12" r="1" fill="currentColor" stroke="none" /><circle cx="3.5" cy="18" r="1" fill="currentColor" stroke="none" /></template>
    <template v-else><path d="m3 17 5-5 4 3 7-8" /><path d="M16 7h3v3" /></template>
  </svg>
</template>
