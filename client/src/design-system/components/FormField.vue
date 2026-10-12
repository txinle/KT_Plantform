<script setup lang="ts">defineProps<{ forId: string; label: string; error?: string }>()</script>
<template><div class="kt-field"><label :for="forId">{{ label }}</label><slot /><p v-if="error" class="kt-field__error" role="alert">{{ error }}</p></div></template>
