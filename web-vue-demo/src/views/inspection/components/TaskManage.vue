<template>
  <div v-if="tasks && tasks.length > 0" class="flex flex-col gap-2">
    <div
      v-for="task in tasks"
      :key="task.id"
      class="flex items-center gap-2 px-3 py-2 text-sm transition-colors rounded-lg"
      :class="getTaskBackgroundClass(task.status)"
    >
      <!-- 状态图标 -->
      <div class="flex-shrink-0">
        <!-- 已完成 -->
        <div
          v-if="task.status === 'completed'"
          class="flex h-5 w-5 items-center justify-center rounded-full bg-emerald-500 text-white"
        >
          <i class="fa-solid fa-check text-xs"></i>
        </div>
        <!-- 进行中 -->
        <ThinkingIcon v-else-if="task.status === 'in_progress'" :size="20" />
        <!-- 待处理 -->
        <div
          v-else-if="task.status === 'pending'"
          class="h-5 w-5 rounded-full border-2 border-gray-300 dark:border-gray-600"
        ></div>
        <!-- 已取消 -->
        <div
          v-else-if="task.status === 'cancelled'"
          class="flex h-5 w-5 items-center justify-center rounded-full bg-gray-500 text-white"
        >
          <i class="fa-solid fa-ban text-xs"></i>
        </div>
        <!-- 错误 -->
        <div
          v-else-if="task.status === 'error'"
          class="flex h-5 w-5 items-center justify-center rounded-full bg-rose-500 text-white"
        >
          <i class="fa-solid fa-xmark text-xs"></i>
        </div>
        <!-- 等待确认 -->
        <div
          v-else-if="task.status === 'awaiting_confirm'"
          class="flex h-5 w-5 items-center justify-center rounded-full bg-amber-500 text-white"
        >
          <i class="fa-solid fa-user-check text-xs"></i>
        </div>
      </div>

      <!-- 任务标题 -->
      <div class="flex-1 font-medium text-gray-900 dark:text-gray-100">
        {{ task.title }}
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { h, defineComponent } from 'vue'
import type { AnalysisTask } from '../types'

interface Props {
  tasks: AnalysisTask[]
}

defineProps<Props>()

// 获取任务背景样式
function getTaskBackgroundClass(status: string): string {
  if (status === 'awaiting_confirm') {
    return 'bg-amber-50 dark:bg-amber-900/20'
  }
  return 'bg-gray-50 dark:bg-gray-800/50'
}

// ThinkingIcon 组件 - 使用简单的旋转动画代替 Lottie
const ThinkingIcon = defineComponent({
  name: 'ThinkingIcon',
  props: {
    size: {
      type: Number,
      default: 20
    }
  },
  setup(props) {
    return () =>
      h(
        'div',
        {
          class: 'flex items-center justify-center',
          style: { width: `${props.size}px`, height: `${props.size}px` }
        },
        [
          h('i', {
            class: 'fa-solid fa-circle-notch fa-spin text-indigo-500',
            style: { fontSize: `${props.size * 0.8}px` }
          })
        ]
      )
  }
})
</script>
