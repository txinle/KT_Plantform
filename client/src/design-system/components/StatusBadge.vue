<script setup lang="ts">defineProps<{ tone: 'success' | 'warning' | 'danger' | 'neutral'; label: string }>()</script>
<template><span class="kt-status" :class="`kt-status--${tone}`">{{ label }}</span></template>
