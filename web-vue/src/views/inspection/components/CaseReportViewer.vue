<template>
  <aside
    class="flex flex-col border-l border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-900 w-full min-w-0"
    :class="panelHeightClass"
  >
    <!-- 头部 -->
    <div class="flex items-center justify-between gap-2 px-3 py-3 flex-shrink-0 h-[52px] border-b border-gray-100 dark:border-gray-800">
      <div class="flex items-center gap-2 min-w-0 flex-1">
        <i class="fa-solid fa-file-lines text-amber-600 text-lg flex-shrink-0"></i>
        <h2 class="text-sm font-medium text-gray-900 dark:text-white truncate">
          {{ report.name }}
        </h2>
      </div>
      <button
        @click="$emit('close')"
        class="inline-flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-md text-gray-500 transition-colors hover:bg-gray-200 dark:text-gray-400 dark:hover:bg-gray-700"
        title="关闭"
      >
        <i class="fa-solid fa-xmark text-sm"></i>
      </button>
    </div>

    <!-- 内容区 -->
    <div class="flex-1 overflow-auto bg-white px-4 py-4 dark:bg-gray-900">
      <div class="rounded-lg border border-gray-200 bg-gray-50 p-4 dark:border-gray-700 dark:bg-gray-800">
        <pre class="text-sm leading-relaxed text-gray-700 dark:text-gray-300 whitespace-pre-wrap font-sans">
          {{ report.content }}
        </pre>
      </div>
    </div>
  </aside>
</template>

<script setup lang="ts">
interface Props {
  report: {
    name: string
    content: string
  }
  panelHeightClass: string
}

defineProps<Props>()

defineEmits<{
  (e: 'close'): void
}>()
</script>
