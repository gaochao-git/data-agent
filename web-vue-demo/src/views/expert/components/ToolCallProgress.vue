<!--
  工具调用进度显示组件
  显示当前正在调用的工具和结果预览
-->

<script setup lang="ts">
import { withDefaults, defineProps } from 'vue'

interface ToolCallInfo {
  id: string
  name: string
  status: 'running' | 'completed' | 'error'
  resultPreview: string
  timestamp: number
}

interface Props {
  toolCalls: ToolCallInfo[]
}

const props = withDefaults(defineProps<Props>(), {
  toolCalls: () => [],
})

// 获取工具调用的显示名称（截取10字以内）
const getDisplayName = (name: string): string => {
  return name.length > 10 ? name.slice(0, 10) : name
}

// 获取结果预览（截取20字以内）
const getResultPreview = (preview: string): string => {
  return preview.length > 20 ? preview.slice(0, 20) : preview
}
</script>

<template>
  <div class="tool-call-progress flex flex-col gap-2">
    <div
      v-for="toolCall in toolCalls"
      :key="toolCall.id"
      class="flex items-center gap-2 rounded-[12px] bg-[#F6F8FA] px-4 py-2"
    >
      <!-- 状态图标 -->
      <div class="flex h-5 w-5 items-center justify-center flex-shrink-0">
        <!-- running: 旋转动画 -->
        <div
          v-if="toolCall.status === 'running'"
          class="h-[10px] w-[10px] animate-spin rounded-full border-[2px] border-[#E5EAF0] border-t-[#007EA7]"
        ></div>
        <!-- completed: 绿色勾 -->
        <i
          v-else-if="toolCall.status === 'completed'"
          class="fa-solid fa-check text-[10px] text-[#31C48D]"
        ></i>
        <!-- error: 红色叉 -->
        <i
          v-else
          class="fa-solid fa-xmark text-[10px] text-[#E65054]"
        ></i>
      </div>

      <!-- 工具名称 -->
      <span class="text-[14px] font-[350] leading-[22px] text-[#1F3A56]">
        {{ getDisplayName(toolCall.name) }}
      </span>

      <!-- 结果预览 -->
      <span
        v-if="toolCall.resultPreview"
        class="flex-1 truncate text-[12px] font-[350] leading-[20px] text-[#8592A6]"
      >
        {{ getResultPreview(toolCall.resultPreview) }}
      </span>
    </div>
  </div>
</template>

<style scoped>
@keyframes spin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}

.animate-spin {
  animation: spin 1s linear infinite;
}
</style>
