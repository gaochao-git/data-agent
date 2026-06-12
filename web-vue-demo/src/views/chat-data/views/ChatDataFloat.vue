<script setup lang="ts">
/**
 * 智能问数悬浮窗组件
 * 可拖动、可调整大小的悬浮窗口
 *
 * 用法一：内置悬浮按钮
 *   <ChatDataFloat />
 *
 * 用法二：自定义触发按钮（ref 调用）
 *   <ChatDataFloat ref="floatRef" :show-fab="false" />
 *   <button @click="floatRef.open()">打开</button>
 *
 * 用法三：v-model 控制
 *   <ChatDataFloat v-model:visible="show" :show-fab="false" />
 *   <ChatDataFloat v-model:visible="show" :show-fab="false" :fullscreen="true" />
 *
 * 用法四：插槽自定义触发按钮
 *   <ChatDataFloat>
 *     <template #trigger="{ open }">
 *       <button @click="open">我的按钮</button>
 *     </template>
 *   </ChatDataFloat>
 */
import { ref, computed, watch } from 'vue'
import ChatDataPage from './index.vue'
import type { ChatDataOptions } from '../types'

interface Props {
  /** 外部控制显隐（不传则使用内置悬浮按钮） */
  visible?: boolean
  /** 隐藏内置悬浮按钮 */
  showFab?: boolean
  /** 是否默认最大化打开 */
  fullscreen?: boolean
  /** 初始窗口宽度 */
  initialWidth?: number
  /** 初始窗口高度 */
  initialHeight?: number
  /** 配置参数 */
  options?: ChatDataOptions
}

const props = withDefaults(defineProps<Props>(), {
  showFab: true,
  fullscreen: false,
  initialWidth: 1100,
  initialHeight: 700,
})

const emit = defineEmits<{
  'update:visible': [val: boolean]
}>()

const isOpen = ref(false)
const isMaximized = ref(false)

// 窗口尺寸和位置
const winWidth = ref(props.initialWidth)
const winHeight = ref(props.initialHeight)
const winX = ref(-1) // -1 表示还没初始化
const winY = ref(-1)

// 最大化前保存的状态
const beforeMaximize = ref({ x: 0, y: 0, w: 0, h: 0 })

// 最小尺寸
const MIN_W = 480
const MIN_H = 400

// 初始化位置（居中偏右下）
const initPosition = () => {
  if (winX.value === -1) {
    winX.value = Math.max(32, window.innerWidth - winWidth.value - 48)
    winY.value = Math.max(32, (window.innerHeight - winHeight.value) / 2)
  }
}

const resetWindowState = () => {
  winWidth.value = props.initialWidth
  winHeight.value = props.initialHeight
  winX.value = -1
  winY.value = -1
  beforeMaximize.value = { x: 0, y: 0, w: 0, h: 0 }
}

const applyOpenState = (nextOpen: boolean) => {
  if (nextOpen) {
    initPosition()
    beforeMaximize.value = {
      x: winX.value,
      y: winY.value,
      w: winWidth.value,
      h: winHeight.value,
    }
    isOpen.value = true
    isMaximized.value = props.fullscreen
    return
  }

  isOpen.value = false
  isMaximized.value = false
  resetWindowState()
}

// 打开窗口
const open = () => {
  if (isOpen.value) return
  applyOpenState(true)
  emit('update:visible', true)
}

// 关闭窗口
const close = () => {
  if (!isOpen.value) return
  applyOpenState(false)
  emit('update:visible', false)
}

// 切换显示
const toggle = () => {
  if (isOpen.value) {
    close()
  } else {
    open()
  }
}

// 外部 v-model:visible 控制
watch(() => props.visible, (val) => {
  if (val === undefined) return
  if (val === isOpen.value) return
  applyOpenState(Boolean(val))
}, { immediate: true })

// 暴露方法给父组件
defineExpose({ open, close, toggle })

// 最大化/还原
const toggleMaximize = () => {
  if (isMaximized.value) {
    winX.value = beforeMaximize.value.x
    winY.value = beforeMaximize.value.y
    winWidth.value = beforeMaximize.value.w
    winHeight.value = beforeMaximize.value.h
    isMaximized.value = false
  } else {
    beforeMaximize.value = { x: winX.value, y: winY.value, w: winWidth.value, h: winHeight.value }
    winX.value = 0
    winY.value = 0
    winWidth.value = window.innerWidth
    winHeight.value = window.innerHeight
    isMaximized.value = true
  }
}

