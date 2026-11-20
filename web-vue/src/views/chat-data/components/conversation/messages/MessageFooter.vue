<script setup lang="ts">
/**
 * 助手消息底栏（主对话与子智能体过程共用）：复制 + 生成统计/子智能体用量 +
 * 可选 AI 生成声明 + 时间戳。子智能体不展示 AI 声明，时长来自任务耗时，
 * 用量来自子智能体 billing 统计；其余布局与交互保持同一套。
 * 复制内容由投影层算好（getAssistantCopyText），这里只负责交互。
 */
import type { PropType } from 'vue'
import { computed } from 'vue'
import { ElTooltip } from 'element-plus'
import AssistantMessageFooter from '../../AssistantMessageFooter.vue'
import type { MessageGenerationStats } from '../../../types'
import { formatGenerationSpeed, formatTotalDurationLabel, generationStatsRows } from '../../../utils'
import { copyTextToClipboard } from '../../../utils/clipboard'

interface UsageDetailRow {
  label: string
  value: string
}

const props = defineProps({
  timestamp: { type: String, default: '' },
  stats: { type: Object as PropType<MessageGenerationStats>, default: undefined },
  copyText: { type: String, default: '' },
  /** 子智能体任务耗时；主对话优先使用 stats.totalDurationMs */
  durationMs: { type: Number as PropType<number | null>, default: null },
  /** 子智能体 billing 用量摘要；主对话为空 */
  usageText: { type: String, default: '' },
  /** 子智能体 token 明细浮层的模型名；主对话为空 */
  usageModel: { type: String, default: '' },
  /** 子智能体 token 明细行；主对话为空 */
  usageRows: { type: Array as PropType<UsageDetailRow[]>, default: () => [] },
  /** 子智能体模型调用速率；有值时底栏与主对话一样显示 tok/s */
  usageSpeed: { type: Number, default: 0 },
  showAiNotice: { type: Boolean, default: true },
})

const displayDurationMs = computed(() => props.stats?.totalDurationMs ?? props.durationMs)
const usageSpeedText = computed(() => (
  props.usageSpeed > 0 ? formatGenerationSpeed({ tokensPerSecond: props.usageSpeed }) : ''
))

const handleCopy = async () => {
  if (!props.copyText) return
  await copyTextToClipboard(props.copyText)
}
</script>

<template>
  <AssistantMessageFooter :timestamp="timestamp" :show-ai-notice="showAiNotice">
    <button
      type="button"
      class="inline-flex h-[26px] min-w-[58px] shrink-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-md bg-transparent px-2 text-[11px] text-gray-500 transition-all duration-200 hover:bg-blue-500/5 hover:text-blue-500"
      data-message-copy
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
    <ElTooltip v-if="usageText || usageRows.length" effect="light" placement="top" :show-arrow="true">
      <template #content>
        <div class="min-w-[224px] py-0.5 text-[11px] leading-[26px]">
          <div
            v-if="usageModel"
            class="mb-1 flex items-center justify-between gap-8 border-b border-gray-200 pb-1"
          >
            <span class="text-gray-400">模型</span>
            <span class="max-w-[180px] truncate font-medium text-gray-800" :title="usageModel">{{ usageModel }}</span>
          </div>
          <div
            v-for="row in usageRows"
            :key="row.label"
            class="flex items-center justify-between gap-8"
          >
            <span class="text-gray-400">{{ row.label }}</span>
            <span class="font-medium tabular-nums text-gray-800">{{ row.value }}</span>
          </div>
          <div v-if="!usageRows.length" class="whitespace-nowrap">{{ usageText }}</div>
        </div>
      </template>
      <span
        v-if="usageSpeedText"
        class="inline-flex items-center gap-1 whitespace-nowrap text-[11px] text-gray-400"
        data-message-speed
      >
        <i class="fa-solid fa-bolt text-[10px]"></i>
        {{ usageSpeedText }}
      </span>
      <span
        v-else
        class="inline-flex items-center gap-1 whitespace-nowrap text-[11px] text-gray-400"
        :aria-label="usageText"
        data-message-tokens
      >
        <i class="fa-solid fa-bolt text-[10px]"></i>
        -- tok/s
      </span>
    </ElTooltip>
    <span
      v-if="displayDurationMs"
      class="inline-flex items-center gap-1 whitespace-nowrap text-[11px] text-gray-400"
      data-message-duration
    >
      <i class="fa-regular fa-clock text-[10px]"></i>
      用时 {{ formatTotalDurationLabel(displayDurationMs) }}
    </span>
  </AssistantMessageFooter>
</template>
