<template>
  <Teleport to="body">
    <Transition name="fade-scale">
      <div
        v-if="isOpen"
        class="fixed z-[9999]"
        :style="{ left: `${position.x}px`, top: `${position.y}px`, width: `${size.width}px`, height: `${size.height}px` }"
        @mousedown="handleDragStart"
        @mousemove="handleMouseMove"
        @mouseup="handleMouseUp"
      >
        <div
          ref="cardRef"
          class="flex h-full w-full flex-col rounded-xl border border-gray-200 bg-white shadow-2xl dark:border-gray-700 dark:bg-gray-800"
          @mouseup.stop
        >
          <!-- 头部 - 可拖动区域 -->
          <div
            class="flex items-center justify-between px-4 py-3 select-none flex-shrink-0 border-b border-gray-200 bg-indigo-50 rounded-t-xl dark:border-gray-700 dark:bg-indigo-900/30"
            :style="{ cursor: isDragging ? 'grabbing' : 'grab' }"
            @mousedown="handleDragStart"
          >
            <div class="flex items-center gap-2 min-w-0">
              <i class="fa-solid fa-scale-balanced text-indigo-600 dark:text-indigo-400 text-sm"></i>
              <span class="text-sm font-semibold text-gray-900 dark:text-white truncate">
                {{ lawName }}
              </span>
              <span class="text-xs text-gray-400 ml-2">| 点击拖动位置</span>
            </div>
            <div class="flex items-center gap-1 flex-shrink-0">
              <button
                type="button"
                @mousedown.stop
                @click="onClose"
                class="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
              >
                <i class="fa-solid fa-xmark"></i>
              </button>
            </div>
          </div>

          <!-- 内容区域 - 模拟A4纸样式 -->
          <div class="flex-1 overflow-auto p-6 bg-gray-100 dark:bg-gray-900">
            <div class="mx-auto min-h-[842px] max-w-[595px] rounded bg-white p-10 shadow-lg dark:bg-gray-800">
              <div v-if="isLoading" class="flex h-full items-center justify-center">
                <div class="text-center">
                  <i class="fa-solid fa-spinner fa-spin mb-3 text-2xl text-indigo-500"></i>
                  <p class="text-sm text-gray-500">正在加载法规原文...</p>
                </div>
              </div>
              <div v-else class="text-sm leading-relaxed whitespace-pre-wrap text-gray-800 dark:text-gray-200">
                {{ content }}
              </div>
            </div>
          </div>

          <!-- 底部提示 -->
          <div class="flex items-center justify-between px-4 py-2 border-t border-gray-200 bg-gray-50 text-xs text-gray-500 flex-shrink-0 rounded-b-xl dark:border-gray-700 dark:bg-gray-800/50 dark:text-gray-400">
            <span>
              <i class="fa-solid fa-arrows-up-down-left-right mr-1"></i>
              拖动边缘调整大小
            </span>
            <span>
              <i class="fa-solid fa-file-pdf mr-1"></i>
              法规原文预览
            </span>
          </div>

          <!-- 调整大小的手柄 -->
          <!-- 四条边 -->
          <div
            class="absolute top-0 left-2 right-2 h-1 cursor-n-resize"
            @mousedown="(e) => handleResizeStart(e, 'n')"
          />
          <div
            class="absolute bottom-0 left-2 right-2 h-1 cursor-s-resize"
            @mousedown="(e) => handleResizeStart(e, 's')"
          />
          <div
            class="absolute left-0 top-2 bottom-2 w-1 cursor-w-resize"
            @mousedown="(e) => handleResizeStart(e, 'w')"
          />
          <div
            class="absolute right-0 top-2 bottom-2 w-1 cursor-e-resize"
            @mousedown="(e) => handleResizeStart(e, 'e')"
          />
          <!-- 四个角 -->
          <div
            class="absolute top-0 left-0 w-3 h-3 cursor-nw-resize"
            @mousedown="(e) => handleResizeStart(e, 'nw')"
          />
          <div
            class="absolute top-0 right-0 w-3 h-3 cursor-ne-resize"
            @mousedown="(e) => handleResizeStart(e, 'ne')"
          />
          <div
            class="absolute bottom-0 left-0 w-3 h-3 cursor-sw-resize"
            @mousedown="(e) => handleResizeStart(e, 'sw')"
          />
          <div
            class="absolute bottom-0 right-0 w-3 h-3 cursor-se-resize"
            @mousedown="(e) => handleResizeStart(e, 'se')"
          />
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup lang="ts">
import { ref, watch, onMounted } from 'vue'

interface Props {
  is: boolean
  lawName: string
  lawItemName: string
  onClose: () => void
}

const props = defineProps<Props>()

// 卡片位置
const position = ref({ x: 100, y: 100 })
// 卡片尺寸
const size = ref({ width: 700, height: 600 })
// 是否正在拖动位置
const isDragging = ref(false)
// 是否正在调整大小
const isResizing = ref(false)
// 调整大小的方向
const resizeDirection = ref('')
// 拖动/调整起始点
const dragStartRef = ref({ x: 0, y: 0, posX: 0, posY: 0, width: 0, height: 0 })
// 卡片 ref
const cardRef = ref<HTMLDivElement | null>(null)
// 文档内容
const content = ref('')
// 加载状态
const isLoading = ref(false)

// 最小尺寸
const MIN_WIDTH = 400
const MIN_HEIGHT = 300

