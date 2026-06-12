<template>
  <div
    ref="wrapperRef"
    class="chat-button-wrapper"
    :style="{ top: chatButtonTop + 'px' }"
    @mousedown="onWrapperMouseDown"
    @click="handleClick"
  >
    <el-badge
      v-if="unreadCount > 0"
      :value="unreadCount"
      :max="99"
      class="chat-badge"
    >
      <el-button
        class="chat-trigger-button"
        type="primary"
        :icon="isOpen ? Close : ChatDotRound"
        circle
        size="large"
      />
    </el-badge>

    <el-button
      v-else
      class="chat-trigger-button"
      type="primary"
      :icon="isOpen ? Close : ChatDotRound"
      circle
      size="large"
    />

    <div
      class="chat-tooltip"
      v-show="!isOpen"
    >
      在线客服
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { ChatDotRound, Close } from '@element-plus/icons-vue'

const props = defineProps({
  unreadCount: {
    type: Number,
    default: 0
  },
  isOpen: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['open-chat'])

const handleClick = () => {
  if (suppressClick.value) {
    suppressClick.value = false
    return
  }
  emit('open-chat')
}

const wrapperRef = ref(null)
const chatButtonTop = ref(Math.round(window.innerHeight * 0.8))
const dragging = ref(false)
const suppressClick = ref(false)
let startY = 0
let startTop = 0
let wrapperHeight = 0

const onWrapperMouseDown = (e) => {
  dragging.value = true
  suppressClick.value = false
  startY = e.clientY
  startTop = chatButtonTop.value
  const el = wrapperRef.value
  wrapperHeight = el ? el.offsetHeight : 0
  window.addEventListener('mousemove', onMouseMove)
  window.addEventListener('mouseup', onMouseUp)
}

const onMouseMove = (e) => {
  if (!dragging.value) return
  const deltaY = e.clientY - startY
  if (Math.abs(deltaY) > 3) suppressClick.value = true
  const vh = window.innerHeight
  const minTop = 10
  const maxTop = Math.max(minTop, vh - wrapperHeight - 10)
  const nextTop = startTop + deltaY
  chatButtonTop.value = Math.min(Math.max(nextTop, minTop), maxTop)
}

const onMouseUp = () => {
  if (!dragging.value) return
  dragging.value = false
  window.removeEventListener('mousemove', onMouseMove)
  window.removeEventListener('mouseup', onMouseUp)
}

onMounted(() => {
  const el = wrapperRef.value
  const vh = window.innerHeight
  const initial = vh - 120
  chatButtonTop.value = Math.min(Math.max(initial, 10), vh - (el ? el.offsetHeight : 60) - 10)
})

onBeforeUnmount(() => {
  window.removeEventListener('mousemove', onMouseMove)
  window.removeEventListener('mouseup', onMouseUp)
})
</script>

<style scoped>
.chat-button-wrapper {
  position: fixed;
  right: 20px;
  cursor: pointer;
  z-index: 10000;
}

.chat-trigger-button {
  width: 60px;
  height: 60px;
  font-size: 24px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}

.chat-trigger-button:hover {
  transform: scale(1.1) rotate(10deg);
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.2);
}

.chat-badge :deep(.el-badge__content) {
  position: absolute;
  top: -8px;
  right: -8px;
  font-size: 12px;
  height: 20px;
  line-height: 20px;
  min-width: 20px;
  border: 2px solid #fff;
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
}

.chat-tooltip {
  position: absolute;
  right: 70px;
  top: 50%;
  transform: translateY(-50%);
  background: rgba(0, 0, 0, 0.8);
  color: white;
  padding: 6px 12px;
  border-radius: 4px;
  font-size: 12px;
  white-space: nowrap;
  opacity: 0;
  transition: opacity 0.3s;
  pointer-events: none;
}

.chat-button-wrapper:hover .chat-tooltip {
  opacity: 1;
}

.chat-tooltip::after {
  content: '';
  position: absolute;
  right: -5px;
  top: 50%;
  transform: translateY(-50%);
  border-width: 5px 0 5px 5px;
  border-style: solid;
  border-color: transparent transparent transparent rgba(0, 0, 0, 0.8);
}
</style>
