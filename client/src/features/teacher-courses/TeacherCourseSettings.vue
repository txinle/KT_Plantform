<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ApiError } from '@/api/client'
import TeacherCourseShell from './TeacherCourseShell.vue'
import SiteWorkspaceShell from '@/features/management-shared/SiteWorkspaceShell.vue'
import SiteDialog from '@/features/management-shared/SiteDialog.vue'
import { getAdminApi } from '@/features/admin-workspace/api'
import type { TeacherCourseSettingsResponse } from './api'
import { getTeacherCourseApi } from './api'
import { collaborationApi, type CollaborationMember, type CollaborationPerson, type CourseCollaboration } from './collaboration-api'

const route = useRoute()
const courseId = Number(route.params.courseId)
const isAdminWorkspace = computed(() => route.path.startsWith('/manage/'))
const settings = ref<TeacherCourseSettingsResponse>()
const collaboration = ref<CourseCollaboration>()
const form = reactive({ name: '', code: '', term: '', description: '', state_token: '', teachers: [] as number[] })
const fieldErrors = ref<Record<string, unknown>>({})
const error = ref('')
const saved = ref('')
const busy = ref(false)
const modal = ref<'member' | 'remove' | 'archive' | 'transfer' | 'purge' | 'purge-execute' | ''>('')
const modalError = ref('')
const editing = ref<CollaborationMember>()
const selected = ref<CollaborationPerson>()
const query = ref('')
const results = ref<CollaborationPerson[]>([])
const searched = ref(false)
const searching = ref(false)
const selectedPermissions = ref<string[]>([])
const transferReason = ref('')
const keepPrevious = ref(true)
const purgeReason = ref('research_withdrawal')
const purgeId = ref('')
let searchSequence = 0
const archived = computed(() => settings.value?.course.status === 'archived')
const canGovern = computed(() => collaboration.value?.capabilities.can_manage_members || (archived.value && settings.value?.capabilities.can_restore))
const permissionDescriptions: Record<string, string> = { bank: '维护题目、题库与知识点', class: '管理班级与学生选课', experiment: '配置、发布和管理实验', data: '查看本课程统计与结果', export: '下载本课程授权范围的数据' }
const permissionName = (key: string) => collaboration.value?.permission_options.find(option => option.key === key)?.label || key
const errorText = (value: unknown) => Array.isArray(value) ? value.join(' ') : String(value || '')
function hydrate(response: TeacherCourseSettingsResponse) { settings.value = response; Object.assign(form, response.form, { teachers: [...response.form.teachers] }) }
async function load() {
  error.value = ''
  try {
    if (isAdminWorkspace.value) await getAdminApi().get('access/')
    const [response, members] = await Promise.all([getTeacherCourseApi().settings(courseId), collaborationApi.get(courseId)])
    hydrate(response); collaboration.value = members
  }
  catch (caught) { error.value = caught instanceof Error ? caught.message : '课程设置加载失败。' }
}
onMounted(load)
async function submit() {
  if (!settings.value?.capabilities.can_update || busy.value) return
  busy.value = true; error.value = ''; saved.value = ''; fieldErrors.value = {}
  try {
    hydrate(await getTeacherCourseApi().saveSettings(courseId, { name: form.name, code: form.code, term: form.term, description: form.description, state_token: form.state_token, teachers: [...form.teachers] }))
    collaboration.value = await collaborationApi.get(courseId)
    saved.value = '课程设置已保存。'
  } catch (caught) {
    if (caught instanceof ApiError) fieldErrors.value = caught.fields
    error.value = caught instanceof Error ? caught.message : '课程设置保存失败。'
  } finally { busy.value = false }
}
function openMember(member?: CollaborationMember) {
  editing.value = member; selected.value = member; selectedPermissions.value = [...(member?.permissions || [])]
  query.value = ''; results.value = []; searched.value = false; modalError.value = ''; modal.value = 'member'
}
function openRemove(member: CollaborationMember) { editing.value = member; modalError.value = ''; modal.value = 'remove' }
function openTransfer() { selected.value = undefined; query.value = ''; results.value = []; searched.value = false; transferReason.value = ''; keepPrevious.value = true; modalError.value = ''; modal.value = 'transfer' }
function openGovernance(value: 'archive' | 'purge' | 'purge-execute', id = '') { purgeId.value = id; modalError.value = ''; modal.value = value }
async function searchTeachers() {
  const sequence = ++searchSequence
  if (!query.value.trim()) { results.value = []; searched.value = false; return }
  searching.value = true; modalError.value = ''
  try {
    const response = await collaborationApi.search(courseId, query.value.trim())
    if (sequence === searchSequence) { results.value = response.items; searched.value = true }
  } catch (caught) { if (sequence === searchSequence) modalError.value = caught instanceof Error ? caught.message : '教师查询失败。' }
  finally { if (sequence === searchSequence) searching.value = false }
}
function adjustPermission(key: string) {
  if (key === 'export' && selectedPermissions.value.includes('export') && !selectedPermissions.value.includes('data')) selectedPermissions.value.push('data')
  if (key === 'data' && !selectedPermissions.value.includes('data')) selectedPermissions.value = selectedPermissions.value.filter(value => value !== 'export')
}
async function mutateMember(action: 'add' | 'update' | 'remove' | 'transfer') {
  if (!collaboration.value || busy.value) return
  const teacher = action === 'remove' ? editing.value : selected.value
  if (!teacher) { modalError.value = '请先搜索并选择一位教师。'; return }
  if (['add', 'update'].includes(action) && !selectedPermissions.value.length) { modalError.value = '请至少选择一项功能权限。'; return }
  if (action === 'transfer' && !transferReason.value.trim()) { modalError.value = '请填写移交原因。'; return }
  busy.value = true; modalError.value = ''
  try {
    const response = await collaborationApi.mutate(courseId, {
      action, teacher_id: teacher.id, state_token: collaboration.value.state_token,
      ...(['add', 'update'].includes(action) ? { permissions: [...selectedPermissions.value] } : {}),
      ...(action === 'transfer' ? { keep_previous: keepPrevious.value, reason: transferReason.value.trim() } : {}),
    })
    collaboration.value = response
    if (response.redirect_url && !isAdminWorkspace.value) { window.location.assign(response.redirect_url); return }
    hydrate(await getTeacherCourseApi().settings(courseId))
    modal.value = ''; saved.value = { add: '协作教师已添加。', update: '协作权限已更新。', remove: '协作成员已移除。', transfer: '课程负责人已移交。' }[action]
  } catch (caught) { modalError.value = caught instanceof Error ? caught.message : '协作变更失败，请刷新后重试。' }
  finally { busy.value = false }
}
async function governance() {
  if (busy.value) return
  busy.value = true; modalError.value = ''
  try {
    const api = getTeacherCourseApi()
    if (modal.value === 'archive') {
      hydrate(archived.value ? await api.restore(`/api/v1/teacher/courses/${courseId}/restore/`) : await api.archive(courseId))
      saved.value = '课程状态已更新。'
    } else if (modal.value === 'purge') {
      hydrate(await api.createPurgeRequest(courseId, purgeReason.value)); saved.value = '数据清除影响预览已生成，请核对清除请求。'
    } else {
      hydrate(await api.executePurgeRequest(courseId, purgeId.value)); saved.value = '数据清除请求已执行。'
    }
    collaboration.value = await collaborationApi.get(courseId); modal.value = ''
  } catch (caught) { modalError.value = caught instanceof Error ? caught.message : '课程治理操作失败。' }
  finally { busy.value = false }
}
</script>

