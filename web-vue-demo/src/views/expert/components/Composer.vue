<!--
  输入框组件
  迁移自: web/src/pages/GeneralAssistant/components/unified/conversation/Composer.tsx

  功能：
  - 文本输入
  - 文件上传
  - 引用块显示
  -.操作按钮（深度思考等）
  - 建议（suggestions）
-->

<script setup lang="ts">
import { ref, computed, withDefaults, defineEmits, defineExpose } from 'vue'
// 导入图片资源
import uploadIcon from '@/assets/images/upload.png'
import sendIcon from '@/assets/images/send.png'
import sendOkIcon from '@/assets/images/send-ok.png'
import stopOkIcon from '@/assets/images/stop-ok.png'
import deepThinkIcon from '@/assets/images/deep-think.svg'

interface InputAction {
  id: string
  label: string
  icon?: string
  defaultSelected?: boolean
  kind?: string
}

interface ReferenceBlock {
  id: string
  text: string
}

interface Props {
  footerTagText: string
  inputValue: string
  placeholderText?: string
  selectedFiles?: File[]
  referenceBlocks?: ReferenceBlock[]
  inputActions?: InputAction[]
  selectedActionIds?: Set<string>
  showUpload?: boolean
  uploadAccept?: string
  isSending?: boolean
  canSend?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  placeholderText: '请输入您的问题...',
  selectedFiles: () => [],
  referenceBlocks: () => [],
  inputActions: () => [],
  selectedActionIds: () => new Set(),
  showUpload: true,
  uploadAccept: '.pdf,.doc,.docx,.xls,.xlsx,.txt',
  isSending: false,
  canSend: false,
})

const emit = defineEmits<{
  (e: 'update:inputValue', value: string): void
  (e: 'inputKeyDown', event: KeyboardEvent): void
  (e: 'send'): void
  (e: 'stop'): void
  (e: 'removeFile', index: number): void
  (e: 'removeReferenceBlock', id: string): void
  (e: 'toggleAction', actionId: string): void
  (e: 'fileChange', event: Event): void
}>()

// Refs
const textareaRef = ref<HTMLTextAreaElement | null>(null)
const fileInputRef = ref<HTMLInputElement | null>(null)

// Computed: 获取发送/停止按钮图片
const sendButtonSrc = computed(() => {
  if (props.isSending) return stopOkIcon
  if (props.canSend) return sendOkIcon
  return sendIcon
})

/**
 * 格式化文件大小
 */
function formatFileSize(size: number): string {
  if (!Number.isFinite(size) || size <= 0) return '0KB'
  const units = ['B', 'KB', 'MB', 'GB']
  let value = size
  let unitIndex = 0
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024
    unitIndex += 1
  }
  const digits = value >= 100 || unitIndex === 0 ? 0 : 1
  return `${value.toFixed(digits)}${units[unitIndex]}`
}

/**
 * 获取文件扩展名
 */
function getFileExtension(name: string): string {
  const parts = name.split('.')
  return parts.length > 1 ? parts[parts.length - 1].toLowerCase() : ''
}

/**
 * 判断是否为图片
 */
function isImageFile(file: File): boolean {
  const extension = getFileExtension(file.name)
  const imageExtensions = ['png', 'jpg', 'jpeg', 'gif', 'webp']
  return imageExtensions.includes(extension) || file.type.startsWith('image/')
}

/**
 * 自动调整文本框高度
 */
function autoResizeTextarea() {
  const el = textareaRef.value
  if (!el) return
  el.style.height = 'auto'
  const newHeight = Math.min(el.scrollHeight, 200) // 最大高度200px
  el.style.height = `${newHeight}px`
}

/**
 * 处理输入
 */
function handleInput(event: Event) {
  const target = event.target as HTMLTextAreaElement
  emit('update:inputValue', target.value)
  autoResizeTextarea()
}

/**
 * 处理键盘事件
 */
function handleKeyDown(event: KeyboardEvent) {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    if (props.canSend && !props.isSending) {
      emit('send')
    }
  }
  emit('inputKeyDown', event)
}

/**
 * 处理发送/停止
 */
function handleSendOrStop() {
  if (props.isSending) {
    emit('stop')
  } else if (props.canSend) {
    emit('send')
  }
}

/**
 * 处理文件上传按钮点击
 */
function handleUploadClick() {
  fileInputRef.value?.click()
}

/**
 * 处理文件选择
 */
function handleFileChange(event: Event) {
  emit('fileChange', event)
}

