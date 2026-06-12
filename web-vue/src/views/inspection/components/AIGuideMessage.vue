<template>
  <div class="ai-guide-container p-4 bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 max-w-[600px]">
    <!-- 头部：标题和图标 -->
    <div class="flex items-center gap-3 mb-3">
      <div v-if="icon" class="w-10 h-10 rounded-full flex items-center justify-center" :class="iconBgClass">
        <i :class="['fa-solid', icon, 'text-lg', iconColorClass]"></i>
      </div>
      <div>
        <h3 class="text-lg font-semibold text-gray-900 dark:text-gray-100">{{ title }}</h3>
        <p v-if="description" class="text-xs text-gray-500 dark:text-gray-400">{{ description }}</p>
      </div>
    </div>

    <!-- 欢迎消息 -->
    <div class="mb-4">
      <p class="text-sm text-gray-700 dark:text-gray-300 leading-relaxed">{{ welcomeMessage }}</p>
    </div>

    <!-- 上传要求 -->
    <div v-if="uploadRequirements && uploadRequirements.length > 0" class="mb-4">
      <h4 class="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wide mb-2">
        支持上传
      </h4>
      <div class="flex flex-wrap gap-2">
        <span
          v-for="req in uploadRequirements"
          :key="req"
          class="inline-flex items-center px-2.5 py-1 rounded-md text-xs font-medium bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300"
        >
          <i class="fa-solid fa-file-lines mr-1.5 text-blue-500/70"></i>
          {{ req }}
        </span>
      </div>
    </div>

    <!-- 示例提示词 -->
    <div v-if="examplePrompts && examplePrompts.length > 0" class="mb-3">
      <h4 class="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wide mb-2">
        您可以这样问我
      </h4>
      <div class="space-y-2">
        <button
          v-for="prompt in examplePrompts"
          :key="prompt"
          @click="$emit('example-click', prompt)"
          class="w-full text-left px-3 py-2 rounded-md text-sm text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700/50 hover:text-indigo-600 dark:hover:text-indigo-400 transition-colors flex items-center group"
        >
          <i class="fa-regular fa-comment-dots mr-2 text-gray-400 group-hover:text-indigo-500 transition-colors"></i>
          {{ prompt }}
        </button>
      </div>
    </div>

    <!-- 操作按钮 -->
    <div class="flex gap-2 mt-4 pt-4 border-t border-gray-100 dark:border-gray-700">
      <button
        @click="$emit('action-click')"
        class="flex-1 inline-flex items-center justify-center px-4 py-2 border border-transparent rounded-md text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-indigo-500 transition-colors"
      >
        <i class="fa-solid fa-upload mr-2"></i>
        上传文件
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

interface Props {
  title?: string
  description?: string
  welcomeMessage: string
  icon?: string
  color?: string
  uploadRequirements?: string[]
  examplePrompts?: string[]
}

const props = withDefaults(defineProps<Props>(), {
  title: '智能助手',
  description: '',
  icon: 'fa-robot',
  color: 'blue',
  uploadRequirements: () => [],
  examplePrompts: () => []
})

defineEmits<{
  (e: 'action-click'): void
  (e: 'example-click', prompt: string): void
}>()

// 计算图标背景颜色类
const iconBgClass = computed(() => {
  const colorMap: Record<string, string> = {
    blue: 'bg-blue-100 dark:bg-blue-900/30',
    purple: 'bg-purple-100 dark:bg-purple-900/30',
    indigo: 'bg-indigo-100 dark:bg-indigo-900/30',
    green: 'bg-green-100 dark:bg-green-900/30',
    red: 'bg-red-100 dark:bg-red-900/30',
    orange: 'bg-orange-100 dark:bg-orange-900/30',
    teal: 'bg-teal-100 dark:bg-teal-900/30'
  }
  return colorMap[props.color] || colorMap.blue
})

// 计算图标颜色类
const iconColorClass = computed(() => {
  const colorMap: Record<string, string> = {
    blue: 'text-blue-600 dark:text-blue-400',
    purple: 'text-purple-600 dark:text-purple-400',
    indigo: 'text-indigo-600 dark:text-indigo-400',
    green: 'text-green-600 dark:text-green-400',
    red: 'text-red-600 dark:text-red-400',
    orange: 'text-orange-600 dark:text-orange-400',
    teal: 'text-teal-600 dark:text-teal-400'
  }
  return colorMap[props.color] || colorMap.blue
})
</script>

<style scoped>
.ai-guide-container {
  max-width: 600px;
}
</style>