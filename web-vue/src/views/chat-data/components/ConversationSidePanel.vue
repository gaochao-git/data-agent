<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'

interface Props {
  open: boolean
  title: string
  subtitle?: string
  triggerLabel?: string
  showTrigger?: boolean
  /** 是否渲染面板头部的关闭按钮（✕）。调用方若已有其它开关入口可传 false 隐藏。 */
  showClose?: boolean
}

withDefaults(defineProps<Props>(), {
  subtitle: '',
  triggerLabel: '工作区',
  showTrigger: true,
  showClose: true,
})

const emit = defineEmits<{
  open: []
  close: []
}>()

const MIN_PANEL_WIDTH = 320
const MAX_PANEL_WIDTH = 900
// 会话区至少要保留的宽度，避免拖拽把对话挤成 0 宽
const MIN_CHAT_WIDTH = 280

// 宽度：null 表示沿用样式表的 `w-[40%]`（含 min/max clamp），首次拖拽后转为固定 px
const panelWidth = ref<number | null>(null)
const isResizing = ref(false)
const panelRef = ref<HTMLElement | null>(null)
let detachResizeListeners: (() => void) | null = null

// 上限取决于父容器实际可用宽度，而非视口——父容器被左右两栏挤占后往往远小于视口
const maxAllowedWidth = () => {
  const container = panelRef.value?.parentElement
  const available = container?.getBoundingClientRect().width ?? window.innerWidth
  return Math.max(MIN_PANEL_WIDTH, Math.min(MAX_PANEL_WIDTH, Math.round(available - MIN_CHAT_WIDTH)))
}

const clampPanelWidth = (width: number, upper: number = maxAllowedWidth()) => (
  Math.max(MIN_PANEL_WIDTH, Math.min(upper, Math.round(width)))
)

const panelStyle = computed(() => (panelWidth.value === null ? undefined : {
  width: `${panelWidth.value}px`,
  // 内联覆盖 min-w-[380px] / max-w-[580px]，否则拖拽会被样式表的 clamp 顶回去
  minWidth: `${MIN_PANEL_WIDTH}px`,
  maxWidth: `${MAX_PANEL_WIDTH}px`,
}))

const stopResize = () => {
  isResizing.value = false
  document.body.classList.remove('is-resizing')
  detachResizeListeners?.()
  detachResizeListeners = null
}

const startResize = (event: MouseEvent) => {
  const panel = panelRef.value
  if (!panel) return
  event.preventDefault()
  const startX = event.clientX
  const startWidth = panel.getBoundingClientRect().width
  // 容器已放不下当前宽度时（默认 40% / 380 可能已超）只允许变窄、不再变宽，避免一按就跳变
  const dragMax = Math.max(startWidth, maxAllowedWidth())
  isResizing.value = true
  document.body.classList.add('is-resizing')

  const onMove = (moveEvent: MouseEvent) => {
    // 面板贴右，鼠标左移 => 面板变宽
    panelWidth.value = clampPanelWidth(startWidth + (startX - moveEvent.clientX), dragMax)
  }
  const onUp = () => stopResize()
  const onWindowResize = () => {
    if (panelWidth.value !== null) panelWidth.value = clampPanelWidth(panelWidth.value)
  }

  // capture 保证光标移出面板甚至移出窗口后仍持续收到事件
  document.addEventListener('mousemove', onMove, true)
  document.addEventListener('mouseup', onUp, true)
  window.addEventListener('resize', onWindowResize)
  detachResizeListeners = () => {
    document.removeEventListener('mousemove', onMove, true)
    document.removeEventListener('mouseup', onUp, true)
    window.removeEventListener('resize', onWindowResize)
  }
}

onBeforeUnmount(stopResize)
</script>

<template>
  <button
    v-if="showTrigger && !open"
    type="button"
    class="absolute right-3 top-1/2 z-10 flex -translate-y-1/2 flex-col items-center gap-1 rounded-l-xl border border-r-0 border-slate-200 bg-white px-2.5 py-3 text-xs font-medium text-slate-500 shadow-[0_8px_24px_rgba(15,23,42,0.12)] transition-colors hover:bg-blue-50 hover:text-blue-700"
    @click="emit('open')"
  >
    <span class="[writing-mode:vertical-rl] tracking-wider">{{ triggerLabel }}</span>
  </button>

  <aside
    v-if="open"
    ref="panelRef"
    class="relative flex w-[40%] min-w-[380px] max-w-[580px] flex-col border-l border-slate-200 bg-white"
    :style="panelStyle"
  >
    <!-- 左边缘拖拽调宽 -->
    <div
      class="group absolute left-0 top-0 z-10 h-full w-1.5 cursor-col-resize transition-colors"
      :class="isResizing ? 'bg-blue-400/60' : 'hover:bg-blue-300/60'"
      @mousedown="startResize"
    >
      <div
        class="absolute left-1/2 top-1/2 h-8 w-0.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-slate-300 transition-colors group-hover:bg-blue-400"
        :class="{ 'bg-blue-400': isResizing }"
      />
    </div>

    <div class="flex h-14 items-center gap-3 border-b border-slate-100 px-4">
      <div class="flex min-w-0 flex-1 items-center gap-2">
        <div class="min-w-0">
          <div class="truncate text-sm font-semibold text-slate-800">{{ title }}</div>
          <div v-if="subtitle" class="mt-0.5 truncate font-mono text-xs text-slate-400">
            {{ subtitle }}
          </div>
        </div>
        <!-- 头部附加信息（如工作区文件数量、一键折叠目录）：紧贴标题右侧，
             供调用方把原本挤在内容区卡片标题行上的状态挪上来 -->
        <slot name="header-extra" />
      </div>
      <!-- 关闭按钮：工具栏右上角已有同功能的开关按钮，调用方传 show-close=false 时隐藏。
           保留实现而非删除，需要时把 :show-close="false" 去掉即可恢复。 -->
      <button
        v-if="showClose"
        type="button"
        class="h-8 w-8 rounded-md border border-slate-200 bg-white text-slate-500 hover:bg-slate-50"
        @click="emit('close')"
      >
        <i class="fa-solid fa-xmark"></i>
      </button>
    </div>
    <div class="flex-1 overflow-auto p-4">
      <slot />
    </div>
  </aside>
</template>