// --- 拖动逻辑 ---
const isDragging = ref(false)

const startDrag = (e: MouseEvent) => {
  if (isMaximized.value) return
  e.preventDefault()
  isDragging.value = true
  const startX = e.clientX
  const startY = e.clientY
  const startWinX = winX.value
  const startWinY = winY.value

  const onMove = (ev: MouseEvent) => {
    ev.preventDefault()
    winX.value = Math.max(0, Math.min(window.innerWidth - 100, startWinX + ev.clientX - startX))
    winY.value = Math.max(0, Math.min(window.innerHeight - 48, startWinY + ev.clientY - startY))
  }
  const onUp = () => {
    isDragging.value = false
    document.removeEventListener('mousemove', onMove)
    document.removeEventListener('mouseup', onUp)
  }
  document.addEventListener('mousemove', onMove)
  document.addEventListener('mouseup', onUp)
}

// --- 缩放逻辑 ---
type ResizeDir = 'n' | 's' | 'e' | 'w' | 'ne' | 'nw' | 'se' | 'sw'
const isResizing = ref(false)

const startResize = (dir: ResizeDir, e: MouseEvent) => {
  if (isMaximized.value) return
  e.preventDefault()
  e.stopPropagation()
  isResizing.value = true

  const startX = e.clientX
  const startY = e.clientY
  const startW = winWidth.value
  const startH = winHeight.value
  const startWX = winX.value
  const startWY = winY.value

  const onMove = (ev: MouseEvent) => {
    ev.preventDefault()
    const dx = ev.clientX - startX
    const dy = ev.clientY - startY

    if (dir.includes('e')) winWidth.value = Math.max(MIN_W, startW + dx)
    if (dir.includes('s')) winHeight.value = Math.max(MIN_H, startH + dy)
    if (dir.includes('w')) {
      const newW = Math.max(MIN_W, startW - dx)
      winX.value = startWX + startW - newW
      winWidth.value = newW
    }
    if (dir.includes('n')) {
      const newH = Math.max(MIN_H, startH - dy)
      winY.value = startWY + startH - newH
      winHeight.value = newH
    }
  }
  const onUp = () => {
    isResizing.value = false
    document.removeEventListener('mousemove', onMove)
    document.removeEventListener('mouseup', onUp)
  }
  document.addEventListener('mousemove', onMove)
  document.addEventListener('mouseup', onUp)
}

// 窗口样式
const windowStyle = computed(() => ({
  left: `${winX.value}px`,
  top: `${winY.value}px`,
  width: `${winWidth.value}px`,
  height: `${winHeight.value}px`,
}))
</script>

<template>
  <!-- 内置悬浮按钮（可通过 :show-fab="false" 隐藏） -->
  <slot name="trigger" :open="open" :toggle="toggle" :is-open="isOpen">
    <div
      v-if="showFab"
      v-show="!isOpen"
      class="chatdata-fab"
      @click="open()"
    >
      <i class="fa-solid fa-comments text-lg"></i>
    </div>
  </slot>

  <!-- 悬浮窗口 -->
  <Teleport to="body">
    <div
      v-if="isOpen"
      class="chatdata-float-window"
      :class="{ 'chatdata-float-maximized': isMaximized, 'chatdata-float-resizing': isResizing || isDragging }"
      :style="isMaximized ? {} : windowStyle"
    >
      <!-- 8 个缩放手柄 -->
      <template v-if="!isMaximized">
        <div class="chatdata-resize-handle chatdata-resize-n" @mousedown="startResize('n', $event)"></div>
        <div class="chatdata-resize-handle chatdata-resize-s" @mousedown="startResize('s', $event)"></div>
        <div class="chatdata-resize-handle chatdata-resize-e" @mousedown="startResize('e', $event)"></div>
        <div class="chatdata-resize-handle chatdata-resize-w" @mousedown="startResize('w', $event)"></div>
        <div class="chatdata-resize-handle chatdata-resize-ne" @mousedown="startResize('ne', $event)"></div>
        <div class="chatdata-resize-handle chatdata-resize-nw" @mousedown="startResize('nw', $event)"></div>
        <div class="chatdata-resize-handle chatdata-resize-se" @mousedown="startResize('se', $event)"></div>
        <div class="chatdata-resize-handle chatdata-resize-sw" @mousedown="startResize('sw', $event)"></div>
      </template>

      <!-- 标题栏（可拖动） -->
      <div class="chatdata-float-header" @mousedown="startDrag" @dblclick="toggleMaximize">
        <div class="flex items-center gap-2">
          <i class="fa-solid fa-comments text-blue-500 text-sm"></i>
          <span class="text-sm font-medium text-gray-800">智能问数</span>
        </div>
        <div class="flex items-center gap-1">
          <button class="chatdata-float-btn" :title="isMaximized ? '还原' : '最大化'" @click.stop="toggleMaximize">
            <i :class="['fa-regular text-[11px]', isMaximized ? 'fa-window-restore' : 'fa-square']"></i>
          </button>
          <button class="chatdata-float-btn chatdata-float-btn-close" title="关闭" @click.stop="close">
            <i class="fa-solid fa-xmark text-xs"></i>
          </button>
        </div>
      </div>

      <!-- 内容区域 -->
      <div class="chatdata-float-body">
        <ChatDataPage :options="options" />
      </div>
    </div>
  </Teleport>
