<script setup lang="ts">
import {computed,nextTick,onBeforeUnmount,onMounted,ref,useId,watch} from 'vue'
import type {SiteSelectOption,SiteSelectPage} from './site-select-types'
const props=withDefaults(defineProps<{modelValue:string|number;options?:SiteSelectOption[];label:string;name?:string;placeholder?:string;disabled?:boolean;searchable?:boolean;pageSize?:number;loadOptions?:(query:string,page:number)=>Promise<SiteSelectPage>;dependencyKey?:string|number}>(),{options:()=>[],name:undefined,placeholder:'请选择',disabled:false,searchable:false,pageSize:10,loadOptions:undefined,dependencyKey:undefined})
const emit=defineEmits<{'update:modelValue':[value:string|number]}>()
const id=useId(),root=ref<HTMLElement>(),button=ref<HTMLButtonElement>(),list=ref<HTMLElement>(),searchInput=ref<HTMLInputElement>(),open=ref(false),active=ref(-1)
const search=ref(''),query=ref(''),page=ref(1),remoteItems=ref<SiteSelectOption[]>([]),remotePages=ref(1),remoteTotal=ref(0),retained=ref<SiteSelectOption>(),loading=ref(false),error=ref('')
let serial=0,timer:ReturnType<typeof setTimeout>|undefined
const remote=computed(()=>props.searchable&&Boolean(props.loadOptions))
const matching=computed(()=>props.options.filter(option=>[option.label,option.description].join(' ').toLowerCase().includes(query.value.trim().toLowerCase())))
const size=computed(()=>Math.max(1,props.pageSize))
const total=computed(()=>remote.value?remoteTotal.value:matching.value.length)
const totalPages=computed(()=>remote.value?Math.max(1,remotePages.value):Math.max(1,Math.ceil(total.value/size.value)))
const shown=computed(()=>!props.searchable?props.options:remote.value?remoteItems.value:matching.value.slice((page.value-1)*size.value,page.value*size.value))
const selected=computed(()=>props.options.find(option=>option.value===props.modelValue)||remoteItems.value.find(option=>option.value===props.modelValue)||(retained.value?.value===props.modelValue?retained.value:undefined))
const enabled=computed(()=>shown.value.flatMap((option,index)=>option.disabled?[]:[index]))
const optionId=(index:number)=>`${id}-option-${index}`
function close(){open.value=false;active.value=-1}
function scrollActive(){void nextTick(()=>{list.value?.querySelector<HTMLElement>(`[data-option-index="${active.value}"]`)?.scrollIntoView?.({block:'nearest'})})}
function activate(last=false){const selectedIndex=shown.value.findIndex(option=>option.value===props.modelValue&&!option.disabled);active.value=selectedIndex>=0?selectedIndex:(last?enabled.value.at(-1):enabled.value[0])??-1;scrollActive()}
async function fetchOptions(){if(!remote.value||!props.loadOptions||props.disabled)return;const request=++serial;loading.value=true;error.value='';remoteItems.value=[];try{const result=await props.loadOptions(query.value,page.value);if(request!==serial)return;remoteItems.value=result.items;remoteTotal.value=result.total;remotePages.value=result.total_pages;page.value=result.page;if(result.selected?.value===props.modelValue)retained.value=result.selected;else{const current=result.items.find(item=>item.value===props.modelValue);if(current)retained.value=current}activate()}catch(e){if(request===serial)error.value=e instanceof Error?e.message:'读取选项失败，请重试。'}finally{if(request===serial)loading.value=false}}
function show(last=false){if(props.disabled)return;open.value=true;activate(last);if(remote.value)void fetchOptions();if(props.searchable)void nextTick(()=>searchInput.value?.focus())}
function toggle(){if(props.disabled)return;if(open.value)close();else show()}
function choose(index:number){const option=shown.value[index];if(props.disabled||loading.value||!option||option.disabled)return;retained.value=option;emit('update:modelValue',option.value);close();button.value?.focus()}
function navigate(next:number){if(loading.value||next<1||next>totalPages.value)return;page.value=next;if(remote.value)void fetchOptions();else activate()}
function move(delta:number){const position=enabled.value.indexOf(active.value);const next=Math.min(enabled.value.length-1,Math.max(0,position+delta));active.value=enabled.value[next]??-1;scrollActive()}
function keydown(event:KeyboardEvent){
 if(props.disabled)return
 if(event.key==='Escape'){if(open.value){event.preventDefault();event.stopPropagation();close();button.value?.focus()}return}
 if(event.key==='Tab'){if(!props.searchable)close();return}
 const fromSearch=event.target===searchInput.value
 if(fromSearch&&[' ','Home','End'].includes(event.key))return
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
function reset(){clearTimeout(timer);serial++;close();search.value='';query.value='';page.value=1;remoteItems.value=[];remotePages.value=1;remoteTotal.value=0;retained.value=undefined;loading.value=false;error.value=''}
watch(search,()=>{clearTimeout(timer);serial++;if(remote.value){remoteItems.value=[];loading.value=open.value}timer=setTimeout(()=>{query.value=search.value;page.value=1;if(remote.value&&open.value)void fetchOptions();else activate()},200)})
watch([()=>props.disabled,()=>props.dependencyKey],()=>reset())
watch(()=>props.options,()=>{if(props.searchable)page.value=1;if(open.value)activate()})
watch(()=>props.modelValue,()=>{if(!props.modelValue)retained.value=undefined;if(open.value)activate()})
onMounted(()=>{document.addEventListener('pointerdown',outside);if(remote.value&&props.modelValue)void fetchOptions()})
onBeforeUnmount(()=>{serial++;clearTimeout(timer);document.removeEventListener('pointerdown',outside)})
</script>
<template>
  <div ref="root" class="site-select" :class="{'site-select--disabled':disabled,'site-select--searchable':searchable}" @focusout="blur">
    <span :id="`${id}-label`" class="site-select__label">{{ label }}</span>
    <button :id="`${id}-button`" ref="button" type="button" role="combobox" class="site-select__trigger" :class="{'site-select__trigger--open':open}" :disabled="disabled" :aria-labelledby="`${id}-label`" aria-haspopup="listbox" :aria-controls="`${id}-list`" :aria-expanded="open" :aria-activedescendant="open&&active>=0?optionId(active):undefined" @click="toggle" @keydown="keydown">
      <span :class="{'site-select__placeholder':!selected}">{{ selected?.label||placeholder }}</span><svg viewBox="0 0 20 20" aria-hidden="true"><path d="m5 7.5 5 5 5-5" /></svg>
    </button>
    <input v-if="name" type="hidden" :name="name" :value="modelValue" :disabled="disabled">
    <div v-if="open" class="site-select__panel">
      <input v-if="searchable" ref="searchInput" v-model="search" class="site-select__search" type="search" :aria-label="`搜索${label}`" :placeholder="`搜索${label}`" :aria-controls="`${id}-list`" :aria-activedescendant="active>=0?optionId(active):undefined" @keydown="keydown">
      <p v-if="loading" role="status" class="site-select__empty">正在加载…</p>
      <div v-else-if="error" role="alert" class="site-select__empty">{{ error }} <button type="button" @click="fetchOptions">重试</button></div>
      <div :id="`${id}-list`" ref="list" class="site-select__list" role="listbox" :aria-labelledby="`${id}-label`" :aria-busy="loading">
        <div v-for="(option,index) in shown" :id="optionId(index)" :key="`${typeof option.value}:${option.value}`" role="option" :data-option-index="index" class="site-select__option" :class="{'site-select__option--active':active===index,'site-select__option--selected':option.value===modelValue}" :aria-selected="option.value===modelValue" :aria-disabled="option.disabled||undefined" @mousedown.prevent @mouseenter="!option.disabled&&(active=index)" @click="choose(index)">
          <span>{{ option.label }}<small v-if="option.description">{{ option.description }}</small></span><svg v-if="option.value===modelValue" viewBox="0 0 20 20" aria-hidden="true"><path d="m4 10 4 4 8-8" /></svg>
        </div>
        <p v-if="!loading&&!error&&!shown.length" class="site-select__empty">{{ query?'没有匹配选项':'暂无可选项' }}</p>
      </div>
      <div v-if="searchable&&!error" class="site-select__pagination"><button type="button" aria-label="上一页" :disabled="loading||page<=1" @click="navigate(page-1)">上一页</button><span>{{ page }} / {{ totalPages }} · 共 {{ total }} 项</span><button type="button" aria-label="下一页" :disabled="loading||page>=totalPages" @click="navigate(page+1)">下一页</button></div>
    </div>
  </div>
</template>
<style scoped>
.site-select{display:grid;gap:8px;min-width:0;text-align:left;font-size:13px;color:#26364c}.site-select__label{font-size:12px;font-weight:500}.site-select .site-select__trigger{display:flex;align-items:center;justify-content:space-between;gap:12px;width:100%;min-height:44px;padding:10px 12px;border:1px solid #dbe3ed;border-radius:6px;background:#fff;color:#26364c;font:inherit;text-align:left;cursor:pointer}.site-select__trigger>span{min-width:0;overflow-wrap:anywhere}.site-select__trigger:hover:not(:disabled){border-color:#a6bddf;background:#fafcff}.site-select__trigger:focus-visible{outline:2px solid #285dbb;outline-offset:2px}.site-select .site-select__trigger--open{border-color:#8caad7;background:#fafcff}.site-select svg{width:18px;height:18px;flex-shrink:0;fill:none;stroke:currentColor;stroke-width:1.6;stroke-linecap:round;stroke-linejoin:round}.site-select__trigger svg{color:#738095}.site-select__trigger--open svg{transform:rotate(180deg)}.site-select__placeholder{color:#64758b}.site-select__list{position:relative;max-height:200px;overflow-y:auto;overscroll-behavior:contain;padding:4px;border:1px solid #cbd8e9;border-radius:7px;background:#fff;box-shadow:0 4px 12px #182d490d}.site-select__option{display:flex;align-items:center;justify-content:space-between;gap:12px;min-height:44px;padding:10px 12px;border-radius:4px;line-height:1.6;cursor:pointer;overflow-wrap:anywhere}.site-select__option--active,.site-select__option:hover:not([aria-disabled=true]){background:#f0f5fc;color:#244c87}.site-select__option--selected{background:#edf3ff;color:#285dbb;font-weight:500}.site-select__option[aria-disabled=true]{color:#8896a7;background:#f8fafc;cursor:not-allowed}.site-select .site-select__trigger:disabled{cursor:not-allowed;background:#f2f5f8;color:#77869a;opacity:1}.site-select__empty{padding:12px;margin:0;color:#64758b}.site-select--searchable{position:relative}.site-select--searchable .site-select__panel{position:absolute;top:100%;left:0;right:0;z-index:20;padding:8px;background:white;border:1px solid #cbd8e9;border-radius:7px;box-shadow:0 8px 24px #182d4926}.site-select--searchable .site-select__list{border:0;box-shadow:none}.site-select .site-select__search{width:100%;min-height:38px;box-sizing:border-box;padding:8px 10px;border:1px solid #cbd8e9;border-radius:5px;font:inherit}.site-select__option small{display:block;font-size:11px;color:#64758b}.site-select__pagination{display:flex;align-items:center;justify-content:space-between;gap:6px;padding-top:8px;font-size:11px}.site-select__pagination button{padding:5px 7px;font:inherit;white-space:nowrap}
</style>