<template>
  <component
    :is="isAdminWorkspace ? SiteWorkspaceShell : TeacherCourseShell"
    class="course-settings-workspace"
    v-bind="isAdminWorkspace
      ? { mode: 'admin', active: 'course', mainId: 'admin-course-settings-main' }
      : { courseId, course: settings?.course, active: 'settings', mainId: 'teacher-course-settings-main' }"
  >
    <div :class="{ 'management-workspace teacher-settings-panel': !isAdminWorkspace }">
      <p v-if="!settings && !error" role="status">正在加载课程设置…</p>
      <p v-if="error" class="error-block" role="alert">{{ errorText(fieldErrors.__all__ || fieldErrors.non_field_errors) || error }} <button class="link" @click="load">重新加载</button></p>
      <p v-if="saved" class="success-note" role="status">{{ saved }}</p>
      <template v-if="settings && collaboration">
        <div class="eyebrow">{{ settings.course.code }} / {{ settings.course.term }}</div>
        <div class="heading"><div><h1>课程设置</h1><p>维护课程信息，为每位协作教师分配合适的工作范围。</p></div><span class="badge" :class="{ gray: archived }">{{ archived ? '已归档 · 只读' : '● 进行中' }}</span></div>
        <div class="course-grid">
          <div class="course-main">
            <form class="card course-basics" data-course-settings @submit.prevent="submit">
              <div class="card-header"><div><h2>基本信息</h2><p>课程标识与教学安排</p></div><button v-if="settings.capabilities.can_update" class="link" type="submit" :disabled="busy">{{ busy ? '正在保存…' : '保存信息' }}</button><span v-else class="badge gray">只读</span></div>
              <div class="info-grid">
                <label>课程名称<input v-model="form.name" name="name" maxlength="160" required :disabled="!settings.capabilities.can_update"><small v-if="fieldErrors.name" class="error">{{ errorText(fieldErrors.name) }}</small></label>
                <label>课程代码<input v-model="form.code" name="code" maxlength="64" required :disabled="!settings.capabilities.can_update"><small v-if="fieldErrors.code" class="error">{{ errorText(fieldErrors.code) }}</small></label>
                <label>学期<input v-model="form.term" name="term" maxlength="64" required :disabled="!settings.capabilities.can_update"></label>
                <label>课程负责人<input :value="`${collaboration.owner.display_name} · ${collaboration.owner.username}`" readonly></label>
              </div>
              <details class="course-description"><summary>课程说明</summary><label class="field">说明内容<textarea v-model="form.description" name="description" rows="3" :disabled="!settings.capabilities.can_update" /></label></details>
            </form>
            <section class="card collaboration-card">
              <div class="card-header"><div><h2>协作教师 <span class="member-count">{{ collaboration.members.length }} 位</span></h2><p>仅显示本课程成员，由负责人分配功能权限。</p></div><button v-if="collaboration.capabilities.can_manage_members" class="primary" data-add-collaborator @click="openMember()">＋ 添加协作教师</button></div>
              <div class="member-grid">
                <article class="member-tile owner"><div class="person"><span class="avatar">{{ collaboration.owner.display_name.slice(0, 1) }}</span><div><strong>{{ collaboration.owner.display_name }}</strong><small>{{ collaboration.owner.username }} · 负责课程与成员授权</small></div></div><span class="badge blue">负责人 · 全部功能</span></article>
                <article v-for="member in collaboration.members" :key="member.id" class="member-tile member"><div class="person"><span class="avatar">{{ member.display_name.slice(0, 1) }}</span><div><strong>{{ member.display_name }} <span v-if="member.is_active === false" class="badge gray">账号已停用</span></strong><small>{{ member.username }}</small></div></div><div class="permission-tags"><span v-for="permission in member.permissions" :key="permission">{{ permissionName(permission) }}</span></div><div v-if="collaboration.capabilities.can_manage_members" class="member-actions"><button class="link" :data-edit-collaborator="member.id" @click="openMember(member)">调整权限</button><button class="link danger" :data-remove-collaborator="member.id" @click="openRemove(member)">移除</button></div></article>
                <div v-if="!collaboration.members.length" class="member-empty">暂未添加协作教师。<span v-if="collaboration.capabilities.can_manage_members">通过右上方按钮搜索并添加。</span></div>
              </div>
            </section>
            <div class="policy-note"><span>ⓘ</span><div><strong>账号与课程授权分开管理</strong><br>管理员创建教师账号后，教师不会自动加入课程。协作权限只在当前课程生效，移除成员不删除账号和历史操作。</div></div>
          </div>
          <div class="course-side">
            <section class="card side-card"><div class="eyebrow">ACCESS CONTROL</div><h2>让协作范围清晰可见</h2><p>课程负责人按实际分工勾选主要功能，可随时调整。</p><ul><li v-for="option in collaboration.permission_options" :key="option.key">{{ option.label }}</li></ul><div class="divider" /><small>协作成员授权、负责人变更与课程删除不包含在这些勾选项中。数据导出须明确授权，并同时拥有数据查看权限。</small><button v-if="collaboration.capabilities.can_transfer" class="link transfer-owner" @click="openTransfer">移交课程负责人</button></section>
            <section class="card side-card"><h2>课程{{ archived ? '恢复' : '归档' }}</h2><p>{{ archived ? '恢复后可继续管理成员与教学安排。历史排期不会自动延长。' : '归档后课程进入只读状态，保留已有成员、教学记录与数据。' }}</p><button v-if="collaboration.capabilities.can_archive || settings.capabilities.can_restore" class="wide" :class="{ danger: !archived }" data-course-archive @click="openGovernance('archive')">{{ archived ? '恢复课程' : '归档课程' }}</button><small v-else>由课程负责人或平台管理员操作。</small></section>
          </div>
        </div>
        <details v-if="canGovern" class="card course-governance"><summary>身份与数据治理</summary><div class="governance-body"><h2>受控数据清除</h2><p>课程导出包含学生学号和姓名等身份数据。清除会话与采集数据不等于删除课程，执行前需生成并核对服务端影响预览。</p><button data-purge-create @click="openGovernance('purge')">生成清除预览</button><h3>清除请求</h3><div v-if="settings.purge_requests.length" class="table-scroll"><table><thead><tr><th>清除请求</th><th>预览数量</th><th>状态</th><th>操作</th></tr></thead><tbody><tr v-for="request in settings.purge_requests" :key="request.purge_request_id"><td>{{ request.purge_request_id }}</td><td>{{ request.preview_session_count }} 会话 · {{ request.preview_event_count }} 事件</td><td>{{ request.status_label }}</td><td><button v-if="request.execute_url" class="link danger" :data-purge-execute="request.purge_request_id" @click="openGovernance('purge-execute', request.purge_request_id)">执行清除</button></td></tr></tbody></table></div><p v-else>尚无清除请求。</p></div></details>
      </template>

      <SiteDialog :open="modal === 'member' || modal === 'transfer'" :title="modal === 'transfer' ? '移交课程负责人' : editing ? '调整协作权限' : '添加协作教师'" :subtitle="`${settings?.course.name || '课程'} · 授权仅对当前课程生效`" @close="!busy && (modal = '')">
        <template v-if="!editing || modal === 'transfer'">
          <div class="step-label"><span>1</span>{{ modal === 'transfer' ? '搜索新负责人' : '搜索教师' }}</div>
          <form class="teacher-search" data-search-teachers @submit.prevent="searchTeachers"><div class="search"><input v-model="query" aria-label="搜索教师姓名或账号" placeholder="输入教师姓名或登录账号" autocomplete="off"></div><button type="submit" :disabled="searching">{{ searching ? '查询中…' : '查询' }}</button></form>
          <div class="results"><div v-if="!searched" class="empty">输入姓名或账号后查询，不展示全平台教师名单。</div><div v-else-if="!results.length" class="empty">没有找到启用的教师账号。</div><button v-for="person in results" :key="person.id" class="result" :class="{ chosen: selected?.id === person.id }" :data-teacher-id="person.id" :disabled="modal === 'member' && collaboration?.members.some(member => member.id === person.id)" @click="selected = person"><span class="avatar">{{ person.display_name.slice(0, 1) }}</span><div class="person"><div><strong>{{ person.display_name }}</strong><small>{{ person.username }}</small></div></div><span v-if="collaboration?.members.some(member => member.id === person.id)" class="badge gray">已有成员</span><span v-else-if="selected?.id === person.id" class="badge blue">已选择</span></button></div>
        </template>
        <div v-else class="selected-person"><div class="person"><span class="avatar">{{ editing.display_name.slice(0, 1) }}</span><div><strong>{{ editing.display_name }}</strong><small>{{ editing.username }}</small></div></div></div>
        <template v-if="modal === 'member'"><div class="step-label"><span>{{ editing ? '✓' : '2' }}</span>选择可使用的功能</div><div class="permission-grid"><label v-for="option in collaboration?.permission_options" :key="option.key" class="permission-option"><input v-model="selectedPermissions" type="checkbox" :value="option.key" @change="adjustPermission(option.key)"><div><strong>{{ option.label }}</strong><small>{{ permissionDescriptions[option.key] }}</small></div></label></div><p class="hint">至少选择一项功能。数据查看与导出分开授权；选择导出时会同时启用数据查看。成员管理与课程删除不在授权范围内。</p></template>
        <template v-else><label class="permission-option"><input v-model="keepPrevious" type="checkbox"><div><strong>原负责人保留为协作教师</strong><small>仅在原账号仍启用时保留，授予题库、班级、实验及数据查看权限。</small></div></label><label class="field transfer-reason">移交原因<textarea v-model="transferReason" rows="3" required placeholder="说明人员或教学安排变化" /></label><p class="hint">课程历史记录保持不变。未保留原负责人时，其本课程管理权限将被移除。</p></template>
        <p v-if="modalError" class="error" role="alert">{{ modalError }}</p>
        <template #footer><small>由课程负责人授权</small><button :disabled="busy" @click="modal = ''">取消</button><button class="primary" data-save-collaborator :disabled="busy" @click="mutateMember(modal === 'transfer' ? 'transfer' : editing ? 'update' : 'add')">{{ busy ? '正在保存…' : modal === 'transfer' ? '确认移交' : editing ? '保存权限' : '确认添加' }}</button></template>
      </SiteDialog>
      <SiteDialog :open="modal === 'remove'" title="移除协作教师" :subtitle="editing ? `${editing.display_name} · ${editing.username}` : ''" @close="!busy && (modal = '')"><p>移除后，该教师将失去本课程的协作访问权限。其平台账号、其他课程权限与历史操作记录均保留。</p><p v-if="modalError" class="error" role="alert">{{ modalError }}</p><template #footer><button :disabled="busy" @click="modal = ''">取消</button><button class="primary" data-confirm-remove :disabled="busy" @click="mutateMember('remove')">{{ busy ? '正在移除…' : '确认移除' }}</button></template></SiteDialog>
      <SiteDialog :open="modal === 'archive'" :title="archived ? '恢复课程' : '归档课程'" :subtitle="settings?.course.name" @close="!busy && (modal = '')"><p>{{ archived ? '恢复后可继续调整协作成员和功能权限。历史任务排期不会自动延长。' : '归档后课程只读，已有成员与历史数据保留。归档不等同于结束所有班级任务，请先核对教学安排。' }}</p><p v-if="modalError" class="error" role="alert">{{ modalError }}</p><template #footer><button :disabled="busy" @click="modal = ''">取消</button><button class="primary" data-confirm-archive :disabled="busy" @click="governance">{{ busy ? '正在处理…' : archived ? '确认恢复' : '确认归档' }}</button></template></SiteDialog>
      <SiteDialog :open="modal === 'purge' || modal === 'purge-execute'" :title="modal === 'purge' ? '生成数据清除预览' : '确认执行数据清除'" @close="!busy && (modal = '')"><label v-if="modal === 'purge'" class="field">清除原因<select v-model="purgeReason" name="reason_code"><option value="research_withdrawal">学生退出研究</option><option value="collection_error">错误采集纠正</option><option value="retention_expired">保留期限结束</option></select></label><p v-else>将执行清除请求 {{ purgeId }}。请核对上方请求中的会话和事件数量，清除后无法恢复相关原始记录。</p><p v-if="modalError" class="error" role="alert">{{ modalError }}</p><template #footer><button :disabled="busy" @click="modal = ''">取消</button><button class="primary" :disabled="busy" @click="governance">{{ busy ? '正在处理…' : modal === 'purge' ? '生成影响预览' : '确认执行清除' }}</button></template></SiteDialog>
    </div>
  </component>