</template>

<style>
/* === 悬浮按钮 === */
.chatdata-fab {
  position: fixed;
  z-index: 1501;
  width: 52px;
  height: 52px;
  border-radius: 50%;
  background: linear-gradient(135deg, #3b82f6 0%, #2563eb 100%);
  color: white;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  box-shadow: 0 4px 14px rgba(37, 99, 235, 0.4);
  transition: transform 0.2s, box-shadow 0.2s;
}
.chatdata-fab:hover {
  transform: scale(1.08);
  box-shadow: 0 6px 20px rgba(37, 99, 235, 0.5);
}

/* === 悬浮窗口 === */
.chatdata-float-window {
  position: fixed;
  z-index: 3000;
  display: flex;
  flex-direction: column;
  background: #fff;
  border-radius: 12px;
  box-shadow: 0 8px 40px rgba(0, 0, 0, 0.15), 0 0 0 1px rgba(0, 0, 0, 0.06);
  overflow: hidden;
  transition: box-shadow 0.2s;
}
.chatdata-float-maximized {
  top: 0 !important;
  left: 0 !important;
  width: 100vw !important;
  height: 100vh !important;
  border-radius: 0;
}
.chatdata-float-resizing {
  user-select: none;
  transition: none;
}

/* === 标题栏 === */
.chatdata-float-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 40px;
  padding: 0 12px;
  background: #f8fafc;
  border-bottom: 1px solid #e2e8f0;
  cursor: grab;
  flex-shrink: 0;
  user-select: none;
}
.chatdata-float-header:active {
  cursor: grabbing;
}

/* 标题栏按钮 */
.chatdata-float-btn {
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  background: transparent;
  border-radius: 6px;
  color: #64748b;
  cursor: pointer;
  transition: all 0.15s;
}
.chatdata-float-btn:hover {
  background: #e2e8f0;
  color: #334155;
}
.chatdata-float-btn-close:hover {
  background: #fee2e2;
  color: #dc2626;
}

/* === 内容区域 === */
.chatdata-float-body {
  flex: 1;
  overflow: hidden;
  min-height: 0;
}

/* 覆盖内嵌页面的 viewport 高度 */
.chatdata-float-body .chatdata-root {
  height: 100% !important;
}
.chatdata-float-body .chatdata-root > .flex.overflow-hidden {
  height: calc(100% - 48px) !important;
}

/* === 8方向缩放手柄 === */
.chatdata-resize-handle {
  position: absolute;
  z-index: 10;
}
.chatdata-resize-n { top: -3px; left: 8px; right: 8px; height: 6px; cursor: n-resize; }
.chatdata-resize-s { bottom: -3px; left: 8px; right: 8px; height: 6px; cursor: s-resize; }
.chatdata-resize-e { right: -3px; top: 8px; bottom: 8px; width: 6px; cursor: e-resize; }
.chatdata-resize-w { left: -3px; top: 8px; bottom: 8px; width: 6px; cursor: w-resize; }
.chatdata-resize-ne { top: -3px; right: -3px; width: 12px; height: 12px; cursor: ne-resize; }
.chatdata-resize-nw { top: -3px; left: -3px; width: 12px; height: 12px; cursor: nw-resize; }
.chatdata-resize-se { bottom: -3px; right: -3px; width: 12px; height: 12px; cursor: se-resize; }
.chatdata-resize-sw { bottom: -3px; left: -3px; width: 12px; height: 12px; cursor: sw-resize; }
</style>
