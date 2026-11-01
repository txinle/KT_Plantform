<script setup lang="ts">
import { computed, ref, watch, onUnmounted, nextTick } from 'vue'
import SiteDialog from '@/features/management-shared/SiteDialog.vue'
import SiteSelect from '@/features/management-shared/SiteSelect.vue'
import { getAdminApi, type AdminRow } from './api'
import { getTeacherDataCenterApi, type DataCenterExportCatalog, type DataCenterExportJobMutation, type DataQuery } from '@/features/teacher-data-center/api'

interface ExportEntry { id: number; name: string; catalog?: DataCenterExportCatalog; error?: string; job?: DataCenterExportJobMutation }
const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ close: []; changed: [] }>()
const admin = getAdminApi(), api = getTeacherDataCenterApi()
const courses = ref<AdminRow[]>([]), course = ref(0), experiment = ref(0)
const classMode = ref<'all' | 'single'>('all'), classId = ref(0)
const catalog = ref<DataCenterExportCatalog>(), entries = ref<ExportEntry[]>([]), jobs = ref<ExportEntry[]>([])
const error = ref(''), loading = ref(false), creating = ref(false), confirmed = ref(false)
let generation = 0, timer: ReturnType<typeof setTimeout> | undefined, polls = 0
let statusController: AbortController | undefined
const allExperiments = computed(() => experiment.value === -1)
const selectedCatalog = computed(() => entries.value[0]?.catalog)
function unavailable(entry: ExportEntry): string {
  if (entry.error) return entry.error
  if (!entry.catalog?.scope.is_complete) return '导出范围不完整'
  if (entry.catalog.preparation.pending_count) return `${entry.catalog.preparation.pending_count} 名已结束学生的数据正在后台整理，暂不能导出`
  if ((entry.catalog.selected_package || entry.catalog.packages[0])?.blocked) return '当前范围暂不能导出'
  return ''
}
const readyEntries = computed(() => entries.value.filter(entry => !unavailable(entry)))
const canCreate = computed(() => readyEntries.value.length > 0 && confirmed.value && !loading.value && !creating.value)
function stop() { if (timer) clearTimeout(timer); timer = undefined; statusController?.abort() }
function invalidate() { generation++; entries.value = []; confirmed.value = false; error.value = ''; loading.value = false }
watch(() => props.open, async open => {
  stop(); invalidate()
  if (!open) return
  course.value = 0; experiment.value = 0; classMode.value = 'all'; classId.value = 0; catalog.value = undefined; jobs.value = []
  const current = generation
  try { const result = await admin.get<{ items: AdminRow[] }>('courses/'); if (props.open && current === generation) courses.value = result.items }
  catch (e) { if (props.open && current === generation) error.value = (e as Error).message }
})
watch(course, async value => {
  invalidate(); catalog.value = undefined; experiment.value = 0; classMode.value = 'all'; classId.value = 0
  if (!value || !props.open) return
  await nextTick()
  if (value !== course.value || !props.open) return
  const current = generation; loading.value = true
  try { const result = await api.exports(value, {}); if (current === generation && props.open) catalog.value = result }
  catch (e) { if (current === generation) error.value = (e as Error).message }
  finally { if (current === generation) loading.value = false }
})
watch(experiment, () => { classMode.value = 'all'; classId.value = 0 })
watch([experiment, classMode, classId], () => {
  invalidate()
  if (!experiment.value || !props.open || classMode.value === 'single' && !classId.value) return
  void loadPreview()
})
async function loadPreview() {
  if (!course.value || !experiment.value || classMode.value === 'single' && !classId.value) return
  const current = ++generation, courseId = course.value
  const targets = catalog.value?.experiments.filter(item => allExperiments.value || item.id === experiment.value) || []
  entries.value = []; confirmed.value = false; loading.value = true; error.value = ''
  const result: ExportEntry[] = []
  for (const target of targets) {
    const query: DataQuery = { scope_version: 2, experiment_id: target.id, class_mode: classMode.value, ...(classMode.value === 'single' ? { class_id: classId.value } : {}) }
    try { result.push({ ...target, catalog: await api.exports(courseId, query) }) }
    catch (e) { result.push({ ...target, error: (e as Error).message }) }
    if (current !== generation || !props.open) return
  }
  entries.value = result
  if (!allExperiments.value && result[0]?.catalog) catalog.value = result[0].catalog
  loading.value = false
}
function schedule() {
  if (props.open && jobs.value.some(entry => entry.job && ['queued', 'running'].includes(entry.job.status)) && polls < 3) timer = setTimeout(() => { polls++; void refresh() }, [5000, 15000, 30000][polls])
}
async function refresh() {
  if (!props.open) return
  stop(); statusController = new AbortController()
  const signal = statusController.signal
  for (const entry of jobs.value) {
    if (!entry.job || !['queued', 'running'].includes(entry.job.status)) continue
    try {
      const result = await api.exportJob(course.value, entry.job.job_id, signal)
      if (signal.aborted || !props.open) return
      entry.job = result
    } catch (e) { if (!signal.aborted) error.value = (e as Error).message }
  }
  if (!signal.aborted) { schedule(); emit('changed') }
}
async function createEntries(targets: ExportEntry[]) {
  creating.value = true; error.value = ''
  for (const entry of targets) {
    entry.error = ''
    try { entry.job = await api.createExport(course.value, { package: 'experiment', confirm_identified: true, scope: entry.catalog!.scope.values as Record<string, unknown> }) }
    catch (e) { entry.error = (e as Error).message }
  }
  creating.value = false; polls = 0; emit('changed'); schedule()
}
async function create() {
  if (!canCreate.value) return
  jobs.value = readyEntries.value.map(entry => ({ ...entry }))
  await createEntries(jobs.value)
}
async function retry(entry: ExportEntry) { if (!creating.value && !entry.job) await createEntries([entry]) }
onUnmounted(() => { generation++; stop() })
</script>

