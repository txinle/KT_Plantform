<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import Alert from '@/design-system/components/Alert.vue'
import EmptyState from '@/design-system/components/EmptyState.vue'
import PageHeader from '@/design-system/components/PageHeader.vue'
import StatusBadge from '@/design-system/components/StatusBadge.vue'
import { ApiError } from '@/api/client'

import { getTeacherAccountManagementApi, type AccountStudent, type CourseClassManagement, type StudentImportPreview } from './api'
import './teacher-account-management.css'

type Workflow = 'manage' | 'manual' | 'copy' | 'import'
type ManagedEnrollment = CourseClassManagement['enrollments'][number]

interface IdentityEditCandidate {
  student_id: number
  student_number: string
  display_name: string
  state_token: string
  affects_other_courses: boolean
}

interface StudentNameConfirmation {
  submitted: { student_number: string; display_name: string }
  existing_name: string
  submitted_name: string
  confirmation_token: string
  affects_other_courses: boolean
}

const route = useRoute()
const router = useRouter()
const api = getTeacherAccountManagementApi()
const courseId = Number(route.params.courseId)
const classId = Number(route.params.classId)
const management = ref<CourseClassManagement>()
const loading = ref(true)
const error = ref('')
const success = ref('')
const workflow = ref<Workflow>(
  ['manual', 'copy', 'import'].includes(String(route.meta.workflow))
    ? String(route.meta.workflow) as Workflow
    : 'manage',
)
const classForm = reactive({ name: '', academic_year: '', is_active: true, state_token: '' })
const manualForm = reactive({ student_number: '', display_name: '' })
const copySourceClass = ref('')
const copyPreview = ref<{
  source_class: CourseClassManagement['source_classes'][number]
  preview: Array<{ student: AccountStudent; skip: boolean }>
}>()
const importPreview = ref<StudentImportPreview>()
const importFile = ref<File>()
const moveTargets = reactive<Record<number, string>>({})
const removeCandidate = ref<AccountStudent>()
const resetCandidate = ref<AccountStudent>()
const editCandidate = ref<IdentityEditCandidate>()
const nameConfirmation = ref<StudentNameConfirmation>()
const nameConfirmationDialog = ref<HTMLDialogElement>()
const deleteCandidate = ref(false)
const submitting = ref(false)

const currentClass = computed(() => management.value?.class_group)
const moveTargetClasses = computed(() => {
  if (!management.value) return []
  return management.value.target_classes.filter(
    (target) => target.id !== management.value?.class_group.id,
  )
})

function message(caught: unknown, fallback: string) {
  return caught instanceof Error ? caught.message : fallback
}

function applyManagement(payload: CourseClassManagement) {
  management.value = payload
  classForm.name = payload.class_group.name
  classForm.academic_year = payload.class_group.academic_year
  classForm.is_active = payload.class_group.is_active
  classForm.state_token = payload.class_group.state_token
  for (const enrollment of payload.enrollments) {
    if (moveTargets[enrollment.student.id] === undefined) moveTargets[enrollment.student.id] = ''
  }
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    applyManagement(await api.management(courseId, classId))
  } catch (caught) {
    error.value = message(caught, '班级管理信息加载失败。')
  } finally {
    loading.value = false
  }
}

function showWorkflow(next: Workflow) {
  workflow.value = next
  error.value = ''
  success.value = ''
}

async function saveClass() {
  submitting.value = true
  error.value = ''
  success.value = ''
  try {
    applyManagement(await api.updateManagement(courseId, classId, { ...classForm }))
    success.value = '班级设置已保存。'
  } catch (caught) {
    error.value = message(caught, '班级设置未保存。')
  } finally {
    submitting.value = false
  }
}

async function deleteClass() {
  if (!management.value) return
  submitting.value = true
  error.value = ''
  try {
    await api.deleteManagement(courseId, classId)
    await router.push(management.value.actions.classes_url)
  } catch (caught) {
    error.value = message(caught, '班级未删除。')
  } finally {
    submitting.value = false
  }
}

