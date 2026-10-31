<script setup lang="ts">
/**
 * 助手消息底栏：复制 + 生成统计（速率/用时）+ AI 生成声明 + 时间戳。
 * 复制内容由投影层算好（getAssistantCopyText），这里只负责交互。
 */
import type { PropType } from 'vue'
import { ElTooltip } from 'element-plus'
import AssistantMessageFooter from '../../AssistantMessageFooter.vue'
import type { MessageGenerationStats } from '../../../types'
import { formatGenerationSpeed, formatTotalDurationLabel, generationStatsRows } from '../../../utils'
import { copyTextToClipboard } from '../../../utils/clipboard'

const props = defineProps({
  timestamp: { type: String, default: '' },
  stats: { type: Object as PropType<MessageGenerationStats>, default: undefined },
  copyText: { type: String, default: '' },
})

const handleCopy = async () => {
  if (!props.copyText) return
  await copyTextToClipboard(props.copyText)
}
</script>

<template>
  <AssistantMessageFooter :timestamp="timestamp">
    <button
      type="button"
      class="inline-flex h-[26px] min-w-[58px] shrink-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-md bg-transparent px-2 text-[11px] text-gray-500 transition-all duration-200 hover:bg-blue-500/5 hover:text-blue-500"
      @click="handleCopy"
    >
      <i class="fa-regular fa-copy text-[11px]"></i>
      <span>复制</span>
    </button>
    <!-- 速率徽标仅在解码统计（usage）存在时展示；usage 缺失场景只有「用时 X」 -->
    <ElTooltip
      v-if="stats?.tokensPerSecond"
      placement="top"
      effect="light"
      :show-arrow="true"
    >
      <template #content>
        <div class="min-w-[224px] py-0.5 text-[11px] leading-[26px]">
          <div
            v-if="stats.model"
            class="mb-1 flex items-center justify-between gap-8 border-b border-gray-200 pb-1"
          >
            <span class="text-gray-400">模型</span>
            <span class="max-w-[180px] truncate font-medium text-gray-800" :title="stats.model">{{ stats.model }}</span>
          </div>
          <div
            v-for="row in generationStatsRows(stats)"
            :key="row.label"
            class="flex items-center justify-between gap-8"
            :class="row.divider ? 'mt-1 border-t border-gray-200 pt-1' : ''"
          >
            <span class="text-gray-400">{{ row.label }}</span>
            <span class="font-medium tabular-nums text-gray-800">{{ row.value }}</span>
          </div>
        </div>
      </template>
      <span class="inline-flex items-center gap-1 whitespace-nowrap text-[11px] text-gray-400">
        <i class="fa-solid fa-bolt text-[10px]"></i>
        {{ formatGenerationSpeed(stats) }}
      </span>
    </ElTooltip>
    <span
      v-if="stats?.totalDurationMs"
      class="inline-flex items-center gap-1 whitespace-nowrap text-[11px] text-gray-400"
    >
      <i class="fa-regular fa-clock text-[10px]"></i>
      用时 {{ formatTotalDurationLabel(stats.totalDurationMs) }}
    </span>
  </AssistantMessageFooter>
</template>
