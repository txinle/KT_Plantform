<script setup lang="ts">defineProps<{ tone: 'info' | 'success' | 'warning' | 'danger' }>()</script>
<template><div class="kt-alert" :class="`kt-alert--${tone}`" :role="tone === 'danger' ? 'alert' : 'status'"><slot /></div></template>
