<template>
  <div class="rounded-lg border border-gray-200 bg-white p-3 dark:border-gray-700 dark:bg-gray-800">
    <h4 class="mb-2 text-sm font-bold text-gray-900 dark:text-white">
      {{ title }}
    </h4>
    <ul class="space-y-1">
      <li v-if="!items || items.length === 0" class="text-sm text-gray-500 dark:text-gray-400">
        {{ isCompleted ? '暂无' : '检索中...' }}
      </li>
      <li v-else v-for="(item, index) in items" :key="index" class="flex gap-2 text-sm">
        <span class="text-gray-400 flex-shrink-0">•</span>
        <span
          @click="handleClick(item)"
          class="text-indigo-600 hover:text-indigo-800 dark:text-indigo-400 dark:hover:text-indigo-300 cursor-pointer hover:underline"
        >
          {{ ensureBookTitle(item.law_name) }}{{ item.law_item_name }}
        </span>
      </li>
    </ul>
  </div>
</template>

<script setup lang="ts">
interface LawItem {
  law_name: string
  law_item_name: string
  law_item_content: string
}

interface Props {
  title: string
  items: LawItem[]
  isCompleted: boolean
  onLawClick?: (law: LawItem) => void
}

const props = defineProps<Props>()

function ensureBookTitle(lawName: string): string {
  if (!lawName) return ''
  if (lawName.startsWith('《') && lawName.endsWith('》')) {
    return lawName
  }
  return `《${lawName}》`
}

function handleClick(item: LawItem) {
  if (props.onLawClick) {
    props.onLawClick(item)
  }
}
</script>