function captureNameConflict(
  caught: unknown,
  submitted: { student_number: string; display_name: string },
): boolean {
  if (!(caught instanceof ApiError) || caught.status !== 409 || caught.code !== 'STUDENT_NAME_CONFLICT') return false
  const { existing_name, submitted_name, confirmation_token, affects_other_courses } = caught.fields
  if (
    typeof existing_name !== 'string'
    || typeof submitted_name !== 'string'
    || typeof confirmation_token !== 'string'
    || typeof affects_other_courses !== 'boolean'
  ) return false
  nameConfirmation.value = {
    submitted,
    existing_name,
    submitted_name,
    confirmation_token,
    affects_other_courses,
  }
  return true
}

async function submitManualEnrollment(
  submitted: { student_number: string; display_name: string },
  confirmationToken?: string,
) {
  submitting.value = true
  error.value = ''
  success.value = ''
  try {
    await api.manualEnrollment(courseId, classId, {
      ...submitted,
      ...(confirmationToken ? { confirmation_token: confirmationToken } : {}),
    })
    nameConfirmation.value = undefined
    manualForm.student_number = ''
    manualForm.display_name = ''
    await load()
    success.value = '学生已加入当前班级。'
  } catch (caught) {
    if (captureNameConflict(caught, submitted)) return
    if (caught instanceof ApiError && caught.code === 'STALE_CONFIRMATION') {
      nameConfirmation.value = undefined
      await load()
    }
    error.value = message(caught, '未能添加学生。')
  } finally {
    submitting.value = false
  }
}

async function addManualStudent() {
  await submitManualEnrollment({
    student_number: manualForm.student_number,
    display_name: manualForm.display_name,
  })
}

async function confirmStudentName() {
  if (!nameConfirmation.value) return
  const pending = nameConfirmation.value
  await submitManualEnrollment(pending.submitted, pending.confirmation_token)
}

function cancelStudentNameConfirmation() {
  nameConfirmation.value = undefined
}

watch(nameConfirmation, async (pending) => {
  if (!pending) return
  await nextTick()
  const dialog = nameConfirmationDialog.value
  if (!dialog || dialog.open) return
  if (typeof dialog.showModal === 'function') dialog.showModal()
  else dialog.setAttribute('open', '')
})

function beginIdentityEdit(enrollment: ManagedEnrollment) {
  editCandidate.value = {
    student_id: enrollment.student.id,
    student_number: enrollment.student.student_number,
    display_name: enrollment.student.display_name,
    state_token: enrollment.identity_state_token,
    affects_other_courses: enrollment.affects_other_courses,
  }
  error.value = ''
  success.value = ''
}

async function saveStudentIdentity() {
  if (!editCandidate.value) return
  const submitted = { ...editCandidate.value }
  submitting.value = true
  error.value = ''
  success.value = ''
  try {
    const result = await api.updateStudentIdentity(courseId, classId, submitted.student_id, {
      student_number: submitted.student_number,
      display_name: submitted.display_name,
      state_token: submitted.state_token,
    })
    editCandidate.value = undefined
    await load()
    success.value = result.affects_other_courses
      ? '学生信息已更新。该修改会同步影响此学生在其他课程中的身份显示'
      : '学生信息已更新。'
  } catch (caught) {
    if (caught instanceof ApiError && caught.code === 'STALE_CONFIRMATION') {
      editCandidate.value = undefined
      await load()
    }
    error.value = message(caught, '学生信息未更新。')
  } finally {
    submitting.value = false
  }
}

async function previewCopy() {
  const sourceClassId = Number(copySourceClass.value)
  if (!sourceClassId) {
    error.value = '请选择来源课程班级。'
    return
  }
  submitting.value = true
  error.value = ''
  success.value = ''
  try {
    copyPreview.value = await api.previewCopy(courseId, classId, sourceClassId)
  } catch (caught) {
    error.value = message(caught, '复制预览生成失败。')
  } finally {
    submitting.value = false
  }
}

async function confirmCopy() {
  submitting.value = true
  error.value = ''
  success.value = ''
  try {
    const result = await api.confirmCopy(courseId, classId)
    copyPreview.value = undefined
    copySourceClass.value = ''
    await load()
    success.value = `已复制 ${result.copied} 名学生，跳过 ${result.skipped} 名已选课学生。`
  } catch (caught) {
    error.value = message(caught, '复制名单失败。')
  } finally {
    submitting.value = false
  }
}

