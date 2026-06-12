<template>
  <div v-if="fileGroups.length > 0" class="space-y-3">
    <!-- 分组数量提示 -->
    <div class="flex items-center justify-between text-xs text-gray-500 dark:text-gray-400 px-1">
      <span>已上传 {{ fileGroups.length }}/10 个违法行为</span>
      <div class="flex items-center gap-2">
        <span v-if="fileGroups.length >= 10" class="text-amber-600 dark:text-amber-400">
          <i class="fa-solid fa-triangle-exclamation mr-1"></i>
          已达上限
        </span>
        <button
          v-if="onClearAll && fileGroups.length > 0"
          type="button"
          @click="onClearAll"
          class="text-red-500 hover:text-red-700 dark:text-red-400 dark:hover:text-red-300 transition-colors hover:underline"
          title="清空全部"
        >
          <i class="fa-solid fa-trash-can mr-1"></i>
          清空全部
        </button>
      </div>
    </div>

    <!-- 文件列表 -->
    <div class="space-y-2">
      <div
        v-for="(group, index) in fileGroups"
        :key="group.group_id"
        class="group relative flex items-center gap-3 p-3 bg-white dark:bg-gray-800/50 border border-gray-200 dark:border-gray-700 rounded-lg hover:border-gray-300 dark:hover:border-gray-600 hover:shadow-sm transition-all"
      >
        <!-- 序号标识 -->
        <div class="flex-shrink-0 w-6 h-6 rounded-full bg-indigo-100 dark:bg-indigo-900/30 text-indigo-600 dark:text-indigo-400 flex items-center justify-center text-xs font-semibold">
          {{ index + 1 }}
        </div>

        <!-- 文件图标和信息 -->
        <div class="flex-shrink-0 w-9 h-9 bg-blue-50 dark:bg-blue-900/20 rounded flex items-center justify-center">
          <i class="fa-solid fa-file-word text-blue-600 dark:text-blue-400 text-lg"></i>
        </div>

        <div class="flex-1 min-w-0">
          <div class="text-sm font-medium text-gray-900 dark:text-gray-100 truncate">
            {{ group.fact_determination.filename }}
          </div>
          <div class="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
            {{ formatDate(group.fact_determination.upload_time) }}
          </div>
        </div>

        <!-- 删除按钮 -->
        <button
          type="button"
          @click="onDeleteGroup(group.group_id)"
          class="flex-shrink-0 opacity-0 group-hover:opacity-100 p-2 text-gray-400 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 rounded transition-all"
          title="删除此违法行为"
        >
          <i class="fa-solid fa-trash text-sm"></i>
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { ViolationFileGroup } from '../types'

interface Props {
  fileGroups: ViolationFileGroup[]
  onDeleteGroup: (groupId: string) => Promise<void>
  onClearAll?: () => Promise<void>
}

defineProps<Props>()

// 格式化日期
function formatDate(dateStr: string): string {
  const d = new Date(dateStr)
  return d.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  })
}
</script>
