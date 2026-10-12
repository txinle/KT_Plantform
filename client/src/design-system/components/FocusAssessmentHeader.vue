<script setup lang="ts">defineProps<{ current: number; total: number }>()</script>
<template><header aria-label="作答进度"><strong>第 {{ current }}/{{ total }} 题</strong><slot /></header></template>
