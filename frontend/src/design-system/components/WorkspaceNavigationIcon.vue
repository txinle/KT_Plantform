<script setup lang="ts">
defineProps<{ name: 'home' | 'overview' | 'bank' | 'class' | 'experiment' | 'data' | 'settings' }>()
</script>

<template>
  <svg class="kt-workspace-navigation-icon" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <template v-if="name === 'home'"><path d="m3 11 9-8 9 8" /><path d="M5.5 9.5V21h13V9.5M9.5 21v-7h5v7" /></template>
    <template v-else-if="name === 'overview'"><path d="M4 20V12M10 20V8M16 20V4M3 8l6-4 4 3 7-5" /></template>
    <template v-else-if="name === 'bank'"><rect x="5" y="3" width="14" height="18" rx="2" /><path d="M9 8h6M9 12h6M9 16h4" /></template>
    <template v-else-if="name === 'class'"><circle cx="9" cy="8" r="3" /><circle cx="17" cy="10" r="2.5" /><path d="M3 21v-2a6 6 0 0 1 12 0v2M15 15.5a5 5 0 0 1 6 4.9" /></template>
    <template v-else-if="name === 'experiment'"><path d="M9 3h6M10 3v5l-6 10a2 2 0 0 0 1.7 3h12.6a2 2 0 0 0 1.7-3L14 8V3" /><path d="M7.5 15h9" /></template>
    <template v-else-if="name === 'data'"><ellipse cx="12" cy="5" rx="7" ry="3" /><path d="M5 5v6c0 1.7 3.1 3 7 3s7-1.3 7-3V5M5 11v6c0 1.7 3.1 3 7 3s7-1.3 7-3v-6" /></template>
    <template v-else><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06-2.83 2.83-.06-.06a1.7 1.7 0 0 0-1.88-.34 1.7 1.7 0 0 0-1.03 1.55V21h-4v-.08A1.7 1.7 0 0 0 8.95 19.4a1.7 1.7 0 0 0-1.88.34l-.06.06-2.83-2.83.06-.06A1.7 1.7 0 0 0 4.6 15 1.7 1.7 0 0 0 3.08 14H3v-4h.08A1.7 1.7 0 0 0 4.6 8.95a1.7 1.7 0 0 0-.34-1.88L4.2 7l2.83-2.83.06.06A1.7 1.7 0 0 0 9 4.6 1.7 1.7 0 0 0 10 3.08V3h4v.08A1.7 1.7 0 0 0 15.05 4.6a1.7 1.7 0 0 0 1.88-.34l.06-.06L19.82 7l-.06.06A1.7 1.7 0 0 0 19.4 9 1.7 1.7 0 0 0 20.92 10H21v4h-.08A1.7 1.7 0 0 0 19.4 15Z" /></template>
  </svg>
</template>