function chooseImportFile(event: Event) {
  const input = event.target as HTMLInputElement
  importFile.value = input.files?.[0]
  importPreview.value = undefined
}

async function previewImport() {
  if (!importFile.value) {
    error.value = '请选择 Excel 文件。'
    return
  }
  submitting.value = true
  error.value = ''
  success.value = ''
  try {
    const body = new FormData()
    body.append('file', importFile.value)
    importPreview.value = await api.previewCourseImport(courseId, classId, body)
  } catch (caught) {
    error.value = message(caught, '名单校验失败。')
  } finally {
    submitting.value = false
  }
}

async function confirmImport() {
  submitting.value = true
  error.value = ''
  success.value = ''
  try {
    const result = await api.confirmCourseImport(courseId, classId)
    importPreview.value = undefined
    importFile.value = undefined
    await load()
    success.value = `已建立 ${result.enrollments} 条课程选课，其中新建 ${result.created_accounts} 个全局学生账号。`
  } catch (caught) {
    error.value = message(caught, '名单导入失败。')
  } finally {
    submitting.value = false
  }
}

async function moveStudent(student: AccountStudent) {
  const targetClassId = Number(moveTargets[student.id])
  if (!targetClassId) {
    error.value = '请选择转入班级。'
    return
  }
  submitting.value = true
  error.value = ''
  success.value = ''
  try {
    const result = await api.moveStudent(courseId, classId, student.id, targetClassId)
    await load()
    success.value = `${student.display_name} 已转入 ${result.class_group.display_name}。`
  } catch (caught) {
    error.value = message(caught, '转班失败。')
  } finally {
    submitting.value = false
  }
}

async function removeStudent() {
  if (!removeCandidate.value) return
  submitting.value = true
  error.value = ''
  success.value = ''
  try {
    await api.removeStudent(courseId, classId, removeCandidate.value.id)
    const name = removeCandidate.value.display_name
    removeCandidate.value = undefined
    await load()
    success.value = `${name} 已移出当前课程；全局账号和历史研究数据均已保留。`
  } catch (caught) {
    error.value = message(caught, '移出学生失败。')
  } finally {
    submitting.value = false
  }
}

async function resetPassword() {
  if (!resetCandidate.value) return
  submitting.value = true
  error.value = ''
  success.value = ''
  try {
    await api.resetCourseStudentPassword(courseId, classId, resetCandidate.value.id)
    const name = resetCandidate.value.display_name
    resetCandidate.value = undefined
    success.value = `${name} 的全局登录密码已重置。`
  } catch (caught) {
    error.value = message(caught, '密码重置失败。')
  } finally {
    submitting.value = false
  }
}

onMounted(load)
</script>

