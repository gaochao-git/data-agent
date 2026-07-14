<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

type AgentId = 'chat_data' | 'chat_data_data_analyze'

interface AgentOption {
  id: AgentId
  label: string
  disabled?: boolean
}

interface Props {
  modelValue: AgentId
  options: readonly AgentOption[]
}

const props = defineProps<Props>()

const emit = defineEmits<{
  'update:modelValue': [value: AgentId]
  disabledClick: [option: AgentOption]
}>()

const open = ref(false)
const rootRef = ref<HTMLElement | null>(null)
const selectedLabel = computed(() => props.options.find(agent => agent.id === props.modelValue)?.label || '问数')

const selectAgent = (agent: AgentOption) => {
  if (agent.disabled) {
    open.value = false
    emit('disabledClick', agent)
    return
  }
  emit('update:modelValue', agent.id)
  open.value = false
}

const handleDocumentClick = (event: MouseEvent) => {
  const target = event.target
  if (!(target instanceof Node)) return
  if (rootRef.value?.contains(target)) return
  open.value = false
}

onMounted(() => {
  document.addEventListener('click', handleDocumentClick)
})

onBeforeUnmount(() => {
  document.removeEventListener('click', handleDocumentClick)
})
</script>

<template>
  <div ref="rootRef" class="relative">
    <button
      type="button"
      class="flex items-center gap-1.5 whitespace-nowrap px-2 py-1 rounded-full border-none bg-transparent text-xs text-gray-500 outline-none cursor-pointer hover:bg-black/[0.05]"
      @click="open = !open"
    >
      <i class="fa-solid fa-robot text-[10px] text-gray-400"></i>
      <span class="toolbar-label">{{ selectedLabel }}</span>
      <i class="fa-solid fa-chevron-down text-[8px] text-gray-400"></i>
    </button>
    <div
      v-if="open"
      class="absolute left-0 bottom-8 z-50 min-w-[130px] overflow-hidden rounded-lg border border-gray-200 bg-white py-1 text-xs text-gray-600 shadow-lg"
    >
      <button
        v-for="agent in options"
        :key="agent.id"
        type="button"
        :aria-disabled="agent.disabled"
        :title="agent.disabled ? '功能暂未开放' : undefined"
        :class="[
          'flex w-full items-center justify-between gap-2 border-none bg-transparent px-3 py-1.5 text-left text-xs',
          modelValue === agent.id ? 'text-blue-600 bg-blue-50' : 'text-gray-600',
          agent.disabled ? '!text-gray-400 cursor-not-allowed bg-gray-50' : 'cursor-pointer hover:bg-gray-50'
        ]"
        @click="selectAgent(agent)"
      >
        <span>{{ agent.label }}</span>
        <i v-if="!agent.disabled && modelValue === agent.id" class="fa-solid fa-check text-[10px]"></i>
      </button>
    </div>
  </div>
</template>
