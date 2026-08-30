<script setup lang="ts">defineProps<{ caption: string }>()</script>
<template><table class="kt-table"><caption>{{ caption }}</caption><thead><slot name="head" /></thead><tbody><slot /></tbody></table></template>