<template>
  <section class="kt-teacher-account-management">
    <PageHeader :title="currentClass?.name || '班级管理'">
      <template #kicker>{{ management ? `${management.course.code} · ${management.course.term}` : '课程班级' }}</template>
      <p v-if="management">{{ management.course.name }} · 管理班级信息、课程成员和导入流程。</p>
      <template #actions>
        <a v-if="management" class="kt-button kt-button--secondary" :href="management.actions.detail_url">返回班级中心</a>
        <a v-if="management" class="kt-button kt-button--secondary" :href="management.actions.visibility_url">学生端显示设置</a>
      </template>
    </PageHeader>

    <Alert v-if="error" tone="danger">{{ error }}</Alert>
    <Alert v-if="success" tone="success">{{ success }}</Alert>
    <p v-if="loading" role="status">正在加载班级管理信息…</p>

    <template v-else-if="management">
      <nav class="kt-account-management-tabs" aria-label="班级管理操作">
        <button type="button" :aria-current="workflow === 'manage' ? 'page' : undefined" @click="showWorkflow('manage')">班级与成员</button>
        <button type="button" :disabled="!management.capabilities.can_add_students" :aria-current="workflow === 'manual' ? 'page' : undefined" @click="showWorkflow('manual')">手动添加</button>
        <button type="button" :disabled="!management.capabilities.can_add_students" :aria-current="workflow === 'copy' ? 'page' : undefined" @click="showWorkflow('copy')">复制名单</button>
        <button type="button" :disabled="!management.capabilities.can_add_students" :aria-current="workflow === 'import' ? 'page' : undefined" @click="showWorkflow('import')">Excel 导入</button>
      </nav>

      <section v-if="workflow === 'manage'" class="kt-account-management-grid">
        <form class="kt-card kt-account-management-form" data-course-class-settings @submit.prevent="saveClass">
          <h2>班级设置</h2>
          <p>停用后不能新增、导入、复制或转入学生；历史名单仍可查看，且可转出学生或重置密码。</p>
          <input type="hidden" name="state_token" :value="classForm.state_token">
          <label>班级名称<input v-model="classForm.name" name="name" maxlength="120" required :disabled="!management.capabilities.can_update"></label>
          <label>年级<input v-model="classForm.academic_year" name="academic_year" inputmode="numeric" maxlength="32" :disabled="!management.capabilities.can_update"></label>
          <label class="kt-account-management-check"><input v-model="classForm.is_active" name="is_active" type="checkbox" :disabled="!management.capabilities.can_update"> 班级可用</label>
          <button class="kt-button" type="submit" :disabled="submitting || !management.capabilities.can_update">保存班级设置</button>
        </form>

        <aside class="kt-card kt-account-management-guide">
          <h2>成员操作</h2>
          <p>单个补充、批量导入和跨课程复制均在服务器端重新验证班级状态、成员身份和选课关系。</p>
          <a class="kt-button kt-button--secondary" :href="management.actions.import_template_url">下载 Excel 参考模板</a>
          <button v-if="management.capabilities.can_delete" class="kt-button kt-button--danger" data-course-class-delete type="button" :disabled="submitting" @click="deleteCandidate = true">删除空班级</button>
        </aside>
      </section>

      <section v-if="workflow === 'manual'" class="kt-card kt-account-management-form" aria-labelledby="manual-enrollment-title">
        <h2 id="manual-enrollment-title">手动添加单个学生</h2>
        <p>已有同学号、同姓名的全局账号会安全复用；新账号初始密码为 S + 学号。</p>
        <form data-manual-enrollment @submit.prevent="addManualStudent">
          <label>学号<input v-model="manualForm.student_number" name="student_number" maxlength="64" required :disabled="submitting"></label>
          <label>姓名<input v-model="manualForm.display_name" name="display_name" maxlength="80" required :disabled="submitting"></label>
          <div class="kt-account-management-actions"><button class="kt-button" type="submit" :disabled="submitting">添加到当前班级</button><button class="kt-button kt-button--secondary" type="button" @click="showWorkflow('manage')">返回管理</button></div>
        </form>
      </section>

      <section v-if="workflow === 'copy'" class="kt-card kt-account-management-form" aria-labelledby="copy-enrollment-title">
        <h2 id="copy-enrollment-title">从其他课程班级复制</h2>
        <p>只复用学生账号并建立当前课程选课，不改变来源课程关系、姓名或登录密码。</p>
        <form v-if="!copyPreview" @submit.prevent="previewCopy">
          <label>来源课程班级
            <select v-model="copySourceClass" required :disabled="submitting">
              <option value="">请选择来源课程班级</option>
              <option v-for="source in management.source_classes" :key="source.id" :value="source.id">{{ source.course_code }} · {{ source.course_term }} / {{ source.display_name }}</option>
            </select>
          </label>
          <button class="kt-button" type="submit" :disabled="submitting">生成复制预览</button>
        </form>
        <template v-else>
          <p class="kt-account-management-notice">来源：{{ copyPreview.source_class.course_code }} / {{ copyPreview.source_class.display_name }}。已在当前课程的学生将被跳过。</p>
          <div class="kt-table-scroll"><table class="kt-table"><thead><tr><th>学号</th><th>姓名</th><th>处理结果</th></tr></thead><tbody><tr v-for="row in copyPreview.preview" :key="row.student.id"><td>{{ row.student.student_number }}</td><td>{{ row.student.display_name }}</td><td><StatusBadge :tone="row.skip ? 'warning' : 'success'" :label="row.skip ? '将跳过' : '将复制'" /></td></tr></tbody></table></div>
          <div class="kt-account-management-actions"><button class="kt-button" type="button" :disabled="submitting" @click="confirmCopy">确认复制名单</button><button class="kt-button kt-button--secondary" type="button" :disabled="submitting" @click="copyPreview = undefined">重新选择</button></div>
        </template>
      </section>

      <section v-if="workflow === 'import'" class="kt-card kt-account-management-form" aria-labelledby="excel-import-title">
        <h2 id="excel-import-title">批量导入课程学生</h2>
        <p>上传后先在服务器校验预览；确认时一次性建立选课，任一冲突都会使整批回滚。</p>
        <a class="kt-button kt-button--secondary" :href="management.actions.import_template_url">下载 Excel 参考模板</a>
        <form v-if="!importPreview" @submit.prevent="previewImport">
          <label>Excel 学生名单<input accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" type="file" required :disabled="submitting" @change="chooseImportFile"></label>
          <button class="kt-button" type="submit" :disabled="submitting">校验并生成预览</button>
        </form>
        <template v-else>
          <p class="kt-account-management-notice">名单校验通过，尚未写入数据库。</p>
          <div class="kt-table-scroll"><table class="kt-table"><thead><tr><th>学号</th><th>姓名</th><th>课程班级</th><th>账号处理</th></tr></thead><tbody><tr v-for="row in importPreview.preview" :key="row.student_number"><td>{{ row.student_number }}</td><td>{{ row.display_name }}</td><td>{{ row.class_name }}</td><td>{{ row.account_action === 'reuse' ? '复用已有账号' : '新建账号（S + 学号）' }}</td></tr></tbody></table></div>
          <div class="kt-account-management-actions"><button class="kt-button" type="button" :disabled="submitting" @click="confirmImport">确认建立 {{ importPreview.preview.length }} 条选课关系</button><button class="kt-button kt-button--secondary" type="button" :disabled="submitting" @click="importPreview = undefined">重新上传</button></div>
        </template>
      </section>

      <section v-if="workflow === 'manage'" class="kt-card kt-account-management-members" aria-labelledby="class-member-title">
        <div class="kt-section-heading"><h2 id="class-member-title">当前课程成员</h2><span>{{ management.enrollments.length }} 人</span></div>
        <EmptyState v-if="!management.enrollments.length" title="当前班级没有学生"><p>可通过手动添加、Excel 导入或复制名单建立课程成员关系。</p></EmptyState>
        <div v-else class="kt-table-scroll"><table class="kt-table"><thead><tr><th>学号</th><th>姓名</th><th>转入班级</th><th>账号操作</th><th>课程成员操作</th></tr></thead><tbody><tr v-for="enrollment in management.enrollments" :key="enrollment.enrollment_id"><td>{{ enrollment.student.student_number }}</td><td>{{ enrollment.student.display_name }}</td><td><div class="kt-account-management-inline"><select v-model="moveTargets[enrollment.student.id]" :disabled="submitting || !management.capabilities.can_move_students"><option value="">选择班级</option><option v-for="target in moveTargetClasses" :key="target.id" :value="target.id">{{ target.display_name }}</option></select><button class="kt-button kt-button--secondary" type="button" :disabled="submitting || !management.capabilities.can_move_students" @click="moveStudent(enrollment.student)">转班</button></div></td><td><div class="kt-account-management-inline"><button class="kt-button kt-button--secondary" data-edit-student-identity type="button" :disabled="submitting || !management.capabilities.can_edit_students" @click="beginIdentityEdit(enrollment)">编辑信息</button><button class="kt-button kt-button--secondary" data-course-reset-password type="button" :disabled="submitting || !management.capabilities.can_reset_passwords" @click="resetCandidate = enrollment.student">重置密码</button></div></td><td><button class="kt-button kt-button--danger" type="button" :disabled="submitting || !management.capabilities.can_remove_students" @click="removeCandidate = enrollment.student">移出课程</button></td></tr></tbody></table></div>
      </section>

      <section v-if="editCandidate" class="kt-card kt-account-management-confirm" aria-labelledby="edit-student-identity-title">
        <h2 id="edit-student-identity-title">编辑学生信息</h2>
        <p>仅修改该学生账号的学号和姓名；登录密码及首次改密状态保持不变。</p>
        <p v-if="editCandidate.affects_other_courses" class="kt-account-management-notice">该修改会同步影响此学生在其他课程中的身份显示</p>
        <form data-student-identity-form @submit.prevent="saveStudentIdentity">
          <label>学号<input v-model="editCandidate.student_number" name="student_number" maxlength="64" required :disabled="submitting"></label>
          <label>姓名<input v-model="editCandidate.display_name" name="display_name" maxlength="80" required :disabled="submitting"></label>
          <div class="kt-account-management-actions"><button class="kt-button" type="submit" :disabled="submitting">保存学生信息</button><button class="kt-button kt-button--secondary" type="button" :disabled="submitting" @click="editCandidate = undefined">取消</button></div>
        </form>
      </section>

      <dialog v-if="nameConfirmation" ref="nameConfirmationDialog" class="kt-card kt-account-management-confirm kt-account-management-dialog" role="dialog" aria-modal="true" aria-labelledby="student-name-conflict-title" @cancel.prevent="cancelStudentNameConfirmation">
        <h2 id="student-name-conflict-title">确认更新已有学生姓名</h2>
        <p>学号 {{ nameConfirmation.submitted.student_number }} 当前对应“{{ nameConfirmation.existing_name }}”，本次提交姓名为“{{ nameConfirmation.submitted_name }}”。</p>
        <p v-if="nameConfirmation.affects_other_courses" class="kt-account-management-notice">该修改会同步影响此学生在其他课程中的身份显示</p>
        <div class="kt-account-management-actions"><button class="kt-button" data-confirm-student-name type="button" :disabled="submitting" @click="confirmStudentName">确认更正并加入</button><button class="kt-button kt-button--secondary" data-cancel-student-name type="button" :disabled="submitting" @click="cancelStudentNameConfirmation">取消</button></div>
      </dialog>

      <section v-if="resetCandidate" class="kt-card kt-account-management-confirm" aria-labelledby="reset-password-title">
        <h2 id="reset-password-title">重置 {{ resetCandidate.display_name }} 的全局登录密码</h2>
        <p>重置后密码将变为 S + 当前学号（S{{ resetCandidate.student_number }}），该学生现有登录会话将全部失效。</p>
        <div class="kt-account-management-actions"><button class="kt-button" data-course-reset-password-confirm type="button" :disabled="submitting" @click="resetPassword">确认重置</button><button class="kt-button kt-button--secondary" type="button" :disabled="submitting" @click="resetCandidate = undefined">取消</button></div>
      </section>

      <section v-if="removeCandidate" class="kt-card kt-account-management-confirm" aria-labelledby="remove-student-title">
        <h2 id="remove-student-title">移出 {{ removeCandidate.display_name }}</h2>
        <p>该操作会删除当前课程选课关系，并清理仅属于草稿实验的参与链接；全局账号和已发布研究记录会保留。</p>
        <div class="kt-account-management-actions"><button class="kt-button kt-button--danger" type="button" :disabled="submitting" @click="removeStudent">确认移出课程</button><button class="kt-button kt-button--secondary" type="button" :disabled="submitting" @click="removeCandidate = undefined">取消</button></div>
      </section>

      <section v-if="deleteCandidate" class="kt-card kt-account-management-confirm" aria-labelledby="delete-class-title">
        <h2 id="delete-class-title">删除 {{ currentClass?.name }}</h2>
        <p>仅当班级没有关联学生、选课关系或实验参与规则时才能删除。删除后将无法恢复班级和学生端显示设置。</p>
        <div class="kt-account-management-actions"><button class="kt-button kt-button--danger" data-course-class-delete-confirm type="button" :disabled="submitting" @click="deleteClass">确认删除班级</button><button class="kt-button kt-button--secondary" type="button" :disabled="submitting" @click="deleteCandidate = false">取消</button></div>
      </section>
    </template>
  </section>
</template>
