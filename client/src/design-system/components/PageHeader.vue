<script setup lang="ts">defineProps<{ title: string }>()</script>
<template><header class="kt-page-header"><div class="kt-page-header__copy"><div class="kt-kicker"><slot name="kicker" /></div><h1>{{ title }}</h1><slot /></div><div class="kt-page-header__actions"><slot name="actions" /></div></header></template>
