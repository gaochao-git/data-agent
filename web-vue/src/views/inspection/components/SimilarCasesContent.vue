<template>
  <div>
    <div v-if="!isCompleted" class="flex flex-col items-center justify-center py-12 text-gray-500 dark:text-gray-400">
      <i class="fa-solid fa-hourglass-half mb-3 text-2xl"></i>
      <p class="text-sm">等待事实认定分析完成...</p>
    </div>

    <div v-else-if="loading" class="flex flex-col items-center justify-center py-12 text-gray-500 dark:text-gray-400">
      <i class="fa-solid fa-spinner fa-spin mb-3 text-2xl"></i>
      <p class="text-sm">正在检索相似案例...</p>
    </div>

    <div v-else-if="cases.length === 0" class="flex flex-col items-center justify-center py-12 text-gray-500 dark:text-gray-400">
      <i class="fa-solid fa-folder-open mb-3 text-2xl"></i>
      <p class="text-sm">暂无相似案例</p>
    </div>

    <div v-else class="space-y-3">
      <p class="text-sm text-gray-600 dark:text-gray-400">
        找到 <span class="font-semibold text-gray-900 dark:text-white">{{ cases.length }}</span> 个相似案例，点击查看详情
      </p>
      <SimilarCaseCard
        v-for="caseItem in cases"
        :key="caseItem.id"
        :case-item="caseItem"
        :on-law-click="onLawClick"
        :on-report-click="onReportClick"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import SimilarCaseCard from './SimilarCaseCard.vue'

interface CaseReport {
  id: string
  name: string
  content: string
}

interface SimilarCase {
  id: string
  title: string
  similarity: number
  date: string
  violatedClauses: string[]
  penaltyClauses: string[]
  discretionStandard: string[]
  penaltySuggestion: string[]
  rectificationMeasures: string[]
  reports: CaseReport[]
}

interface Props {
  cases: SimilarCase[]
  loading: boolean
  isCompleted: boolean
}

defineProps<Props>()

const emit = defineEmits<{
  (e: 'law-click', law: { law_name: string; law_item_name: string; law_item_content: string }): void
  (e: 'report-click', report: CaseReport): void
}>()

const onLawClick = (law: { law_name: string; law_item_name: string; law_item_content: string }) => {
  emit('law-click', law)
}

const onReportClick = (report: CaseReport) => {
  emit('report-click', report)
}
</script>
