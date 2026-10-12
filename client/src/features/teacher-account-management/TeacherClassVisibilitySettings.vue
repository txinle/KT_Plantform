<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'

import Alert from '@/design-system/components/Alert.vue'
import PageHeader from '@/design-system/components/PageHeader.vue'

import { getTeacherAccountManagementApi, type StudentVisibilityPolicy } from './api'
import './teacher-account-management.css'

const route = useRoute()
const api = getTeacherAccountManagementApi()
const courseId = Number(route.params.courseId)
const classId = Number(route.params.classId)
const policy = ref<StudentVisibilityPolicy>()
const loading = ref(true)
const submitting = ref(false)
const error = ref('')
const success = ref('')
const form = reactive({
  show_scores: true,
  show_correct_rate: true,
  show_item_results: true,
  show_class_average: true,
  rank_mode: 'self' as 'none' | 'self',
  show_kc_rates: true,
  show_trend: true,
})

function message(caught: unknown, fallback: string) {
  return caught instanceof Error ? caught.message : fallback
}

function applyPolicy(next: StudentVisibilityPolicy) {
  policy.value = next
  form.show_scores = next.show_scores
  form.show_correct_rate = next.show_correct_rate
  form.show_item_results = next.show_item_results
  form.show_class_average = next.show_class_average
  form.rank_mode = next.rank_mode
  form.show_kc_rates = next.show_kc_rates
  form.show_trend = next.show_trend
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    applyPolicy(await api.visibility(courseId, classId))
  } catch (caught) {
    error.value = message(caught, '学生端显示设置加载失败。')
  } finally {
    loading.value = false
  }
}

async function save() {
  submitting.value = true
  error.value = ''
  success.value = ''
  try {
    applyPolicy(await api.saveVisibility(courseId, classId, { ...form }))
    success.value = '学生端显示设置已保存。'
  } catch (caught) {
    error.value = message(caught, '学生端显示设置未保存。')
  } finally {
    submitting.value = false
  }
}

onMounted(load)
</script>

<template>
  <section class="kt-teacher-account-management">
    <PageHeader title="学生端显示设置">
      <template #kicker>班级反馈策略</template>
      <p>设置只控制学生自己的学习反馈；教师分析、研究事实和导出内容不受影响。</p>
      <template #actions><a class="kt-button kt-button--secondary" :href="`/teacher/courses/${courseId}/classes/${classId}/?section=manage`">返回班级管理</a></template>
    </PageHeader>

    <Alert v-if="error" tone="danger">{{ error }}</Alert>
    <Alert v-if="success" tone="success">{{ success }}</Alert>
    <p v-if="loading" role="status">正在加载学生端显示设置…</p>

    <form v-else-if="policy" class="kt-card kt-account-management-form" data-student-visibility-settings @submit.prevent="save">
      <Alert v-if="policy.is_read_only" tone="warning">课程已归档，历史策略继续生效，但不能修改。</Alert>
      <p class="kt-account-management-notice">最终可见内容仍受实验发布时的反馈模式、数据可用性和最小样本量约束。学生不会看到其他人的姓名、答案或过程轨迹。</p>

      <fieldset :disabled="policy.is_read_only || submitting">
        <legend>成绩信息</legend>
        <label class="kt-account-management-check"><input v-model="form.show_scores" name="show_scores" type="checkbox"> 显示实验成绩</label>
        <label class="kt-account-management-check"><input v-model="form.show_correct_rate" name="show_correct_rate" type="checkbox"> 显示正确率</label>
        <label class="kt-account-management-check"><input v-model="form.show_item_results" name="show_item_results" type="checkbox"> 显示逐题结果</label>
      </fieldset>

      <fieldset :disabled="policy.is_read_only || submitting">
        <legend>比较信息</legend>
        <label class="kt-account-management-check"><input v-model="form.show_class_average" name="show_class_average" type="checkbox"> 显示班级平均</label>
        <label>排名显示方式<select v-model="form.rank_mode" name="rank_mode"><option value="none">不展示</option><option value="self">仅本人</option></select></label>
      </fieldset>

      <fieldset :disabled="policy.is_read_only || submitting">
        <legend>学习分析</legend>
        <label class="kt-account-management-check"><input v-model="form.show_kc_rates" name="show_kc_rates" type="checkbox"> 显示知识点正确率</label>
        <label class="kt-account-management-check"><input v-model="form.show_trend" name="show_trend" type="checkbox"> 显示个人趋势</label>
      </fieldset>

      <div v-if="!policy.is_read_only" class="kt-account-management-actions"><button class="kt-button" type="submit" :disabled="submitting">保存显示设置</button><a class="kt-button kt-button--secondary" :href="`/teacher/courses/${courseId}/classes/${classId}/?section=manage`">取消</a></div>
    </form>
  </section>
</template>