// 初始化位置
watch(
  () => props.is,
  (isOpen) => {
    if (isOpen) {
      // 居中显示
      const viewportWidth = window.innerWidth
      const viewportHeight = window.innerHeight
      const cardWidth = size.value.width
      const cardHeight = size.value.height

      let x = (viewportWidth - cardWidth) / 2
      let y = (viewportHeight - cardHeight) / 2

      // 确保在视口内
      x = Math.max(20, Math.min(x, viewportWidth - cardWidth - 20))
      y = Math.max(20, Math.min(y, viewportHeight - cardHeight - 20))

      position.value = { x, y }
    }
  }
)

// 加载法律文档内容
watch(
  [() => props.is, () => props.lawName, () => props.lawItemName],
  ([isOpen, lawName]) => {
    if (isOpen && lawName) {
      isLoading.value = true
      // 模拟加载，实际应该从后端获取
      setTimeout(() => {
        content.value = '该功能需要连接法律法规数据库，请配置后端接口以获取完整法规原文。'
        isLoading.value = false
      }, 300)
    }
  }
)

// 拖动开始
const handleDragStart = (e: MouseEvent) => {
  if (e.button !== 0) return // 只响应左键
  e.preventDefault()
  e.stopPropagation()
  isDragging.value = true
  dragStartRef.value = {
    x: e.clientX,
    y: e.clientY,
    posX: position.value.x,
    posY: position.value.y,
    width: size.value.width,
    height: size.value.height
  }
  document.addEventListener('mousemove', handleDragMove)
  document.addEventListener('mouseup', handleDragEnd)
}

// 调整大小开始
const handleResizeStart = (e: MouseEvent, direction: string) => {
  if (e.button !== 0) return
  e.preventDefault()
  e.stopPropagation()
  isResizing.value = true
  resizeDirection.value = direction
  dragStartRef.value = {
    x: e.clientX,
    y: e.clientY,
    posX: position.value.x,
    posY: position.value.y,
    width: size.value.width,
    height: size.value.height
  }
  document.addEventListener('mousemove', handleResizeMove)
  document.addEventListener('mouseup', handleResizeEnd)
}

// 拖动中
const handleDragMove = (e: MouseEvent) => {
  if (!isDragging.value) return

  const deltaX = e.clientX - dragStartRef.value.x
  const deltaY = e.clientY - dragStartRef.value.y

  const newX = dragStartRef.value.posX + deltaX
  const newY = dragStartRef.value.posY + deltaY

  // 限制在视口内
  const viewportWidth = window.innerWidth
  const viewportHeight = window.innerHeight

  position.value = {
    x: Math.max(0, Math.min(newX, viewportWidth - size.value.width)),
    y: Math.max(0, Math.min(newY, viewportHeight - size.value.height))
  }
}

// 调整大小中
const handleResizeMove = (e: MouseEvent) => {
  if (!isResizing.value) return

  const deltaX = e.clientX - dragStartRef.value.x
  const deltaY = e.clientY - dragStartRef.value.y

  const viewportWidth = window.innerWidth
  const viewportHeight = window.innerHeight

  let newWidth = dragStartRef.value.width
  let newHeight = dragStartRef.value.height
  let newX = dragStartRef.value.posX
  let newY = dragStartRef.value.posY

  const direction = resizeDirection.value

  // 根据方向调整
  if (direction.includes('e')) {
    newWidth = Math.max(MIN_WIDTH, dragStartRef.value.width + deltaX)
  }
  if (direction.includes('w')) {
    const potentialWidth = dragStartRef.value.width - deltaX
    if (potentialWidth >= MIN_WIDTH) {
      newWidth = potentialWidth
      newX = dragStartRef.value.posX + deltaX
    }
  }
  if (direction.includes('s')) {
    newHeight = Math.max(MIN_HEIGHT, dragStartRef.value.height + deltaY)
  }
  if (direction.includes('n')) {
    const potentialHeight = dragStartRef.value.height - deltaY
    if (potentialHeight >= MIN_HEIGHT) {
      newHeight = potentialHeight
      newY = dragStartRef.value.posY + deltaY
    }
  }

  // 限制不超出视口
  if (newX + newWidth > viewportWidth) {
    newWidth = viewportWidth - newX
  }
  if (newY + newHeight > viewportHeight) {
    newHeight = viewportHeight - newY
  }
  if (newX < 0) {
    newWidth += newX
    newX = 0
  }
  if (newY < 0) {
    newHeight += newY
    newY = 0
  }

  size.value = { width: newWidth, height: newHeight }
  position.value = { x: newX, y: newY }
}

// 拖动结束
const handleDragEnd = () => {
  isDragging.value = false
  document.removeEventListener('mousemove', handleDragMove)
  document.removeEventListener('mouseup', handleDragEnd)
}

// 调整大小结束
const handleResizeEnd = () => {
  isResizing.value = false
  resizeDirection.value = ''
  document.removeEventListener('mousemove', handleResizeMove)
  document.removeEventListener('mouseup', handleResizeEnd)
}

// 清理监听器
onMounted(() => {
  window.addEventListener('mousemove', handleMouseMove)
  window.addEventListener('mouseup', handleMouseUp)
})

const handleMouseMove = (e: MouseEvent) => {
  // 事件处理逻辑在各自的 start/end 方法中
}

const handleMouseUp = (e: MouseEvent) => {
  // 事件处理逻辑在各自的 start/end 方法中
}
</script>

<style scoped>
.fade-scale-enter-active,
.fade-scale-leave-active {
  transition: opacity 0.15s ease;
}
.fade-scale-enter-from,
.fade-scale-leave-to {
  opacity: 0;
}
</style>
