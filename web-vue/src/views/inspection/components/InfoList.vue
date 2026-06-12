<template>
  <div class="rounded-lg bg-gray-50 p-3 dark:bg-gray-900/50">
    <h5 class="mb-2 flex items-center gap-2 text-xs font-semibold text-gray-700 dark:text-gray-300">
      <i :class="`fa-solid ${icon} ${iconColor}`"></i>
      {{ title }}
    </h5>
    <ul class="space-y-1">
      <li v-for="(item, idx) in items" :key="idx" class="flex gap-2 text-xs text-gray-600 dark:text-gray-400">
        <span :class="iconColor">•</span>
        <span class="flex-1">{{ formatDetailItem(item) }}</span>
      </li>
    </ul>
  </div>
</template>

<script setup lang="ts">
interface Props {
  title: string
  icon: string
  iconColor: string
  items: string[]
}

defineProps<Props>()

function formatDetailItem(item: string): string {
  // 简单格式化，可以根据需要扩展
  if (!item) return ''
  return item
    .replace(/\n/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}
</script>
