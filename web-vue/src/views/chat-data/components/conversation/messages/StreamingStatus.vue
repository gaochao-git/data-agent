<script setup lang="ts">
/**
 * 流式状态行：九宫格像素动画 + 「工作中」+ 实时速率/用时 + 等待期知识轮播。
 * 只在消息流式中渲染，挂载即活跃。
 */
import { formatSuperstepsLabel, formatTotalDurationLabel } from '../../../utils'
import WaitingTip from '../WaitingTip.vue'

withDefaults(defineProps<{
  liveGenerationSpeed?: number | null
  liveSupersteps?: number | null
  liveSuperstepLimit?: number | null
  liveElapsedMs?: number | null
  /** 仅子智能体任务流启用：主对话的停止入口在输入区，不重复出现 */
  cancellable?: boolean
  cancelling?: boolean
}>(), {
  liveGenerationSpeed: null,
  liveSupersteps: null,
  liveSuperstepLimit: null,
  liveElapsedMs: null,
  cancellable: false,
  cancelling: false,
})

const emit = defineEmits<{
  cancel: []
}>()

// 「工作中」九宫格像素动画（参考 ZCode 同款指示器）：中心块（index 4）恒暗，
// 其余 8 块按螺旋顺序 110ms 错峰闪烁；时长与 keyframes 在 styles/loading-pixel.css。
const LOADING_PIXEL_ORDER = [0, 1, 2, 5, 8, 7, 6, 3]
const LOADING_PIXEL_COUNT = 9
const loadingPixelStyle = (index: number): Record<string, string> => (
  index === 4
    ? { opacity: '0.07', animationName: 'none' }
    : {
        opacity: '0.15',
        animationDelay: `${Math.max(0, LOADING_PIXEL_ORDER.indexOf(index)) * 110}ms`,
      }
)
</script>

<template>
  <div class="mt-2 ml-3 flex min-w-0 items-center gap-2 text-xs text-slate-400 @container">
    <span aria-hidden="true" class="grid size-[13px] shrink-0 grid-cols-3 grid-rows-3 gap-[1.3px] text-[#1677FF]">
      <span
        v-for="pixel in LOADING_PIXEL_COUNT"
        :key="pixel"
        class="chat-loading-pixel size-full rounded-[1px] bg-current"
        :style="loadingPixelStyle(pixel - 1)"
      ></span>
    </span>
    <span class="shrink-0 whitespace-nowrap font-medium">工作中</span>
    <span v-if="liveGenerationSpeed" class="shrink-0 whitespace-nowrap text-[11px] text-slate-400">~{{ liveGenerationSpeed }} tok/s</span>
    <span v-if="liveSupersteps" class="shrink-0 whitespace-nowrap text-[11px] text-slate-400">{{ formatSuperstepsLabel(liveSupersteps, liveSuperstepLimit ?? undefined) }}</span>
    <span v-if="liveElapsedMs" class="shrink-0 whitespace-nowrap text-[11px] text-slate-400">用时 {{ formatTotalDurationLabel(liveElapsedMs) }}</span>
    <!-- 等待期知识轮播：内联在用时之后，不额外占一行；容器过窄时直接不展示，
         避免把状态行（工作中/速率/用时）挤到换行或竖排。 -->
    <span class="@max-[520px]:hidden">
      <WaitingTip :active="true" />
    </span>
    <button
      v-if="cancellable"
      type="button"
      class="ml-auto inline-flex h-6 shrink-0 items-center gap-1 whitespace-nowrap rounded-md border border-rose-100 bg-rose-50 px-1.5 text-[11px] font-medium text-rose-500 transition-colors hover:border-rose-200 hover:bg-rose-100 disabled:cursor-not-allowed disabled:opacity-60"
      :disabled="cancelling"
      data-subagent-stream-cancel
      :title="cancelling ? '正在取消子智能体任务' : '取消正在运行的子智能体任务'"
      @click.stop="emit('cancel')"
    >
      <i :class="cancelling ? 'fa-solid fa-spinner fa-spin' : 'fa-solid fa-stop'" class="text-[9px]"></i>
      <span>{{ cancelling ? '取消中' : '取消' }}</span>
    </button>
  </div>
</template>