/**
 * 处理移除文件
 */
function handleRemoveFile(index: number) {
  emit('removeFile', index)
}

/**
 * 处理移除引用块
 */
function handleRemoveReferenceBlock(id: string) {
  emit('removeReferenceBlock', id)
}

/**
 * 处理操作按钮点击
 */
function handleToggleAction(actionId: string) {
  const action = props.inputActions?.find(a => a.id === actionId)
  if (action && !action.defaultSelected) {
    emit('toggleAction', actionId)
  }
}

/**
 * 判断操作是否选中
 */
function isActionSelected(actionId: string): boolean {
  return props.selectedActionIds?.has(actionId) || false
}

/**
 * 判断操作是否锁定
 */
function isActionLocked(actionId: string): boolean {
  return props.inputActions?.find(a => a.id === actionId)?.defaultSelected || false
}

/**
 * 获取操作按钮样式
 */
function getActionButtonClass(actionId: string): string {
  const isSelected = isActionSelected(actionId)
  const baseClass = 'inline-flex items-center gap-1 rounded-full px-2.5 py-1 transition-colors'
  if (isSelected) {
    return `${baseClass} bg-cyan-100 dark:bg-cyan-900/50`
  }
  return `${baseClass} bg-slate-100`
}

/**
 * 获取文件图标类名
 */
function getFileIconClass(file: File): string {
  const extension = getFileExtension(file.name)
  if (extension === 'pdf') {
    return 'fa-file-pdf text-[#E65054]'
  }
  if (['doc', 'docx'].includes(extension)) {
    return 'fa-file-word text-[#2B579A]'
  }
  if (['xls', 'xlsx'].includes(extension)) {
    return 'fa-file-excel text-[#217346]'
  }
  if (['ppt', 'pptx'].includes(extension)) {
    return 'fa-file-powerpoint text-[#D04423]'
  }
  return 'fa-file-lines text-[#6B7280]'
}

// 暴露方法给父组件
defineExpose({
  textareaRef,
  focus: () => textareaRef.value?.focus(),
})
</script>

