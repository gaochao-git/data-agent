<template>
  <div class="space-y-3">
    <!-- Tab 切换区域 -->
    <div class="flex items-center gap-1 rounded-xl bg-gray-100 p-1 dark:bg-gray-800">
      <button
        @click="activeTab = 'analysis'"
        :class="[
          'flex-1 rounded-lg px-4 py-2 text-sm font-medium transition-all',
          activeTab === 'analysis'
            ? 'bg-white text-gray-900 shadow-sm dark:bg-gray-700 dark:text-white'
            : 'text-gray-600 hover:text-gray-900 dark:text-gray-400 dark:hover:text-white'
        ]"
      >
        <i class="fa-solid fa-file-lines mr-2"></i>
        违法事实
        <span class="ml-1.5 rounded-full bg-indigo-100 px-1.5 py-0.5 text-xs font-semibold text-gray-900 dark:bg-indigo-900/50 dark:text-white">
          {{ analysisResult.违法行为数量 }}
        </span>
      </button>
      <button
        @click="activeTab = 'similar'"
        :class="[
          'flex-1 rounded-lg px-4 py-2 text-sm font-medium transition-all',
          activeTab === 'similar'
            ? 'bg-white text-gray-900 shadow-sm dark:bg-gray-700 dark:text-white'
            : 'text-gray-600 hover:text-gray-900 dark:text-gray-400 dark:hover:text-white'
        ]"
      >
        <i class="fa-solid fa-clone mr-2"></i>
        相似案例
        <span v-if="similarCases.length > 0" class="ml-1.5 rounded-full bg-amber-100 px-1.5 py-0.5 text-xs font-semibold text-gray-900 dark:bg-amber-900/50 dark:text-white">
          {{ similarCases.length }}
        </span>
      </button>
    </div>

    <!-- Tab 内容区域 -->
    <div v-if="activeTab === 'analysis'">
      <AnalysisContent
        :analysis-result="analysisResult"
        :is-completed="isCompleted"
        :expanded-items="expandedItems"
        :on-toggle="toggleItem"
        :on-toggle-all="toggleAll"
        :on-law-click="(law) => emit('law-click', law)"
        :on-show-tooltip="(title, content) => { aiTooltip = { title, content } }"
      />
    </div>

    <div v-else>
      <SimilarCasesContent
        :cases="similarCases"
        :loading="loadingSimilar"
        :is-completed="isCompleted"
        :on-law-click="(law) => emit('law-click', law)"
        :on-report-click="(report) => emit('report-click', report)"
      />
    </div>

    <!-- AI解读弹窗 -->
    <Transition name="fade">
      <div v-if="aiTooltip" class="fixed inset-0 z-[200] flex items-center justify-center bg-black/50 p-4" @click="aiTooltip = null">
        <div
          class="max-h-[80vh] w-full max-w-lg overflow-auto rounded-xl bg-white p-5 shadow-2xl dark:bg-gray-800"
          @click.stop
        >
          <div class="mb-3 flex items-center justify-between">
            <h3 class="flex items-center gap-2 text-base font-semibold text-gray-900 dark:text-white">
              <i class="fa-solid fa-circle-info text-indigo-500"></i>
              {{ aiTooltip.title }}
            </h3>
            <button
              @click="aiTooltip = null"
              class="rounded-lg p-1.5 text-gray-400 hover:bg-gray-100 hover:text-gray-600 dark:hover:bg-gray-700"
            >
              <i class="fa-solid fa-xmark"></i>
            </button>
          </div>
          <p class="text-sm leading-relaxed text-gray-700 dark:text-gray-300 select-text">
            {{ aiTooltip.content }}
          </p>
        </div>
      </div>
    </Transition>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import type { AnalysisResult, HistoricalCase } from '../types'
import AnalysisContent from './AnalysisContent.vue'
import SimilarCasesContent from './SimilarCasesContent.vue'

interface Props {
  analysisResult: AnalysisResult
  isCompleted: boolean
}

const props = defineProps<Props>()

const emit = defineEmits<{
  (e: 'law-click', law: { law_name: string; law_item_name: string; law_item_content: string }): void
  (e: 'report-click', report: any): void
}>()

// Tab 状态
const activeTab = ref<'analysis' | 'similar'>('analysis')
const expandedItems = ref<Set<number>>(new Set())
const aiTooltip = ref<{ title: string; content: string } | null>(null)

// 相似案例
const similarCases = ref<any[]>([])
const loadingSimilar = ref(false)
const hasFetchedRef = ref(false)

// 辅助函数：确保法律名称有书名号
function ensureBookTitle(lawName: string): string {
  if (!lawName) return ''
  if (lawName.startsWith('《') && lawName.endsWith('》')) {
    return lawName
  }
  return `《${lawName}》`
}

// 获取相似案例
const fetchSimilarCases = async () => {
  loadingSimilar.value = true
  const backendCases: any[] = []

  // 从后端返回的历史案例数据中提取
  props.analysisResult.违法行为分析.forEach((violation) => {
    if (violation.历史案例 && violation.历史案例.length > 0) {
      violation.历史案例.forEach((caseItem) => {
        backendCases.push({
          id: caseItem.case_id,
          title: caseItem.case_title,
          similarity: Math.round(caseItem.similarity_score * 100),
          date: caseItem.inspection_date || '未知',
          violatedClauses: caseItem.violated_provisions?.map(p =>
            `${ensureBookTitle(p.law_name)}${p.law_item_name}`
          ) || [],
          penaltyClauses: caseItem.penalty_provisions?.map(p =>
            `${ensureBookTitle(p.law_name)}${p.law_item_name}`
          ) || [],
          discretionStandard: caseItem.discretionary_benchmarks || [],
          penaltySuggestion: [caseItem.penalty_result],
          rectificationMeasures: caseItem.rectification_measures || [],
          reports: [
            {
              id: `${caseItem.case_id}-report`,
              name: '检查报告',
              content: `# ${caseItem.case_title}\n\n## 案例摘要\n${caseItem.case_summary}\n\n## 违法事实\n${caseItem.violation_facts}\n\n## 处罚结果\n${caseItem.penalty_result}\n\n## 参考价值\n${caseItem.reference_value}`
            },
          ],
        })
      })
    }
  })

  // 如果有后端案例，使用后端数据
  if (backendCases.length > 0) {
    similarCases.value = backendCases
  }

  loadingSimilar.value = false
}

// 切换展开/折叠
const toggleItem = (id: number) => {
  const newSet = new Set(expandedItems.value)
  if (newSet.has(id)) {
    newSet.delete(id)
  } else {
    newSet.add(id)
  }
  expandedItems.value = newSet
}

const toggleAll = () => {
  if (expandedItems.value.size === props.analysisResult.违法行为分析.length) {
    expandedItems.value = new Set()
  } else {
    expandedItems.value = new Set(
      props.analysisResult.违法行为分析.map((v) => v.违法行为编号)
    )
  }
}

// 当分析完成时自动获取相似案例
watch(
  () => props.isCompleted,
  (completed) => {
    if (completed && props.analysisResult?.违法行为分析?.length > 0 && !hasFetchedRef.value) {
      hasFetchedRef.value = true
      fetchSimilarCases()
      // 默认展开所有违法事实分析
      expandedItems.value = new Set(props.analysisResult.违法行为分析.map(v => v.违法行为编号))
    }
  },
  { immediate: true }
)
</script>

<style scoped>
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.15s ease;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
