<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import Alert from '@/design-system/components/Alert.vue'
import EmptyState from '@/design-system/components/EmptyState.vue'
import PageHeader from '@/design-system/components/PageHeader.vue'
import StatusBadge from '@/design-system/components/StatusBadge.vue'

import {
  getTeacherTeachingDataApi,
  routeQuery,
  type TeachingDataQuery,
  type TeachingDataResponse,
} from './api'
import './teacher-teaching-data.css'

const route = useRoute()
const router = useRouter()
const courseId = Number(route.params.courseId)
const api = getTeacherTeachingDataApi()
const data = ref<TeachingDataResponse>()
const error = ref('')
const search = ref('')

function tone(status: string) {
  if (status === 'completed') return 'success' as const
  if (status === 'data_error') return 'danger' as const
  if (status === 'in_progress' || status === 'pending_grading') return 'warning' as const
  return 'neutral' as const
}

function formatCount(numerator: number | null, denominator: number | null) {
  return numerator === null || denominator === null ? '—' : `${numerator} / ${denominator}`
}

function formatScore(score: string | null, maxScore: string | null) {
  if (score === null) return '—'
  return maxScore === null ? score : `${score} / ${maxScore}`
}

function formatDate(value: string) {
  return new Date(value).toLocaleString('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  })
}

async function load(query: TeachingDataQuery = routeQuery(route.query)) {
  error.value = ''
  try {
    data.value = await api.teaching(courseId, query)
    search.value = data.value.filters.q
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '教学数据加载失败。'
  }
}

function scopeQuery() {
  const current = routeQuery(route.query)
  return {
    experiment_id: current.experiment_id,
    class_id: current.class_id,
    class_snapshot_id: current.class_snapshot_id,
  }
}

async function navigate(query: TeachingDataQuery) {
  await router.push({ query })
  await load(query)
}

async function submitSearch() {
  await navigate({ ...scopeQuery(), q: search.value.trim() })
}

async function selectStudent(studentId: number) {
  await navigate({ ...scopeQuery(), q: search.value.trim(), student_id: studentId })
}

async function selectSession(sessionId: number) {
  const studentId = data.value?.filters.student_id
  if (studentId == null) return
  await navigate({
    ...scopeQuery(),
    q: search.value.trim(),
    student_id: studentId,
    session_id: sessionId,
  })
}

onMounted(() => load())
</script>

<template>
  <section class="kt-teaching-data">
    <PageHeader title="教学数据">
      <template #kicker>学生、会话与单题作答</template>
      <p>从学生汇总进入会话，再进入单题作答；评分仅使用当前有效 GradeRecord。</p>
    </PageHeader>

    <Alert v-if="error" tone="danger">{{ error }}</Alert>
    <p v-if="!data && !error" role="status">正在加载教学数据…</p>

    <template v-else-if="data">
      <EmptyState v-if="data.blocked_message" title="请选择教学范围">
        <p>{{ data.blocked_message }}。普通教学范围只需要确定实验和班级。</p>
      </EmptyState>

      <template v-else>
        <section class="kt-section kt-teaching-roster" aria-labelledby="teaching-students-title">
          <div class="kt-section-heading">
            <div>
              <p class="kt-kicker">班级 roster</p>
              <h2 id="teaching-students-title">学生数据</h2>
              <p>以班级 roster 为全集，共 {{ data.total_roster_count }} 名学生；没有实验记录的学生仍会显示。</p>
            </div>
          </div>
          <form class="kt-teaching-search" role="search" @submit.prevent="submitSearch">
            <label for="teaching-student-search">搜索姓名或学号</label>
            <div>
              <input id="teaching-student-search" v-model="search" type="search" placeholder="输入姓名或学号">
              <button class="kt-button kt-button--secondary" type="submit">搜索</button>
            </div>
          </form>
          <EmptyState v-if="!data.student_summaries.length" title="没有符合搜索条件的学生。" />
          <div v-else class="kt-card kt-table-card">
            <div class="kt-table-scroll">
              <table class="kt-table">
                <thead><tr><th>学号</th><th>姓名</th><th>数据状态</th><th class="kt-table-number">完成题数</th><th class="kt-table-number">已评分</th><th class="kt-table-number">正确率</th><th class="kt-table-number">得分</th></tr></thead>
                <tbody>
                  <tr v-for="student in data.student_summaries" :key="student.student_id">
                    <td><button class="kt-teaching-link" type="button" @click="selectStudent(student.student_id)">{{ student.student_number }}</button></td>
                    <td>{{ student.display_name }}</td>
                    <td><StatusBadge :label="student.data_status_label" :tone="tone(student.data_status)" /></td>
                    <td class="kt-table-number">{{ formatCount(student.submitted_count, student.attempt_count) }}</td>
                    <td class="kt-table-number">{{ student.graded_count ?? '—' }}</td>
                    <td class="kt-table-number">{{ student.correct_rate === null ? '—' : `${student.correct_rate}%` }}</td>
                    <td class="kt-table-number">{{ formatScore(student.score, student.max_score) }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </section>

        <section v-if="data.sessions.length" class="kt-section" aria-labelledby="teaching-sessions-title">
          <div class="kt-section-heading"><div><p class="kt-kicker">学生会话</p><h2 id="teaching-sessions-title">学生会话</h2></div></div>
          <div class="kt-card kt-table-card"><div class="kt-table-scroll"><table class="kt-table"><thead><tr><th>会话</th><th>状态</th><th class="kt-table-number">作答</th><th>开始时间</th></tr></thead><tbody><tr v-for="session in data.sessions" :key="session.id"><td><button class="kt-teaching-link" type="button" @click="selectSession(session.id)">Session {{ session.id }}</button></td><td><StatusBadge :label="session.status_label" :tone="tone(session.status)" /></td><td class="kt-table-number">{{ session.submitted_count }} / {{ session.attempt_count }}</td><td>{{ formatDate(session.started_at) }}</td></tr></tbody></table></div></div>
        </section>

        <section v-if="data.attempts.length" class="kt-section" aria-labelledby="teaching-attempts-title">
          <div class="kt-section-heading"><div><p class="kt-kicker">发生的事件与时间质量</p><h2 id="teaching-attempts-title">单题作答时间线</h2><p>只呈现发生过的事件与时间质量，不将其解释为心理状态。</p></div></div>
          <article v-for="attempt in data.attempts" :key="attempt.id" class="kt-card kt-teaching-attempt">
            <header><strong>{{ attempt.question_code }}</strong><StatusBadge :label="attempt.status_label" :tone="tone(attempt.status)" /><span>评分：{{ attempt.grade_score ?? '—' }}</span><span>时长：{{ attempt.timing.display_duration_ms === null ? '无法计算' : `${attempt.timing.display_duration_ms} ms（${attempt.timing.display_label}）` }}</span></header>
            <ol v-if="attempt.timeline.length" class="kt-teaching-timeline"><li v-for="event in attempt.timeline" :key="event.event_id"><time>{{ formatDate(event.occurred_at) }}</time><code>{{ event.event_type }}</code><span>{{ event.interval_quality }}</span></li></ol>
            <p v-else class="kt-empty-note">没有可显示的原始事件；该作答可能是历史数据。</p>
            <a class="kt-button kt-button--secondary" :href="attempt.raw_events_url">查看原始事件</a>
          </article>
        </section>
      </template>
    </template>
  </section>
</template>
