<script setup lang="ts">
import { computed, ref } from 'vue'
import ModalDialog from '@/design-system/components/ModalDialog.vue'
import { questionContentParts, type QuestionImages } from './question-content'

defineOptions({ inheritAttrs: false })
const props = defineProps<{ text: string; images?: QuestionImages; option?: boolean }>()
const parts = computed(() => questionContentParts(props.text, props.images))
const enlarged = ref<string>()
</script>

<template>
  <span v-bind="$attrs" class="kt-question-content" :class="{ 'kt-question-content--option': option }"><template v-for="(part, index) in parts" :key="index"><button v-if="part.source" class="kt-question-content__image" type="button" aria-label="放大图片" @click.stop.prevent="enlarged = part.source" @keydown.stop @pointerdown.stop><img :src="part.source" alt="题目图片" decoding="async"></button><template v-else>{{ part.text }}</template></template></span>
  <Teleport to="body">
    <ModalDialog v-if="enlarged" :open="true" title="图片预览" wide @close="enlarged = undefined">
      <img class="kt-question-content__enlarged" :src="enlarged" alt="题目图片放大预览">
      <template #footer><button class="kt-button kt-button--secondary" type="button" @click="enlarged = undefined">关闭</button></template>
    </ModalDialog>
  </Teleport>
</template>

<style scoped>
.kt-question-content { white-space: pre-wrap; overflow-wrap: anywhere; min-width: 0; }
.kt-question-content__image { display: inline-flex; vertical-align: middle; max-width: min(100%, 420px); min-height: 44px; margin: 6px 0; padding: 0; border: 1px solid var(--kt-border); border-radius: 6px; background: var(--kt-surface); cursor: zoom-in; }
.kt-question-content__image img { display: block; max-width: 100%; max-height: 180px; width: auto; height: auto; object-fit: contain; border-radius: inherit; }
.kt-question-content--option .kt-question-content__image { max-width: min(100%, 260px); }
.kt-question-content--option .kt-question-content__image img { max-height: 116px; }
.kt-question-content__image:focus-visible { outline: 2px solid var(--kt-action-600); outline-offset: 3px; }
.kt-question-content__enlarged { display: block; max-width: 100%; max-height: 60dvh; width: auto; height: auto; margin: auto; object-fit: contain; }
</style>
