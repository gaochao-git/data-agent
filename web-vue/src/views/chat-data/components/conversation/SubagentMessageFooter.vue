<script setup lang="ts">
/**
 * 子智能体助手气泡底栏：复制 + 用时 + token 用量（悬浮查看明细）+ 时间。
 *
 * 对齐主对话 footer 的形态：用量不铺成一行长文本，只在需要时悬浮展开；
 * 子智能体场景不展示「AI 生成」提示。
 */
import type { PropType } from 'vue'
import { ElTooltip } from 'element-plus'
import { formatTotalDurationLabel } from '../../utils'
import { copyTextToClipboard } from '../../utils/clipboard'

const props = defineProps({
  timestamp: { type: String, default: '' },
  copyText: { type: String, default: '' },
  durationMs: { type: Number as PropType<number | null>, default: null },
  usageText: { type: String, default: '' },
})

const handleCopy = async () => {
  if (!props.copyText) return
  await copyTextToClipboard(props.copyText)
}
</script>

<template>
  <div class="flex items-center gap-2 px-1" data-subagent-message-footer>
    <button
      v-if="copyText"
      type="button"
      class="inline-flex h-[26px] min-w-[58px] shrink-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-md px-2 text-[11px] text-gray-500 transition-all duration-200 hover:bg-blue-500/5 hover:text-blue-500"
      data-subagent-copy
      @click="handleCopy"
    >
      <i class="fa-regular fa-copy text-[11px]"></i>
      <span>复制</span>
    </button>
    <span
      v-if="durationMs"
      class="inline-flex items-center gap-1 whitespace-nowrap text-[11px] text-gray-400"
      data-subagent-duration
    >
      <i class="fa-regular fa-clock text-[10px]"></i>
      用时 {{ formatTotalDurationLabel(durationMs) }}
    </span>
    <ElTooltip v-if="usageText" effect="light" placement="top" :show-arrow="true">
      <template #content>
        <div class="min-w-[220px] py-0.5 text-[11px] leading-[22px]">{{ usageText }}</div>
      </template>
      <span
        class="inline-flex cursor-help items-center gap-1 whitespace-nowrap text-[11px] text-gray-400 transition-colors hover:text-blue-500"
        :aria-label="usageText"
        data-subagent-tokens
      >
        <i class="fa-solid fa-coins text-[10px]"></i>
        tokens
        <i class="fa-solid fa-circle-info text-[9px]"></i>
      </span>
    </ElTooltip>
    <span
      v-if="timestamp"
      class="ml-auto whitespace-nowrap text-[11px] text-gray-400"
      data-subagent-timestamp
    >
      {{ timestamp }}
    </span>
  </div>
</template>
