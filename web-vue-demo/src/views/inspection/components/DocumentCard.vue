<template>
  <div class="border-t border-dashed border-gray-300 pt-2 dark:border-gray-600">
    <div class="flex items-center gap-2">
      <div class="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded bg-blue-500 text-white">
        <i :class="`${icon} text-xs`"></i>
      </div>
      <div class="flex-1">
        <div class="text-sm font-medium text-gray-900 dark:text-gray-100">
          {{ title }}
        </div>
        <div v-if="subtitle" class="text-xs text-gray-500 dark:text-gray-400">
          {{ subtitle }}
        </div>
      </div>
      <div class="flex gap-2">
        <button
          v-if="showPreview && onPreview"
          @click="onPreview"
          class="rounded bg-white border border-gray-300 px-2 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50 dark:bg-gray-800 dark:border-gray-600 dark:text-gray-200 dark:hover:bg-gray-700"
        >
          <i class="fa-solid fa-eye mr-1"></i>
          预览
        </button>
        <a
          :href="downloadUrl"
          download
          class="rounded bg-white border border-gray-300 px-2 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50 dark:bg-gray-800 dark:border-gray-600 dark:text-gray-200 dark:hover:bg-gray-700"
        >
          <i class="fa-solid fa-download mr-1"></i>
          下载
        </a>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { API_BASE } from '@/lib/config'

interface Props {
  icon: string
  title: string
  subtitle?: string
  filename: string
  threadId: string
  onPreview?: () => void
  showPreview?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  showPreview: true
})

const downloadUrl = computed(() => {
  return `${API_BASE}/api/conversations/${props.threadId}/files/${props.filename}`
})
</script>
