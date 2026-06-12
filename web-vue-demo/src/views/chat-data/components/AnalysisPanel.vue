<script setup lang="ts">
/**
 * 分析过程面板组件 - 右侧滑出面板
 * 展示思考过程和工具调用链路
 */
import { computed, ref, watch, nextTick } from 'vue'
import type { ToolCallInfo } from '../types'
import StructuredMessage from './StructuredMessage.vue'
import ToolCallList from './ToolCallList.vue'

// 分析过程数据
export interface AnalysisData {
  roundId: string              // 轮次唯一标识
  thinkingContent: string      // 思考内容
  toolCalls: ToolCallInfo[]    // 工具调用列表
  textContent?: string         // 其他文字内容
}

interface Props {
  isOpen: boolean
  data: AnalysisData | null
  datasourceId?: string
  isStreaming?: boolean
  showSwitchConversation?: boolean
}

const props = defineProps<Props>()

const emit = defineEmits<{
  close: []
  switchConversation: []
  copySql: [sql: string]
}>()

// 隐藏的工具
const HIDDEN_TOOLS = ['load_skill_tool']

// 可见的工具调用
const visibleToolCalls = computed(() => {
  if (!props.data?.toolCalls) return []
  return props.data.toolCalls.filter(tc => !HIDDEN_TOOLS.includes(tc.tool_name))
})

// 是否有工具调用
const hasToolCalls = computed(() => visibleToolCalls.value.length > 0)

// 自动滚动
const contentRef = ref<HTMLDivElement | null>(null)

watch(
  () => [props.data?.thinkingContent, props.data?.toolCalls],
  () => {
    if (props.isStreaming && contentRef.value) {
      nextTick(() => {
        contentRef.value!.scrollTop = contentRef.value!.scrollHeight
      })
    }
  },
  { deep: true }
)
</script>

<template>
  <div v-if="isOpen && data" class="flex flex-col h-full bg-white">
    <!-- 面板头部 -->
    <div class="flex items-center justify-between px-4 py-3 border-b border-gray-200">
      <h3 class="text-sm font-medium text-gray-900 m-0">分析过程</h3>
      <div class="flex items-center gap-1.5">
        <button
          v-if="showSwitchConversation"
          class="h-7 px-2.5 border border-gray-200 rounded-md bg-white text-xs text-gray-500 cursor-pointer transition-all hover:border-gray-300 hover:text-gray-700"
          title="切换到会话"
          @click="emit('switchConversation')"
        >
          会话
        </button>
        <button class="w-7 h-7 flex items-center justify-center border-none bg-transparent rounded-md text-gray-400 cursor-pointer transition-all hover:bg-gray-100 hover:text-gray-500" @click="emit('close')" title="关闭">
          <i class="fa-solid fa-xmark"></i>
        </button>
      </div>
    </div>

    <!-- 面板内容 - 链路布局 -->
    <div ref="contentRef" class="flex-1 overflow-y-auto p-4">
      <!-- 等待第一个数据时的占位（面板已打开但还没有内容） -->
      <div v-if="!data.thinkingContent && !hasToolCalls" class="flex items-center gap-2 text-gray-500 text-sm py-2">
        <i class="fa-solid fa-spinner fa-spin text-blue-500"></i>
        <span>正在思考...</span>
      </div>

      <!-- 步骤 1：理解需求 -->
      <div v-if="data.thinkingContent" class="flex relative">
        <div class="flex flex-col items-center mr-4">
          <div :class="[
            'w-6 h-6 flex items-center justify-center rounded-full shrink-0 text-[10px]',
            isStreaming && !hasToolCalls
              ? 'bg-gradient-to-br from-blue-500/15 to-blue-500/5 text-blue-500'
              : 'bg-gradient-to-br from-emerald-500/15 to-emerald-500/5 text-emerald-500'
          ]">
            <i v-if="isStreaming && !hasToolCalls" class="fa-solid fa-spinner fa-spin"></i>
            <i v-else class="fa-solid fa-check"></i>
          </div>
          <div
            v-if="hasToolCalls || isStreaming"
            class="w-px flex-1 min-h-5 mt-2"
            style="background-image: repeating-linear-gradient(to bottom, #D8DAE5 0 3px, transparent 3px 6px);"
          ></div>
        </div>
        <div class="flex-1 pb-6">
          <h4 class="text-sm font-medium text-gray-900 m-0 mb-2">理解需求</h4>
          <div class="text-sm text-gray-600">
            <StructuredMessage
              :content="data.thinkingContent"
              :is-streaming="isStreaming && !hasToolCalls"
              @copy-sql="emit('copySql', $event)"
            />
          </div>
        </div>
      </div>

      <!-- 步骤 2：执行任务 -->
      <div v-if="hasToolCalls" class="flex relative">
        <div class="flex flex-col items-center mr-4">
          <div :class="[
            'w-6 h-6 flex items-center justify-center rounded-full shrink-0 text-[10px]',
            isStreaming
              ? 'bg-gradient-to-br from-blue-500/15 to-blue-500/5 text-blue-500'
              : 'bg-gradient-to-br from-emerald-500/15 to-emerald-500/5 text-emerald-500'
          ]">
            <i v-if="isStreaming" class="fa-solid fa-spinner fa-spin"></i>
            <i v-else class="fa-solid fa-check"></i>
          </div>
          <div
            v-if="!isStreaming"
            class="w-px flex-1 min-h-5 mt-2"
            style="background-image: repeating-linear-gradient(to bottom, #D8DAE5 0 3px, transparent 3px 6px);"
          ></div>
        </div>
        <div class="flex-1 pb-6">
          <h4 class="text-sm font-medium text-gray-900 m-0 mb-2">执行任务</h4>
          <div class="text-sm text-gray-600">
            <ToolCallList
              :tool-calls="visibleToolCalls"
              :datasource-id="datasourceId"
              :default-expanded="true"
            />
          </div>
        </div>
      </div>

      <!-- 步骤 3：任务完成（流式结束且有实际内容后才显示） -->
      <div v-if="!isStreaming && (data.thinkingContent || hasToolCalls)" class="flex relative">
        <div class="flex flex-col items-center mr-4">
          <div class="w-6 h-6 flex items-center justify-center rounded-full shrink-0 text-[10px] bg-gradient-to-br from-emerald-500 to-emerald-600 text-white">
            <i class="fa-solid fa-check"></i>
          </div>
        </div>
        <div class="flex-1 flex items-center pb-0">
          <span class="text-sm font-medium text-emerald-500">任务完成</span>
        </div>
      </div>

      <!-- 其他文字内容 -->
      <div v-if="data.textContent" class="mt-4 text-sm text-gray-700">
        {{ data.textContent }}
      </div>
    </div>
  </div>
</template>