<template>
  <footer class="pt-6">
    <div
      :class="[
        'flex min-h-[120px] w-full flex-col gap-3 rounded-[20px] border border-slate-100 bg-white px-6 py-3 shadow-[0_4px_25px_1px_rgba(0,0,0,0.1)] transition-all duration-150',
        'focus-within:border-[#7BB7D4]',
        'focus-within:shadow-[0_0_0_3px_rgba(0,126,167,0.12)]'
      ]"
    >
      <!-- 引用块 -->
      <div
        v-if="referenceBlocks && referenceBlocks.length > 0"
        class="flex flex-col gap-2 text-[13px] text-[#1F3A56]"
      >
        <div
          v-for="item in referenceBlocks"
          :key="item.id"
          class="flex items-start justify-between gap-3 rounded-[12px] border border-[#E5EAF0] bg-[#F6F8FA] px-4 py-2"
        >
          <div
            class="flex-1 truncate leading-[20px]"
            :title="item.text"
          >
            {{ item.text }}
          </div>
          <button
            type="button"
            class="mt-0.5 inline-flex h-5 w-5 items-center justify-center rounded-full text-[#8592A6] hover:bg-[#EEF2F5]"
            @click="handleRemoveReferenceBlock(item.id)"
            aria-label="移除引用"
          >
            <i class="fa-solid fa-xmark text-[10px]"></i>
          </button>
        </div>
      </div>

      <!-- 输入框 -->
      <textarea
        ref="textareaRef"
        rows="3"
        :placeholder="placeholderText"
        :value="inputValue"
        @input="handleInput"
        @keydown="handleKeyDown"
        class="w-full flex-1 min-h-[40px] resize-none border-0 text-[14px] leading-[24px] text-black outline-none placeholder:text-[16px] placeholder:font-[500] placeholder:leading-[24px] placeholder:text-[#0000003F] placeholder:opacity-100"
      />

      <!-- 操作栏 -->
      <div class="flex items-center justify-between">
        <!-- 左侧：操作按钮 -->
        <div class="flex items-center gap-2 text-[14px] font-[500] leading-[22px] text-[rgba(133,146,166,1)]">
          <button
            v-for="action in inputActions"
            :key="action.id"
            type="button"
            :disabled="isActionLocked(action.id)"
            :class="[
              getActionButtonClass(action.id),
              'cursor-pointer',
              'disabled:cursor-not-allowed',
              'disabled:opacity-60'
            ]"
            @click="handleToggleAction(action.id)"
          >
            <span
              v-if="isActionSelected(action.id)"
              class="inline-flex items-center gap-1"
              style="background: linear-gradient(112deg, #007EA7 1%, #1F3A56 100%); -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text; display: flex; align-items: center; gap: 4px;"
            >
              <!-- 深度思考图标 -->
              <img
                v-if="action.icon === 'deep-think'"
                :src="deepThinkIcon"
                alt=""
                class="h-[18px] w-[18px]"
              />
              {{ action.label }}
            </span>
            <span
              v-else
              class="inline-flex items-center gap-1 text-[#6B7280]"
              style="display: flex; align-items: center; gap: 4px;"
            >
              <!-- 深度思考图标 -->
              <img
                v-if="action.icon === 'deep-think'"
                :src="deepThinkIcon"
                alt=""
                class="h-[18px] w-[18px]"
              />
              {{ action.label }}
            </span>
          </button>
        </div>

        <!-- 右侧：上传和发送按钮 -->
        <div class="flex items-center gap-3">
          <!-- 上传按钮 -->
          <button
            v-if="showUpload"
            type="button"
            class="relative h-10 w-10"
            @click="handleUploadClick"
            aria-label="上传文件"
          >
            <img
              :src="uploadIcon"
              alt="上传"
              class="absolute left-[3.84px] top-[2.73px] h-[79.46%] w-[73.36%] opacity-100"
            />
          </button>

          <!-- 隐藏的文件输入 -->
          <input
            v-if="showUpload"
            ref="fileInputRef"
            type="file"
            class="hidden"
            multiple
            :accept="uploadAccept"
            @change="handleFileChange"
          />

          <!-- 发送/停止按钮 -->
          <button
            type="button"
            class="relative flex h-10 w-10 items-center justify-center"
            :disabled="!isSending && !canSend"
            @click="handleSendOrStop"
            :aria-label="isSending ? '停止' : '发送'"
          >
            <img
              :src="sendButtonSrc"
              :alt="isSending ? '停止' : '发送'"
              class="absolute left-0 top-0 h-10 w-10 opacity-50"
            />
          </button>
        </div>
      </div>

      <!-- 已上传文件列表 -->
      <div
        v-if="selectedFiles && selectedFiles.length > 0"
        class="rounded-[16px] bg-[#F5F6F7] px-3 py-3"
      >
        <div class="flex flex-wrap items-center gap-3">
          <div
            v-for="(file, index) in selectedFiles"
            :key="`${file.name}-${index}`"
            class="relative h-[76px] w-[210px] rounded-[12px] border border-[#E2E6EA] bg-white shadow-[0_1px_2px_rgba(0,0,0,0.06)]"
          >
            <!-- 移除按钮 -->
            <button
              type="button"
              class="absolute -right-2 -top-2 inline-flex h-5 w-5 items-center justify-center rounded-full bg-[#C2CAD4] text-white shadow hover:bg-[#A8B0B9] transition-colors"
              @click="handleRemoveFile(index)"
              aria-label="移除附件"
            >
              <i class="fa-solid fa-xmark text-[10px]"></i>
            </button>

            <!-- 图片预览 -->
            <img
              v-if="isImageFile(file)"
              :src="URL.createObjectURL(file)"
              :alt="file.name"
              class="h-full w-full rounded-[12px] object-cover"
            />

            <!-- 文件信息显示 -->
            <div v-else class="flex h-full items-center gap-2 px-3">
              <div class="flex h-9 w-9 items-center justify-center rounded-[8px] bg-[#F6F8FA]">
                <i :class="`fa-solid ${getFileIconClass(file)} text-lg`"></i>
              </div>
              <div class="min-w-0">
                <div
                  class="truncate text-[13px] font-[500] text-[#1F3A56]"
                  :title="file.name"
                >
                  {{ file.name }}
                </div>
                <div class="text-[12px] text-[#8A93A3]">
                  {{ (getFileExtension(file.name) || '文件').toUpperCase() }}, {{ formatFileSize(file.size) }}
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 底部标签 -->
    <div class="flex h-[30px] items-center justify-center gap-4 text-[14px] text-slate-400">
      <span class="text-[rgba(0,126,167,1)]">{{ footerTagText }}</span>
      <span>内容由AI生成，请仔细甄别</span>
    </div>
  </footer>
</template>
