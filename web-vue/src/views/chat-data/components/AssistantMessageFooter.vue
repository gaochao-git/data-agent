<script setup lang="ts">
import AiGeneratedNotice from './AiGeneratedNotice.vue'

interface Props {
  timestamp?: string
  align?: 'between' | 'end'
}

withDefaults(defineProps<Props>(), {
  timestamp: '',
  align: 'between',
})
</script>

<template>
  <div
    :class="[
      'flex items-center gap-2 px-1',
      align === 'end' ? 'justify-end' : 'justify-between'
    ]"
  >
    <div class="flex items-center gap-2">
      <slot />
    </div>
    <div class="ml-auto flex items-center gap-3">
      <AiGeneratedNotice />
      <span v-if="timestamp" class="whitespace-nowrap text-[11px] text-gray-400">
        {{ timestamp }}
      </span>
    </div>
  </div>
</template>
