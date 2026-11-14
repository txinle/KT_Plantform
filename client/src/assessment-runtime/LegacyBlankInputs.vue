<script setup lang="ts">
import { ref, watch } from 'vue'

const props = defineProps<{ count: number; value: string; disabled?: boolean }>()
const emit = defineEmits(['input', 'beforeinput', 'compositionstart', 'compositionend', 'blur'])
const values = ref<string[]>([])
const ambiguous = ref(false)
const serialize = () => values.value.some(value => value.trim().length) ? values.value.join('；') : ''

watch(() => [props.value, props.count] as const, ([value, count]) => {
  if (values.value.length === count && serialize() === value) return
  const parts = /[;；,，、。]/.test(value) ? value.split(/[;；,，、。]/) : value.trim().split(/\s+/)
  ambiguous.value = Boolean(value.trim()) && parts.length !== count
  values.value = Array.from({ length: count }, (_, index) =>
    parts.length === count ? (parts[index] || '').trim() : index === 0 ? value : '')
}, { immediate: true })

function forward(kind: 'input' | 'beforeinput' | 'compositionstart' | 'compositionend', index: number, event: Event) {
  const target = event.target as HTMLInputElement
  if (kind === 'input' || kind === 'compositionend') values.value[index] = target.value
  const offset = values.value.slice(0, index).reduce((sum, value) => sum + value.length + 1, 0)
  const input = event as InputEvent
  emit(kind, {
    inputType: input.inputType, isComposing: input.isComposing,
    target: { value: serialize(),
      selectionStart: target.selectionStart === null ? null : offset + target.selectionStart,
      selectionEnd: target.selectionEnd === null ? null : offset + target.selectionEnd },
  })
}
</script>

<template>
  <fieldset class="kt-legacy-blanks" data-legacy-answer-area>
    <legend>作答区</legend>
    <p v-if="ambiguous" role="status">原答案无法自动分到各空，已完整保留在第一空，请核对后分别填写。</p>
    <label v-for="(_, index) in values" :key="index" class="kt-assessment-text-answer">
      <span>第 {{ index + 1 }} 空</span>
      <input name="answer" :aria-label="`第 ${index + 1} 空`" :value="values[index]" :disabled="disabled"
        @beforeinput="forward('beforeinput', index, $event)" @input="forward('input', index, $event)"
        @compositionstart="forward('compositionstart', index, $event)"
        @compositionend="forward('compositionend', index, $event)" @blur="emit('blur', $event)">
    </label>
  </fieldset>
</template>

<style scoped>
.kt-legacy-blanks { border: 0; margin: 0; padding: 0; min-width: 0; display: grid; gap: 16px; }
.kt-legacy-blanks legend { padding: 0 0 12px; font-weight: 600; }
.kt-legacy-blanks input { min-height: 44px; width: 100%; box-sizing: border-box; }
</style>
