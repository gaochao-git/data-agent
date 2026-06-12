<template>
  <div class="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm transition-shadow hover:shadow-md dark:border-gray-700 dark:bg-gray-800">
    <!-- 标题栏 -->
    <div
      class="flex cursor-pointer items-center justify-between gap-3 px-4 py-3 transition-colors hover:bg-gray-50 dark:hover:bg-gray-700/50"
      @click="onToggle"
    >
      <div class="flex items-center gap-3 min-w-0">
        <div class="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-lg bg-indigo-100 text-sm font-semibold text-indigo-600 dark:bg-indigo-900/50 dark:text-indigo-400">
          {{ violation.违法行为编号 }}
        </div>
        <div class="min-w-0 flex-1">
          <h3 class="truncate text-sm font-medium text-gray-900 dark:text-white flex items-center gap-2">
            {{ truncatedFact }}
            <i
              v-if="violation.AI扩充"
              class="fa-solid fa-circle-info cursor-pointer text-indigo-500 hover:text-indigo-600 text-xs flex-shrink-0"
              @click.stop="showTooltip"
            ></i>
          </h3>
        </div>
      </div>
      <i :class="`fa-solid ${isExpanded ? 'fa-chevron-up' : 'fa-chevron-down'} text-gray-400`"></i>
    </div>

    <!-- 展开内容 -->
    <Transition name="slide">
      <div v-if="isExpanded" class="border-t border-gray-100 dark:border-gray-700">
        <div class="space-y-4 px-4 py-4">
          <!-- 违反条款 -->
          <LawSection
            title="违反条款"
            :items="violation.违反条款"
            :is-completed="isCompleted"
            :on-law-click="onLawClick"
          />

          <!-- 处罚条款 -->
          <LawSection
            title="处罚条款"
            :items="violation.处罚条款"
            :is-completed="isCompleted"
            :on-law-click="onLawClick"
          />

          <!-- 其他信息（裁量基准、处罚建议、整改措施） -->
          <div class="grid gap-4 md:grid-cols-3">
            <InfoList
              v-if="violation.裁量基准?.length > 0"
              title="裁量基准"
              icon="fa-ruler"
              icon-color="text-amber-500"
              :items="violation.裁量基准"
            />
            <InfoList
              v-if="violation.处罚建议?.length > 0"
              title="处罚建议"
              icon="fa-lightbulb"
              icon-color="text-yellow-500"
              :items="violation.处罚建议"
            />
            <InfoList
              v-if="violation.整改措施?.length > 0"
              title="整改措施"
              icon="fa-wrench"
              icon-color="text-blue-500"
              :items="violation.整改措施"
            />
          </div>

          <!-- 证据列表 -->
          <div v-if="violation.证据列表 && violation.证据列表.length > 0" class="space-y-3">
            <h5 class="flex items-center gap-2 text-sm font-semibold text-gray-700 dark:text-gray-300">
              <i class="fa-solid fa-folder-open text-indigo-500"></i>
              证据列表
            </h5>
            <EvidenceTable :evidence="violation.证据列表" />
          </div>
        </div>
      </div>
    </Transition>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { ViolationAnalysis } from '../types'
import LawSection from './LawSection.vue'
import InfoList from './InfoList.vue'
import EvidenceTable from './EvidenceTable.vue'

interface Props {
  violation: ViolationAnalysis
  isExpanded: boolean
  isCompleted: boolean
  onToggle: () => void
  onLawClick?: (law: { law_name: string; law_item_name: string; law_item_content: string }) => void
  onShowTooltip?: (title: string, content: string) => void
}

const props = defineProps<Props>()

const truncatedFact = computed(() => {
  const fact = props.violation.原始事实认定
  return fact.length > 50 ? fact.substring(0, 50) + '...' : fact
})

const showTooltip = () => {
  if (props.onShowTooltip && props.violation.AI扩充) {
    props.onShowTooltip('AI 解读', props.violation.AI扩充)
  }
}
</script>

<style scoped>
.slide-enter-active,
.slide-leave-active {
  transition: all 0.25s ease;
}
.slide-enter-from,
.slide-leave-to {
  height: 0;
  opacity: 0;
}
.slide-enter-to,
.slide-leave-from {
  height: auto;
  opacity: 1;
}
</style>
