<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch, type PropType } from 'vue'

interface ConversationTurnMessage {
  id: string
  role: string
  content?: string
}

const props = defineProps({
  messages: {
    type: Array as PropType<ConversationTurnMessage[]>,
    default: (): ConversationTurnMessage[] => [],
  },
  container: {
    type: Object as PropType<HTMLElement | null>,
    default: null,
  },
})

const activeTurnId = ref('')
const contentIsLong = ref(false)
const expanded = ref(false)
let resizeObserver: ResizeObserver | null = null
let observedContainer: HTMLElement | null = null

const turns = computed(() => props.messages
  .filter(message => message?.role === 'user' && message.id)
  .map(message => ({
    id: String(message.id),
    content: String(message.content || '').trim(),
  })))

const shouldShow = computed(() => turns.value.length > 5 || contentIsLong.value)

const getTurnElement = (turnId: string): HTMLElement | null => {
  const container = props.container
  if (!container) return null
  const element = Array.from(container.querySelectorAll('[data-conversation-turn-id]'))
    .find(item => item.getAttribute('data-conversation-turn-id') === turnId)
  return element instanceof HTMLElement ? element : null
}

const getElementTop = (element: HTMLElement, container: HTMLElement): number => {
  const elementRect = element.getBoundingClientRect()
  const containerRect = container.getBoundingClientRect()
  return elementRect.top - containerRect.top + container.scrollTop
}

const updateNavigator = () => {
  const container = props.container
  if (!container) {
    contentIsLong.value = false
    activeTurnId.value = ''
    return
  }

  contentIsLong.value = container.scrollHeight > container.clientHeight * 2
  const viewportAnchor = container.scrollTop + Math.max(container.clientHeight * 0.25, 24)
  let currentId = turns.value[0]?.id || ''
  for (const turn of turns.value) {
    const element = getTurnElement(turn.id)
    if (element && getElementTop(element, container) <= viewportAnchor) {
      currentId = turn.id
    }
  }
  activeTurnId.value = currentId
}

const scheduleNavigatorUpdate = () => {
  nextTick(updateNavigator)
}

const scrollToTurn = (turnId: string) => {
  const container = props.container
  const element = getTurnElement(turnId)
  if (!container || !element) return
  activeTurnId.value = turnId
  const top = Math.max(getElementTop(element, container) - 24, 0)
  if (typeof container.scrollTo === 'function') {
    container.scrollTo({ top, behavior: 'smooth' })
  } else {
    container.scrollTop = top
  }
}

const connectResizeObserver = () => {
  resizeObserver?.disconnect()
  observedContainer?.removeEventListener('scroll', updateNavigator)
  const container = props.container
  observedContainer = container
  container?.addEventListener('scroll', updateNavigator)
  if (container && typeof ResizeObserver !== 'undefined') {
    resizeObserver = new ResizeObserver(scheduleNavigatorUpdate)
    resizeObserver.observe(container)
  }
}

watch(() => props.container, () => {
  connectResizeObserver()
  scheduleNavigatorUpdate()
})

watch(() => props.messages, scheduleNavigatorUpdate, { deep: true })

onMounted(() => {
  connectResizeObserver()
  scheduleNavigatorUpdate()
})

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  observedContainer?.removeEventListener('scroll', updateNavigator)
})
</script>

<template>
  <div
    v-if="shouldShow"
    class="absolute right-3 top-1/2 z-20 hidden -translate-y-1/2 lg:block"
    aria-label="对话轮次定位"
    @mouseenter="expanded = true"
    @mouseleave="expanded = false"
  >
    <div
      v-if="expanded"
      class="max-h-[55vh] w-56 overflow-y-auto rounded-xl border border-slate-200 bg-white py-1.5 shadow-[0_8px_24px_rgba(15,23,42,0.12)]"
    >
      <button
        v-for="(turn, index) in turns"
        :key="turn.id"
        type="button"
        :class="[
          'flex w-full items-center justify-end gap-2 rounded-lg border-none bg-transparent px-3 py-2 text-xs',
          activeTurnId === turn.id
            ? 'cursor-default font-medium text-[#1677FF]'
            : 'cursor-pointer text-slate-600 hover:bg-slate-50',
        ]"
        @click="scrollToTurn(turn.id)"
      >
        <span class="min-w-0 truncate">{{ turn.content || `第 ${index + 1} 次提问` }}</span>
        <span
          :class="[
            'h-0.5 w-3 shrink-0 rounded-full',
            activeTurnId === turn.id ? 'bg-[#1677FF]' : 'bg-slate-300',
          ]"
        ></span>
      </button>
    </div>
    <nav v-else class="flex max-h-[55vh] flex-col items-end gap-1.5 overflow-hidden py-1 pr-3">
      <button
        v-for="(turn, index) in turns"
        :key="turn.id"
        type="button"
        :class="[
          'flex h-3 shrink-0 items-center justify-end border-none bg-transparent p-0',
          activeTurnId === turn.id ? 'cursor-default' : 'cursor-pointer',
        ]"
        :aria-label="`定位到第 ${index + 1} 次提问：${turn.content || '空消息'}`"
        :title="turn.content || `第 ${index + 1} 次提问`"
        @click="scrollToTurn(turn.id)"
      >
        <span
          :class="[
            'block rounded-full transition-colors duration-150',
            activeTurnId === turn.id
              ? 'h-0.5 w-4 bg-[#1677FF]'
              : 'h-0.5 w-3 bg-slate-300 hover:bg-slate-500',
          ]"
        ></span>
      </button>
    </nav>
  </div>
</template>
