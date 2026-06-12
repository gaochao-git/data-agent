<template>
  <div class="flex justify-center">
    <div class="w-full max-w-2xl">
      <div
        @dragenter="handleDragEnter"
        @dragleave="handleDragLeave"
        @dragover="handleDragOver"
        @drop="handleDrop"
        :class="[
          'relative rounded-2xl border-2 border-dashed p-12 text-center transition-all',
          isDragging
            ? 'border-indigo-500 bg-indigo-50/50 dark:border-indigo-400 dark:bg-indigo-900/20'
            : 'border-gray-300 bg-white dark:border-gray-600 dark:bg-gray-800/50',
          isUploading && 'pointer-events-none opacity-60'
        ]"
      >
        <transition name="fade" mode="out-in">
          <!-- 上传中状态 -->
          <div
            v-if="isUploading"
            key="uploading"
            class="flex flex-col items-center gap-4"
          >
            <div class="flex h-16 w-16 items-center justify-center rounded-full bg-indigo-100 dark:bg-indigo-900/40">
              <i class="fa-solid fa-spinner fa-spin text-2xl text-indigo-600 dark:text-indigo-400"></i>
            </div>
            <p class="text-sm font-medium text-gray-700 dark:text-gray-300">
              正在处理文件...
            </p>
          </div>

          <!-- 准备上传状态 -->
          <div v-else key="ready" class="flex flex-col items-center gap-4">
            <div class="flex h-16 w-16 items-center justify-center rounded-full bg-indigo-100 dark:bg-indigo-900/40">
              <i class="fa-solid fa-cloud-arrow-up text-2xl text-indigo-600 dark:text-indigo-400"></i>
            </div>

            <div class="space-y-2">
              <p class="text-sm font-medium text-gray-700 dark:text-gray-300">
                拖拽文件到此处，或点击选择文件
              </p>
              <p class="text-xs text-gray-500 dark:text-gray-400">
                支持 PDF、Word、TXT 格式
              </p>
            </div>

            <label class="cursor-pointer">
              <input
                type="file"
                :accept="accept"
                @change="handleFileInputChange"
                class="hidden"
                :disabled="isUploading"
              />
              <span class="inline-flex items-center gap-2 rounded-xl bg-indigo-600 px-6 py-2.5 text-sm font-medium text-white shadow-sm transition-all hover:bg-indigo-700 dark:bg-indigo-500 dark:hover:bg-indigo-600">
                <i class="fa-solid fa-folder-open"></i>
                选择文件
              </span>
            </label>
          </div>
        </transition>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'

interface Props {
  onFileSelect: (file: File) => void
  isUploading?: boolean
  accept?: string
}

const props = withDefaults(defineProps<Props>(), {
  isUploading: false,
  accept: '.pdf,.doc,.docx,.txt'
})

const isDragging = ref(false)

const handleDragEnter = (e: DragEvent) => {
  e.preventDefault()
  e.stopPropagation()
  isDragging.value = true
}

const handleDragLeave = (e: DragEvent) => {
  e.preventDefault()
  e.stopPropagation()
  isDragging.value = false
}

const handleDragOver = (e: DragEvent) => {
  e.preventDefault()
  e.stopPropagation()
}

const handleDrop = (e: DragEvent) => {
  e.preventDefault()
  e.stopPropagation()
  isDragging.value = false

  const files = e.dataTransfer?.files
  if (files && files.length > 0) {
    props.onFileSelect(files[0])
  }
}

const handleFileInputChange = (e: Event) => {
  const target = e.target as HTMLInputElement
  const files = target.files
  if (files && files.length > 0) {
    props.onFileSelect(files[0])
  }
}
</script>

<style scoped>
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.3s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
