<!--
  文件卡片组件
  显示已上传的文件，支持图片预览
-->

<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'

interface Props {
  file: {
    file_id: string
    original_filename: string
    file_type: string
    file_size: number
    status?: string
  }
  onRemove?: () => void
}

const props = withDefaults(defineProps<Props>(), {
  onRemove: () => {},
})

// 状态
const previewUrl = ref('')

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
function isImage(file: any): boolean {
  const extension = getFileExtension(file.original_filename || file.name)
  const imageExtensions = ['png', 'jpg', 'jpeg', 'gif', 'webp']
  return imageExtensions.includes(extension) || (file.type && file.type.startsWith('image/'))
}

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
 * 获取文件图标
 */
function getFileIconClass(): string {
  const extension = getFileExtension(props.file.original_filename)
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

/**
 * 获取状态文本
 */
function getStatusText(): string {
  const status = props.file.status
  if (status === 'uploading') return '上传中'
  if (status === 'parsing') return '解析中'
  if (status === 'completed') return '完成'
  if (status === 'failed') return '失败'
  return ''
}

/**
 * 状态指示器颜色
 */
function getStatusColor(): string {
  const status = props.file.status
  if (status === 'uploading' || status === 'parsing') return 'text-blue-500'
  if (status === 'completed') return 'text-green-500'
  if (status === 'failed') return 'text-red-500'
  return ''
}

// 组件挂载时生成图片预览URL
onMounted(() => {
  if (isImage(props.file) && props.file instanceof File) {
    previewUrl.value = URL.createObjectURL(props.file)
  }
})

// 组件卸载时释放URL
onUnmounted(() => {
  if (previewUrl.value) {
    URL.revokeObjectURL(previewUrl.value)
  }
})
</script>

<template>
  <div class="relative h-[76px] w-[210px] rounded-[12px] border border-[#E2E6EA] bg-white shadow-[0_1px_2px_rgba(0,0,0,0.06)]">
    <!-- 移除按钮 -->
    <button
      v-if="onRemove"
      type="button"
      class="absolute -right-2 -top-2 inline-flex h-5 w-5 items-center justify-center rounded-full bg-[#C2CAD4] text-white shadow hover:bg-[#A8B0B9] transition-colors"
      @click="onRemove"
      aria-label="移除附件"
    >
      <i class="fa-solid fa-xmark text-[10px]"></i>
    </button>

    <!-- 图片预览 -->
    <img
      v-if="previewUrl"
      :src="previewUrl"
      :alt="file.original_filename"
      class="h-full w-full rounded-[12px] object-cover"
    />

    <!-- 文件信息显示 -->
    <div v-else class="flex h-full items-center gap-2 px-3">
      <div class="flex h-9 w-9 items-center justify-center rounded-[8px] bg-[#F6F8FA]">
        <i :class="`fa-solid ${getFileIconClass()} text-lg`"></i>
      </div>
      <div class="min-w-0 flex-1">
        <div
          class="truncate text-[13px] font-[500] text-[#1F3A56]"
          :title="file.original_filename"
        >
          {{ file.original_filename }}
        </div>
        <div class="flex items-center gap-1 text-[12px] text-[#8A93A3]">
          <span>{{ (getFileExtension(file.original_filename) || '文件').toUpperCase() }}</span>
          <span>, {{ formatFileSize(file.file_size) }}</span>
          <span v-if="file.status" :class="getStatusColor()">
            {{ getStatusText() }}
          </span>
        </div>
      </div>
    </div>
  </div>
</template>
