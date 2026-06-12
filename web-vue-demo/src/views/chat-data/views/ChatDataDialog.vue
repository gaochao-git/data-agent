<script setup lang="ts">
/**
 * 智能问数弹窗组件
 * 可被外部组件导入，一行代码即可嵌入完整智能问数能力
 *
 * 用法：
 *   <ChatDataDialog v-model:visible="show" />
 *   <ChatDataDialog v-model:visible="show" :initial-width="1200" :initial-height="760" />
 */
import { computed } from 'vue'
import ChatDataPage from './index.vue'
import type { ChatDataOptions } from '../types'

interface Props {
  visible: boolean
  /** 弹窗初始宽度 */
  initialWidth?: string | number
  /** 弹窗初始高度 */
  initialHeight?: string | number
  /** 是否默认全屏 */
  fullscreen?: boolean
  /** 配置参数 */
  options?: ChatDataOptions
}

const props = withDefaults(defineProps<Props>(), {
  initialWidth: '90%',
  initialHeight: '80vh',
  fullscreen: false,
})

const emit = defineEmits<{
  'update:visible': [val: boolean]
}>()

const dialogVisible = computed({
  get: () => props.visible,
  set: (val) => emit('update:visible', val),
})

const normalizeSize = (value: string | number) => {
  if (typeof value === 'number') return `${value}px`
  return value
}
</script>

<template>
  <el-dialog
    v-model="dialogVisible"
    :width="normalizeSize(initialWidth)"
    :fullscreen="fullscreen"
    :show-close="false"
    destroy-on-close
    append-to-body
    class="chatdata-dialog"
  >
    <template #header>
      <div class="flex items-center justify-between">
        <h3 class="text-sm font-medium text-gray-800 m-0 flex items-center gap-2">
          <i class="fa-solid fa-comments text-blue-500"></i>
          智能问数
        </h3>
        <button
          class="w-7 h-7 flex items-center justify-center border-none bg-transparent rounded-md text-gray-400 cursor-pointer transition-all hover:bg-gray-100 hover:text-gray-600"
          @click="dialogVisible = false"
        >
          <i class="fa-solid fa-xmark"></i>
        </button>
      </div>
    </template>

    <div class="chatdata-dialog-body" :style="{ height: fullscreen ? '100%' : normalizeSize(initialHeight) }">
      <ChatDataPage :options="options" />
    </div>
  </el-dialog>
</template>

<style>
.chatdata-dialog .el-dialog__body {
  padding: 0;
}
.chatdata-dialog-body {
  overflow: hidden;
}
.chatdata-dialog-body .chatdata-root {
  height: 100% !important;
}
.chatdata-dialog-body .chatdata-root > .flex.overflow-hidden {
  height: calc(100% - 48px) !important;
}
</style>
