<script setup lang="ts">
defineProps<{
  code: string; name: string; level: number; order: number
  concepts: { kc_code: string; name: string }[]; usedCodes: string[]
  parents: { kc_code: string; name: string }[]; parent: string | null
  canAdd: boolean; readOnly?: boolean
}>()
defineEmits<{
  change: [code: string]; add: []; remove: []; parent: [code: string]; order: [order: number]
}>()
</script>

<template>
  <article class="kt-kc-editor-node" :class="{ 'is-readonly': readOnly }" :data-code="code" :data-level="level">
    <div class="kt-kc-editor-node__heading">
      <span>{{ level === 1 ? '一级 · 主 KC' : level === 2 ? '二级 KC' : '三级 KC' }}</span>
      <button v-if="!readOnly && level > 1" type="button" class="kt-text-button kt-text-button--danger" data-remove @click="$emit('remove')">{{ level === 2 ? '删除分支' : '删除' }}</button>
    </div>
    <strong class="kt-kc-editor-node__name">{{ code ? `${code} · ${name}` : '尚未选择主 KC' }}</strong>
    <select v-if="!readOnly" :id="level === 1 ? 'kc-root' : undefined" :value="code" data-kc-choice :aria-label="level === 1 ? '一级主 KC' : `更换 ${code}`" @change="$emit('change', ($event.target as HTMLSelectElement).value)">
      <option v-if="level === 1" value="">未映射</option>
      <option v-for="concept in concepts" :key="concept.kc_code" :value="concept.kc_code" :disabled="concept.kc_code !== code && usedCodes.includes(concept.kc_code)">{{ concept.kc_code }} · {{ concept.name }}</option>
      <option v-if="code && !concepts.some(c => c.kc_code === code)" :value="code">{{ code }} · 当前关联</option>
    </select>
    <button v-if="!readOnly && level < 3" type="button" class="kt-kc-editor-add" data-add :disabled="!code || !canAdd" @click="$emit('add')">＋ {{ level === 1 ? '添加二级 KC' : '添加三级 KC' }}</button>
    <details v-if="!readOnly && level > 1" class="kt-kc-editor-details">
      <summary>节点位置</summary>
      <label>父节点<select :value="parent" :aria-label="`${code} 父节点`" data-parent @change="$emit('parent', ($event.target as HTMLSelectElement).value)"><option v-for="p in parents" :key="p.kc_code" :value="p.kc_code">{{ p.kc_code }} · {{ p.name }}</option></select></label>
      <label>同级顺序<input :value="order" type="number" min="1" :aria-label="`${code} 同级顺序`" data-order @change="$emit('order', Number(($event.target as HTMLInputElement).value))"></label>
    </details>
  </article>
</template>