<template>
  <SiteDialog :open="open" title="创建标准实验数据包" subtitle="每个实验独立导出 7 个 CSV 与 1 个 JSON 文件" wide @close="!creating && emit('close')">
    <div v-if="!jobs.length" class="form-grid">
      <SiteSelect v-model="course" label="课程" name="course" placeholder="请选择课程" :disabled="creating" :options="courses.map(c => ({ value: c.id, label: `${c.name} · ${c.term || ''}` }))" />
      <SiteSelect v-if="catalog" v-model="experiment" label="实验" name="experiment" placeholder="请选择实验" :disabled="creating" :options="[{ value: -1, label: '全部实验' }, ...catalog.experiments.map(e => ({ value: e.id, label: e.name }))]" />
      <template v-if="experiment">
        <template v-if="!allExperiments">
          <SiteSelect v-model="classMode" label="班级范围" name="class_mode" :disabled="creating" :options="[{ value: 'all', label: '全部已发布班级' }, { value: 'single', label: '单个班级' }]" />
          <SiteSelect v-if="classMode === 'single'" v-model="classId" label="班级" name="class_id" placeholder="请选择班级" :disabled="creating" :options="(catalog?.class_choices || []).map(c => ({ value: c.id, label: `${c.academic_year} ${c.name}` }))" />
        </template>
        <p v-else>全部实验按各自全部已发布班级分别导出，每个实验生成一个独立的标准数据包。</p>
        <button :disabled="loading || creating || classMode === 'single' && !classId" @click="loadPreview">刷新导出范围</button>
      </template>
      <p v-if="loading" role="status">正在读取服务端导出范围…</p>
      <template v-if="catalog">
        <p>每包固定 7 CSV + 1 JSON。课程基础资料保持完整，实验 Session 绑定覆盖该实验全部已发布班级，作答与过程记录按所选班级范围生成。</p>
        <ul class="export-scope-list"><li v-for="table in (selectedCatalog || catalog).tables" :key="table.key" data-testid="export-table"><strong>{{ table.path }}</strong><small>{{ table.description }}</small><small v-if="!allExperiments"> · {{ table.format.toUpperCase() }} · {{ table.total_items }} 条记录</small></li></ul>
      </template>
      <template v-if="entries.length">
        <p v-if="allExperiments">共 {{ entries.length }} 个实验，可创建 {{ readyEntries.length }} 个数据包。未就绪的实验不会创建，请查看下列原因。</p>
        <ul class="export-scope-list"><li v-for="entry in entries" :key="entry.id"><strong>{{ entry.name }}</strong><p v-if="unavailable(entry)" class="error-block">{{ unavailable(entry) }}</p><p v-else>{{ entry.catalog?.scope.display }} · 已就绪</p></li></ul>
        <p>{{ catalog?.identified_data_notice }}</p>
        <label class="permission-option"><input v-model="confirmed" type="checkbox" :disabled="creating"><span><strong>确认导出范围及身份数据</strong><small>确认创建以上 {{ readyEntries.length }} 个可导出实验的数据包，已获授权并按研究要求管理实名数据。</small></span></label>
      </template>
    </div>
    <div v-else role="status">
      <article v-for="entry in jobs" :key="entry.id" class="export-job" data-export-job>
        <h3>{{ entry.name }}</h3>
        <template v-if="entry.job"><p>{{ entry.job.status_label }} · {{ entry.job.job_id }}</p><a v-if="entry.job.download_url" class="management-action-link primary" :href="entry.job.download_url">下载标准实验数据包</a><a v-if="entry.job.history_url" class="management-action-link" :href="`/manage/data/?tab=exports&course_id=${course}&job_id=${entry.job.job_id}`">查看导出记录</a></template>
        <template v-else-if="entry.error"><p class="error-block">{{ entry.error }}</p><button data-retry-export :disabled="creating" @click="retry(entry)">重试此实验</button></template>
        <p v-else>正在创建…</p>
      </article>
      <button :disabled="creating" @click="refresh">刷新状态</button>
    </div>
    <p v-if="error" class="error-block" role="alert">{{ error }}</p>
    <template #footer><button :disabled="creating" @click="emit('close')">关闭</button><button v-if="!jobs.length" class="primary" data-testid="create-export" :disabled="!canCreate" @click="create">{{ creating ? '正在创建…' : '确认并创建导出' }}</button></template>
  </SiteDialog>
</template>

<style scoped>
.export-job { padding: 12px 0 18px; border-bottom: 1px solid #e3e9f0; margin-bottom: 16px; }
.export-job h3 { margin: 0 0 10px; font-size: 15px; }
.export-job a { display: inline-flex; margin: 12px 12px 0 0; }
</style>
