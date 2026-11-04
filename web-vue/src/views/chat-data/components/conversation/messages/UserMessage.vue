<script setup lang="ts">
/**
 * 用户消息：划选引用 chip、@ 工作区文件 chip、附件卡片、正文气泡与时间戳。
 * 数据派生统一走 utils/messageView（与历史回放同口径）。
 */
import type { PropType } from 'vue'
import { computed } from 'vue'
import { ElPopover } from 'element-plus'
import type { Message, MessageAttachment } from '../../../types'
import {
  formatFileSize,
  getEffectiveAttachments,
  getUserMentionedFiles,
  getUserQuotedSnippets,
  isLongUserMessage,
  stripAttachmentPlaceholders,
  USER_MESSAGE_SCROLL_MAX_HEIGHT,
} from '../../../utils/messageView'
import { fileIconNameByExtension } from '../../../utils/fileTypeIcon'
import { formatDateTimeValue } from '../../../utils'

const props = defineProps({
  message: { type: Object as PropType<Message>, required: true },
})

const emit = defineEmits<{
  openAttachment: [attachment: MessageAttachment]
}>()

const quotedSnippets = computed(() => getUserQuotedSnippets(props.message))
const mentionedFiles = computed(() => getUserMentionedFiles(props.message))
const attachments = computed(() => getEffectiveAttachments(props.message))
const displayContent = computed(() => stripAttachmentPlaceholders(props.message.content))
const longMessage = computed(() => isLongUserMessage(displayContent.value))

const handleAttachmentClick = (attachment: MessageAttachment) => {
  emit('openAttachment', attachment)
}

// @ 引用 chip 点击：复用附件打开链路（presented artifact 同款），在会话工作区面板
// 定位并预览该文件。file_id 用 mention: 前缀避免与真实附件 id 混淆。
const handleMentionFileClick = (file: { path: string; name: string }) => {
  emit('openAttachment', {
    file_id: `mention:${file.path}`,
    filename: file.name,
    file_type: file.name.includes('.') ? file.name.split('.').pop()!.toLowerCase() : undefined,
    original_path: file.path,
  })
}

const getFileIconClass = (fileType?: string): string => fileIconNameByExtension(fileType)
</script>

<template>
  <div
    v-if="quotedSnippets.length"
      class="flex w-full flex-row-reverse flex-wrap items-start gap-1.5"
      title="本轮引用的助手回复片段"
    >
      <ElPopover
        v-for="snippet in quotedSnippets"
        :key="snippet.id || snippet.text"
        placement="top-end"
        :width="380"
        trigger="hover"
      >
        <template #reference>
          <span class="flex max-w-[280px] items-center gap-1.5 rounded-full border border-slate-200/70 bg-white/80 px-2.5 py-1 text-xs text-slate-500">
            <i class="fa-solid fa-quote-left text-[11px] shrink-0 text-slate-400"></i>
            <span class="truncate">{{ snippet.text }}</span>
          </span>
        </template>
        <div class="max-h-[240px] overflow-y-auto whitespace-pre-wrap text-xs leading-relaxed text-slate-700">{{ snippet.text }}</div>
      </ElPopover>
    </div>
    <div
      v-if="mentionedFiles.length"
      class="flex w-full flex-row-reverse flex-wrap gap-1.5"
      title="本轮引用的工作区文件"
    >
      <button
        v-for="file in mentionedFiles"
        :key="file.path"
        type="button"
        class="flex items-center gap-1.5 rounded-full border border-[#1677FF]/20 bg-white/80 px-2.5 py-1 text-xs text-[#1F3A56] transition-colors duration-150 hover:border-[#1677FF]/40 hover:bg-white"
        :title="file.path"
        @click="handleMentionFileClick(file)"
      >
        <i class="fa-solid fa-at text-[11px] text-[#1677FF]"></i>
        <span class="max-w-[200px] truncate">{{ file.name }}</span>
      </button>
    </div>
    <div v-if="attachments.length" class="flex w-full flex-col gap-1.5">
      <button
        v-for="attachment in attachments"
        :key="attachment.file_id"
        type="button"
        class="flex items-center gap-2 rounded-lg border border-[#1677FF]/20 bg-white/80 px-2.5 py-1.5 text-left text-xs text-[#1F3A56] transition-colors duration-150 hover:bg-white hover:border-[#1677FF]/40"
        @click="handleAttachmentClick(attachment)"
      >
        <i :class="['text-[13px] text-[#1677FF]', getFileIconClass(attachment.file_type)]"></i>
        <span class="min-w-0 flex-1 truncate">{{ attachment.filename }}</span>
        <span v-if="formatFileSize(attachment.file_size)" class="shrink-0 text-[10px] text-gray-400">{{ formatFileSize(attachment.file_size) }}</span>
        <i class="fa-solid fa-chevron-right text-[9px] text-gray-400"></i>
      </button>
    </div>
    <div
      class="rounded-2xl rounded-br-md bg-[#1677FF]/10 px-4 py-2.5 text-sm leading-relaxed text-[#1F3A56] shadow-[0_2px_8px_rgba(59,130,246,0.12)]"
      :style="longMessage ? { maxHeight: USER_MESSAGE_SCROLL_MAX_HEIGHT, overflowY: 'auto' } : undefined"
    >
      <div class="whitespace-pre-wrap">{{ displayContent }}</div>
    </div>
    <div class="mr-1 text-[10px] text-gray-400">{{ formatDateTimeValue(message.timestamp) }}</div>
</template>