</template>

<style scoped>
.management-workspace.teacher-settings-panel {
  --ink: var(--kt-ink);
  --muted: var(--kt-muted);
  --line: var(--kt-border);
  --blue: var(--kt-action-600);
  --navy: var(--kt-brand-900);
  min-height: 0;
  padding: 0;
  background: transparent;
  color: var(--kt-ink);
  font: inherit;
}
.teacher-settings-panel .eyebrow { font-size: var(--kt-font-sm); letter-spacing: normal; }
.teacher-settings-panel .card h2 { font-size: var(--kt-font-lg); }
.teacher-settings-panel :is(.card-header p, .side-card p, .side-card li, .side-card small,
  .person small, .member-empty span, .policy-note, .course-description, .member-count) {
  font-size: var(--kt-font-sm);
  color: var(--kt-muted);
}
.teacher-settings-panel .heading h1 { font-size: var(--kt-font-title); line-height: 1.35; }
.teacher-settings-panel .heading p, .teacher-settings-panel .info-grid label { font-size: var(--kt-font-base); }
.course-settings-workspace :deep(.workspace-content){padding:22px 28px 28px}
.eyebrow{margin-bottom:6px}
.heading{margin-bottom:18px}
.course-grid{gap:16px;align-items:start}
.course-main{display:flex;flex-direction:column;gap:14px;min-width:0}
.course-main>.card+.card{margin-top:0}
.course-main>.policy-note{margin-top:0;padding:10px 14px}
.course-main .card-header{padding:14px 18px;gap:12px}
.course-basics .info-grid{padding:16px 18px;gap:12px 16px}
.course-basics .info-grid label{gap:6px}
.member-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,250px),1fr));gap:12px;padding:14px 18px}
.member-grid .member-tile{display:flex;flex-direction:column;align-items:stretch;justify-content:flex-start;gap:10px;min-width:0;padding:14px;border:1px solid var(--line);border-radius:7px;background:#fff}
.member-grid .owner{background:#f7faff}
.member-tile .person{align-items:flex-start;gap:10px}
.member-tile .person>div{min-width:0;overflow-wrap:anywhere}
.member-tile .avatar{flex-shrink:0}
.member-tile .permission-tags{margin:0;gap:5px;max-width:none}
.member-tile>.badge{align-self:flex-start;margin-left:0}
.member-empty{display:flex;flex-direction:column;justify-content:center;gap:5px;padding:14px;border:1px dashed var(--line);border-radius:7px;color:var(--muted);font-size:12px}
.member-empty span{font-size:11px}
.member-count{font-size:12px;color:#8799ad;font-weight:400}
.owner strong{font-size:13px;font-weight:500}
.member-actions{display:flex;justify-content:flex-end;gap:4px;flex-shrink:0;margin-top:auto;padding-top:6px;border-top:1px solid var(--line)}
.course-description{padding:0 18px 14px;font-size:12px;color:#738095}
.course-description summary{cursor:pointer}
.course-description label{margin-top:12px}
.course-side{display:grid;gap:14px}
.course-side .side-card{padding:18px;margin:0}
.course-side .side-card h2{margin-bottom:8px}
.course-side .side-card p{margin-bottom:10px;line-height:1.7}
.course-side .side-card ul{display:grid;grid-template-columns:1fr 1fr;gap:4px 14px;margin:0;line-height:1.8}
.course-side .side-card .divider{margin:12px 0}
.course-side .transfer-owner{margin-top:10px}
.teacher-search{display:flex;gap:9px}
.teacher-search .search{flex:1;width:auto}
.transfer-reason{margin-top:18px}
.course-governance{margin-top:14px;padding:12px 18px;font-size:13px}
.course-governance>summary{cursor:pointer;color:#536b8a}
.governance-body{padding-top:20px}
.governance-body>button{margin-top:15px}
@media(min-width:1600px){.course-basics .info-grid{grid-template-columns:repeat(4,minmax(0,1fr))}}
@media(max-width:700px){.course-settings-workspace :deep(.workspace-content){padding:18px 16px}.member-grid{grid-template-columns:1fr;padding:14px}.course-main .card-header{padding:14px}.course-main .card-header button,.member-actions button{min-height:44px}.course-basics .info-grid{grid-template-columns:1fr;padding:14px}.course-basics .info-grid input{min-height:44px}.course-description{padding-inline:14px}.course-governance{padding:14px}.teacher-search{align-items:stretch}}
</style>
