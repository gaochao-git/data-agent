<template>
  <!-- AttachmentViewer 简化版骨架 - 待完善完整功能 -->
  <div class="attachment-viewer">
    <div class="flex items-center justify-center h-full text-gray-400">
      <div class="text-center">
        <i class="fa-solid fa-file-lines text-4xl mb-4"></i>
        <p>附件查看器</p>
        <p class="text-sm mt-2">文件名: {{ fileName }}</p>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
interface Props {
  fileName: string
  fileId: string
  threadId: string
}

defineProps<Props>()
</script>

<style scoped>
.attachment-viewer {
  height: 100%;
  overflow: auto;
}
</style>
