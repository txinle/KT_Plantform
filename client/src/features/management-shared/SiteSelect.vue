<script setup lang="ts">
import {computed,nextTick,onBeforeUnmount,onMounted,ref,useId,watch} from 'vue'
interface SelectOption {value:string|number;label:string;disabled?:boolean}
const props=withDefaults(defineProps<{modelValue:string|number;options:SelectOption[];label:string;name?:string;placeholder?:string;disabled?:boolean}>(),{name:undefined,placeholder:'请选择',disabled:false})
const emit=defineEmits<{'update:modelValue':[value:string|number]}>()
const id=useId(),root=ref<HTMLElement>(),button=ref<HTMLButtonElement>(),list=ref<HTMLElement>(),open=ref(false),active=ref(-1)
const selected=computed(()=>props.options.find(option=>option.value===props.modelValue))
const enabled=computed(()=>props.options.flatMap((option,index)=>option.disabled?[]:[index]))
const optionId=(index:number)=>`${id}-option-${index}`
function close(){open.value=false;active.value=-1}
function scrollActive(){void nextTick(()=>{list.value?.querySelector<HTMLElement>(`[data-option-index="${active.value}"]`)?.scrollIntoView?.({block:'nearest'})})}
function show(last=false){if(props.disabled)return;open.value=true;const selectedIndex=props.options.findIndex(option=>option.value===props.modelValue&&!option.disabled);active.value=selectedIndex>=0?selectedIndex:(last?enabled.value.at(-1):enabled.value[0])??-1;scrollActive()}
function toggle(){if(props.disabled)return;if(open.value)close();else show()}
function choose(index:number){const option=props.options[index];if(props.disabled||!option||option.disabled)return;emit('update:modelValue',option.value);close();button.value?.focus()}
function move(delta:number){const position=enabled.value.indexOf(active.value);const next=Math.min(enabled.value.length-1,Math.max(0,position+delta));active.value=enabled.value[next]??-1;scrollActive()}
function keydown(event:KeyboardEvent){
 if(props.disabled)return
 if(event.key==='Escape'){if(open.value){event.preventDefault();event.stopPropagation();close()}return}
 if(event.key==='Tab'){close();return}
 if(!['ArrowDown','ArrowUp','Home','End','Enter',' '].includes(event.key))return
 event.preventDefault()
 if(!open.value){show(event.key==='ArrowUp'||event.key==='End');if(event.key==='Home')active.value=enabled.value[0]??-1;if(event.key==='End')active.value=enabled.value.at(-1)??-1;return}
 if(event.key==='ArrowDown')move(1)
 else if(event.key==='ArrowUp')move(-1)
 else if(event.key==='Home'||event.key==='End'){active.value=(event.key==='Home'?enabled.value[0]:enabled.value.at(-1))??-1;scrollActive()}
 else choose(active.value)
}
function outside(event:Event){if(event.target instanceof Node&&!root.value?.contains(event.target))close()}
function blur(event:FocusEvent){if(!(event.relatedTarget instanceof Node)||!root.value?.contains(event.relatedTarget))close()}
watch(()=>props.disabled,value=>{if(value)close()})
watch(()=>props.options,()=>{if(open.value)show()})
watch(()=>props.modelValue,()=>{if(open.value)show()})
onMounted(()=>document.addEventListener('pointerdown',outside))
onBeforeUnmount(()=>document.removeEventListener('pointerdown',outside))
</script>
<template>
  <div ref="root" class="site-select" :class="{'site-select--disabled':disabled}" @focusout="blur">
    <span :id="`${id}-label`" class="site-select__label">{{ label }}</span>
    <button :id="`${id}-button`" ref="button" type="button" role="combobox" class="site-select__trigger" :class="{'site-select__trigger--open':open}" :disabled="disabled" :aria-labelledby="`${id}-label`" aria-haspopup="listbox" :aria-controls="`${id}-list`" :aria-expanded="open" :aria-activedescendant="open&&active>=0?optionId(active):undefined" @click="toggle" @keydown="keydown">
      <span :class="{'site-select__placeholder':!selected}">{{ selected?.label||placeholder }}</span><svg viewBox="0 0 20 20" aria-hidden="true"><path d="m5 7.5 5 5 5-5" /></svg>
    </button>
    <input v-if="name" type="hidden" :name="name" :value="modelValue" :disabled="disabled">
    <div v-if="open" :id="`${id}-list`" ref="list" class="site-select__list" role="listbox" :aria-labelledby="`${id}-label`">
      <div v-for="(option,index) in options" :id="optionId(index)" :key="`${typeof option.value}:${option.value}`" role="option" :data-option-index="index" class="site-select__option" :class="{'site-select__option--active':active===index,'site-select__option--selected':option.value===modelValue}" :aria-selected="option.value===modelValue" :aria-disabled="option.disabled||undefined" @mousedown.prevent @mouseenter="!option.disabled&&(active=index)" @click="choose(index)">
        <span>{{ option.label }}</span><svg v-if="option.value===modelValue" viewBox="0 0 20 20" aria-hidden="true"><path d="m4 10 4 4 8-8" /></svg>
      </div>
      <p v-if="!options.length" class="site-select__empty">暂无可选项</p>
    </div>
  </div>
</template>
<style scoped>
.site-select{display:grid;gap:8px;min-width:0;text-align:left;font-size:13px;color:#26364c}.site-select__label{font-size:12px;font-weight:500}.site-select .site-select__trigger{display:flex;align-items:center;justify-content:space-between;gap:12px;width:100%;min-height:44px;padding:10px 12px;border:1px solid #dbe3ed;border-radius:6px;background:#fff;color:#26364c;font:inherit;text-align:left;cursor:pointer}.site-select__trigger>span{min-width:0;overflow-wrap:anywhere}.site-select__trigger:hover:not(:disabled){border-color:#a6bddf;background:#fafcff}.site-select__trigger:focus-visible{outline:2px solid #285dbb;outline-offset:2px}.site-select .site-select__trigger--open{border-color:#8caad7;background:#fafcff}.site-select svg{width:18px;height:18px;flex-shrink:0;fill:none;stroke:currentColor;stroke-width:1.6;stroke-linecap:round;stroke-linejoin:round}.site-select__trigger svg{color:#738095}.site-select__trigger--open svg{transform:rotate(180deg)}.site-select__placeholder{color:#64758b}.site-select__list{position:relative;max-height:200px;overflow-y:auto;overscroll-behavior:contain;padding:4px;border:1px solid #cbd8e9;border-radius:7px;background:#fff;box-shadow:0 4px 12px #182d490d}.site-select__option{display:flex;align-items:center;justify-content:space-between;gap:12px;min-height:44px;padding:10px 12px;border-radius:4px;line-height:1.6;cursor:pointer;overflow-wrap:anywhere}.site-select__option--active,.site-select__option:hover:not([aria-disabled=true]){background:#f0f5fc;color:#244c87}.site-select__option--selected{background:#edf3ff;color:#285dbb;font-weight:500}.site-select__option[aria-disabled=true]{color:#8896a7;background:#f8fafc;cursor:not-allowed}.site-select .site-select__trigger:disabled{cursor:not-allowed;background:#f2f5f8;color:#77869a;opacity:1}.site-select__empty{padding:12px;margin:0;color:#64758b}
</style>
