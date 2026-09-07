<script setup lang="ts">
import QuestionDirectoryPanel from '@/design-system/components/QuestionDirectoryPanel.vue'
import { computed, onMounted, ref } from 'vue'

import type { StudentLearningExperiment } from '../student-courses/api'
import { getStudentCourseApi } from '../student-courses/api'

const props = defineProps<{
  courseId: number
  experimentId: number
  questionId: number
}>()

const experiment = ref<StudentLearningExperiment>()
const itemResults = computed(() => experiment.value?.item_results || [])

onMounted(async () => {
  try {
    experiment.value = await getStudentCourseApi().learningExperiment(props.courseId, props.experimentId)
  } catch {
    // The detail page stays usable when its supplemental directory is unavailable.
  }
})
</script>

<template>
  <QuestionDirectoryPanel v-if="itemResults.length" class="kt-question-directory">
    <ol>
      <li v-for="(item, index) in itemResults" :key="item.question_id">
        <a
          :href="`/student/courses/${courseId}/learning/experiments/${experimentId}/questions/${item.question_id}/`"
          :aria-current="item.question_id === questionId ? 'page' : undefined"
        >
          <span>{{ index + 1 }}</span>{{ item.code }}
        </a>
      </li>
    </ol>
  </QuestionDirectoryPanel>
</template>
