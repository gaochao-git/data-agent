<template>
  <div class="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm transition-all hover:shadow-md dark:border-gray-700 dark:bg-gray-800">
    <!-- 案例标题和操作区域 -->
    <div class="flex items-center gap-3 px-4 py-3">
      <div class="min-w-0 flex-1">
        <h3 class="text-sm font-medium text-gray-900 dark:text-white line-clamp-2">
          {{ caseItem.title }}
        </h3>
      </div>
      <span :class="`rounded-full px-2 py-0.5 text-xs font-semibold flex-shrink-0 ${getSimilarityColor(caseItem.similarity)}`">
        {{ caseItem.similarity }}%
      </span>
      <button
        v-if="caseItem.reports && caseItem.reports.length > 0"
        @click="$emit('report-click', caseItem.reports[0])"
        class="flex-shrink-0 rounded-lg border border-indigo-200 bg-white px-3 py-1.5 text-xs font-medium text-indigo-600 transition-all-all hover:bg-indigo-50 hover:border-indigo-300 dark:border-indigo-800 dark:bg-gray-800 dark:text-indigo-400 dark:hover:bg-indigo-900/20"
      >
        <i class="fa-solid fa-file-lines mr-1.5"></i>
        查看报告
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
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
  caseItem: SimilarCase
}

defineProps<Props>()

defineEmits<{
  (e: 'report-click', report: CaseReport): void
  (e: 'law-click', law: { law: string; law_item: string }): void
}>()

function getSimilarityColor(similarity: number): string {
  if (similarity >= 90) return 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/50 dark:text-emerald-400'
  if (similarity >= 80) return 'bg-amber-100 text-amber-700 dark:bg-amber-900/50 dark:text-amber-400'
  return 'bg-gray-100 text-gray-700 dark:bg-gray-700 dark:text-gray-300'
}
</script>
