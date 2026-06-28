<script setup lang="ts">
type ResultTabStatus = 'success' | 'error' | 'none'

interface ResultTabItem {
  key: string | number
  label: string
  title?: string
  status?: ResultTabStatus
}

interface Props {
  tabs: ResultTabItem[]
  activeIndex: number
  showStatus?: boolean
}

withDefaults(defineProps<Props>(), {
  showStatus: false,
})

const emit = defineEmits<{
  'update:activeIndex': [value: number]
}>()
</script>

<template>
  <div class="shrink-0 max-w-full min-w-0 border-b border-black/[0.06] bg-gradient-to-br from-gray-50 to-slate-50 px-3 py-2">
    <div class="max-w-full min-w-0 overflow-x-auto">
      <div class="inline-flex min-w-0 items-center gap-1.5 rounded-lg bg-black/[0.04] p-1 align-top">
        <button
          v-for="(tab, index) in tabs"
          :key="tab.key"
          type="button"
          :class="[
            'inline-flex h-8 max-w-[180px] shrink-0 items-center gap-1.5 rounded-md border px-3 text-[12px] font-medium transition-all duration-150 sm:max-w-[220px]',
            activeIndex === index
              ? 'border-[#1f82d9] bg-[#e8f3ff] text-[#1f82d9]'
              : 'border-transparent bg-transparent text-gray-600 hover:bg-white hover:text-[#1f82d9]'
          ]"
          :title="tab.title || tab.label"
          @click="emit('update:activeIndex', index)"
        >
          <i
            v-if="showStatus && tab.status === 'error'"
            class="fa-solid fa-circle-xmark shrink-0 text-[10px] text-red-500"
          ></i>
          <i
            v-else-if="showStatus && tab.status === 'success'"
            class="fa-solid fa-circle-check shrink-0 text-[10px] text-emerald-500"
          ></i>
          <span class="min-w-0 truncate">{{ tab.label }}</span>
        </button>
      </div>
    </div>
  </div>
</template>
