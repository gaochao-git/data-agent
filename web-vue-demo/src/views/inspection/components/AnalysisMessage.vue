<template>
  <div class="bg-white dark:bg-gray-800 rounded-2xl rounded-tl-sm px-4 py-3 shadow-sm">
    <!-- 如果是 analysis_data，显示结构化分析结果 -->
    <div v-if="analysisData" class="pb-2">
      <AnalysisResult
        :analysis-result="analysisData"
        :is-completed="isCompleted"
        @law-click="(law) => emit('law-click', law)"
        @report-click="(report) => emit('report-click', report)"
      />
    </div>

    <!-- 任务进度显示 (Human-in-the-Loop) -->
    <div v-if="taskProgress && taskProgress.length > 0 && !analysisData" class="task-progress mb-3 space-y-2">
      <div
        v-for="task in taskProgress"
        :key="task.id"
        class="flex items-center gap-2 px-3 py-2 text-sm transition-colors rounded-lg cursor-pointer"
        :class="[
          getTaskBackgroundClass(task.status),
          task.status === 'awaiting_confirm' ? 'hover:bg-amber-100 dark:hover:bg-amber-900/40' : ''
        ]"
        @click="handleTaskClick(task)"
      >
        <!-- 状态图标 -->
        <div class="flex-shrink-0">
          <!-- 已完成 -->
          <div
            v-if="task.status === 'completed'"
            class="flex h-5 w-5 items-center justify-center rounded-full bg-emerald-500 text-white"
          >
            <i class="fas fa-check text-xs"></i>
          </div>
          <!-- 进行中 -->
          <div v-else-if="task.status === 'in_progress'" class="h-5 w-5 flex items-center justify-center">
            <i class="fas fa-circle-notch fa-spin text-indigo-500 text-xs"></i>
          </div>
          <!-- 待处理 -->
          <div
            v-else-if="task.status === 'pending'"
            class="h-5 w-5 rounded-full border-2 border-gray-300 dark:border-gray-600"
          ></div>
          <!-- 已取消 -->
          <div
            v-else-if="task.status === 'cancelled'"
            class="flex h-5 w-5 items-center justify-center rounded-full bg-gray-500 text-white"
          >
            <i class="fas fa-ban text-xs"></i>
          </div>
          <!-- 错误 -->
          <div
            v-else-if="task.status === 'error'"
            class="flex h-5 w-5 items-center justify-center rounded-full bg-rose-500 text-white"
          >
            <i class="fas fa-xmark text-xs"></i>
          </div>
          <!-- 等待确认 -->
          <div
            v-else-if="task.status === 'awaiting_confirm'"
            class="flex h-5 w-5 items-center justify-center rounded-full bg-amber-500 text-white"
          >
            <i class="fas fa-user-check text-xs"></i>
          </div>
        </div>

        <!-- 任务标题 -->
        <div class="flex-1 font-medium text-gray-900 dark:text-gray-100">
          {{ task.title }}
        </div>

        <!-- 待确认标记 -->
        <div v-if="task.status === 'awaiting_confirm'" class="text-xs text-amber-600 dark:text-amber-400 font-medium">
          待确认
        </div>
      </div>
    </div>

    <!-- 文本内容 -->
    <div v-if="textContent" class="text-sm leading-relaxed text-gray-800 dark:text-gray-200">
      <div class="whitespace-pre-wrap">
        {{ textContentWithoutButton }}
      </div>
    </div>

    <!-- 法律条款 -->
    <div v-if="lawBlocks.length > 0" class="law-blocks space-y-2 mt-3">
      <div
        v-for="(law, index) in lawBlocks"
        :key="'law-' + index"
        class="law-block p-3 bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 rounded-lg cursor-pointer hover:bg-amber-100 dark:hover:bg-amber-900/30 transition"
        @click="$emit('law-click', law)"
      >
        <div class="text-sm font-medium text-amber-900 dark:text-amber-100">
          {{ law.law_name }}
        </div>
        <div class="text-xs text-amber-700 dark:text-amber-300 mt-1">
          {{ law.law_item_name }}
        </div>
      </div>
    </div>

    <!-- Word 文档 (使用 DocumentCard 样式) -->
    <div v-if="wordDocument" class="border-t border-dashed border-gray-300 pt-2 dark:border-gray-600 mt-3">
      <div class="flex items-center gap-2">
        <div class="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded bg-blue-500 text-white">
          <i class="fas fa-file-word text-xs"></i>
        </div>
        <div class="flex-1">
          <div class="text-sm font-medium text-gray-900 dark:text-gray-100">
            {{ wordDocument.title }}
          </div>
        </div>
        <div class="flex gap-2">
          <button
            @click="$emit('word-document-view', wordDocument)"
            class="rounded bg-white border border-gray-300 px-2 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50 dark:bg-gray-800 dark:border-gray-600 dark:text-gray-200 dark:hover:bg-gray-700"
          >
            <i class="fa-solid fa-eye mr-1"></i>
            预览
          </button>
          <a
            :href="`${API_BASE}/api/conversations/${threadId}/files/${wordDocument.filename}`"
            download
            class="inline-flex items-center rounded bg-white border border-gray-300 px-2 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50 dark:bg-gray-800 dark:border-gray-600 dark:text-gray-200 dark:hover:bg-gray-700"
          >
            <i class="fa-solid fa-download mr-1"></i>
            下载
          </a>
        </div>
      </div>
    </div>

    <!-- Excel 文档 (使用 DocumentCard 样式) -->
    <div v-if="excelDocument" class="border-t border-dashed border-gray-300 pt-2 dark:border-gray-600 mt-3">
      <div class="flex items-center gap-2">
        <div class="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded bg-green-500 text-white">
          <i class="fas fa-file-excel text-xs"></i>
        </div>
        <div class="flex-1">
          <div class="text-sm font-medium text-gray-900 dark:text-gray-100">
            {{ excelDocument.title }}
          </div>
        </div>
        <div class="flex gap-2">
          <!-- 预览按钮 (如果有 excelData) -->
          <button
            v-if="excelDocument.excelData"
            @click="$emit('excel-document-view', excelDocument)"
            class="rounded bg-white border border-gray-300 px-2 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50 dark:bg-gray-800 dark:border-gray-600 dark:text-gray-200 dark:hover:bg-gray-700"
          >
            <i class="fa-solid fa-eye mr-1"></i>
            预览
          </button>
          <a
            :href="`${API_BASE}/api/conversations/${threadId}/files/${excelDocument.filename}`"
            download
            class="inline-flex items-center rounded bg-white border border-gray-300 px-2 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50 dark:bg-gray-800 dark:border-gray-600 dark:text-gray-200 dark:hover:bg-gray-700"
          >
            <i class="fa-solid fa-download mr-1"></i>
            下载
          </a>
        </div>
      </div>
    </div>

    <!-- 报告 -->
    <div v-if="report" class="border-t border-dashed border-gray-300 pt-2 dark:border-gray-600 mt-3">
      <div class="flex items-center gap-2">
        <div class="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded bg-purple-500 text-white">
          <i class="fas fa-file-lines text-xs"></i>
        </div>
        <div class="flex-1">
          <div class="text-sm font-medium text-gray-900 dark:text-gray-100">
            {{ report.name }}
          </div>
        </div>
        <div class="flex gap-2">
          <button
            @click="$emit('report-click', report)"
            class="rounded bg-white border border-gray-300 px-2 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50 dark:bg-gray-800 dark:border-gray-600 dark:text-gray-200 dark:hover:bg-gray-700"
          >
            <i class="fa-solid fa-eye mr-1"></i>
            预览
          </button>
        </div>
      </div>
    </div>

    <!-- 操作按钮 -->
    <div v-if="isChatStreaming && isStreamingMessage" class="mt-2 flex gap-2">
      <button
        @click="$emit('refresh-message', message.id)"
        class="p-1.5 text-gray-400 hover:text-indigo-500 hover:bg-indigo-50 dark:hover:bg-indigo-900/20 rounded transition-colors"
        title="刷新"
      >
        <i class="fas fa-rotate"></i>
      </button>
      <button
        @click="$emit('copy-message', textContent)"
        class="p-1.5 text-gray-400 hover:text-indigo-500 hover:bg-indigo-50 dark:hover:bg-indigo-900/20 rounded transition-colors"
        title="复制"
      >
        <i class="fas fa-copy"></i>
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { ChatMessage, ContentBlock, AnalysisTask } from '../types'
import AnalysisResult from './AnalysisResult.vue'

interface Props {
  message: ChatMessage
  threadId: string
  isChatStreaming: boolean
  activeAssistantMessageId: string | null
}

const props = defineProps<Props>()

const emit = defineEmits<{
  (e: 'law-click', law: any): void
  (e: 'word-document-view', doc: any): void
  (e: 'excel-document-view', doc: any): void
  (e: 'report-click', report: any): void
  (e: 'refresh-message', messageId: string): void
  (e: 'copy-message', content: string): void
  (e: 'task-click', task: AnalysisTask): void
}>()

const API_BASE = import.meta.env.VITE_APP_BASE_API || ''

// 提取 analysis_data 块
const analysisData = computed((): AnalysisResult | null => {
  const content = props.message.content
  if (Array.isArray(content)) {
    console.log('[AnalysisMessage] content 数组，长度:', content.length)
    console.log('[AnalysisMessage] content:', content)

    const dataBlock = content.find((block: ContentBlock) =>
      (block as any).type === 'non_standard' && (block as any).value?.type === 'analysis_data'
    ) as any
    console.log('[AnalysisMessage] 找到的 dataBlock:', dataBlock)

    if (dataBlock) {
      const result = dataBlock.value?.data || null
      console.log('[AnalysisMessage] 提取的 analysisData:', result)
      return result
    }
  }
  console.log('[AnalysisMessage] 没有找到 analysis_data 块')
  return null
})

// 提取任务进度 (task_progress)
const taskProgress = computed((): AnalysisTask[] => {
  const content = props.message.content
  if (Array.isArray(content)) {
    const taskBlock = content.find((block: ContentBlock) =>
      (block as any).type === 'non_standard' && (block as any).value?.type === 'task_progress'
    ) as any
    if (taskBlock?.value?.data) {
      return taskBlock.value.data
    }
  }
  return []
})

// 检查是否已完成（通过任务状态判断）
const isCompleted = computed(() => {
  // 如果有 analysisData，说明数据已返回，可以认为是完成状态
  if (analysisData.value) return true
  // 否则通过任务进度判断
  if (!taskProgress.value || taskProgress.value.length === 0) return false
  return taskProgress.value.every(task => task.status === 'completed')
})

// 获取任务背景样式
function getTaskBackgroundClass(status: string): string {
  if (status === 'awaiting_confirm') {
    return 'bg-amber-50 dark:bg-amber-900/20'
  }
  return 'bg-gray-50 dark:bg-gray-800/50'
}

// 处理任务点击
function handleTaskClick(task: AnalysisTask) {
  if (task.status === 'awaiting_confirm') {
    emit('task-click', task)
  }
}

// 提取文本内容
const textContent = computed(() => {
  const content = props.message.content
  if (typeof content === 'string') {
    return content
  }
  if (Array.isArray(content)) {
    return content
      .filter((block: ContentBlock) => block.type === 'text')
      .map((block: any) => block.text || '')
      .join('\n')
  }
  return ''
})

// 提取法律条款
const lawBlocks = computed(() => {
  const content = props.message.content
  if (Array.isArray(content)) {
    return content
      .filter((block: ContentBlock) => block.type === 'non_standard' && (block as any).value?.type === 'law_data')
      .map((block: any) => (block.value?.data as any) || null)
      .filter(Boolean)
  }
  return []
})

// 提取 Word 文档
const wordDocument = computed(() => {
  const content = props.message.content
  if (Array.isArray(content)) {
    // 先检查是否有 Excel 数据，如果有则不返回 Word 文档
    const hasExcel = content.some((block: ContentBlock) =>
      (block as any).type === 'non_standard' && (block as any).value?.type === 'excel_data'
    )
    if (hasExcel) return null

    const fileBlock = content.find((block: ContentBlock) => block.type === 'file') as any
    if (fileBlock?.file_id) {
      const titleBlock = content.find((block: ContentBlock) =>
        (block as any).type === 'non_standard' && (block as any).value?.type === 'file_title'
      ) as any
      return {
        filename: fileBlock.file_id,
        title: titleBlock?.value?.data || '文档'
      }
    }
  }
  return null
})

// 提取 Excel 文档
const excelDocument = computed(() => {
  const content = props.message.content
  if (Array.isArray(content)) {
    const excelBlock = content.find((block: ContentBlock) =>
      (block as any).type === 'non_standard' && (block as any).value?.type === 'excel_data'
    ) as any
    const titleBlock = content.find((block: ContentBlock) =>
      (block as any).type === 'non_standard' && (block as any).value?.type === 'file_title'
    ) as any
    const fileBlock = content.find((block: ContentBlock) => block.type === 'file') as any
    if (excelBlock && titleBlock) {
      return {
        filename: fileBlock?.file_id || 'excel.xlsx',
        title: titleBlock.value?.data || 'Excel文档',
        excelData: excelBlock.value?.data
      }
    }
  }
  return null
})

// 提取报告
const report = computed(() => {
  const content = props.message.content
  if (Array.isArray(content)) {
    const reportBlock = content.find((block: ContentBlock) =>
      (block as any).type === 'non_standard' && (block as any).value?.type === 'report_data'
    ) as any
    if (reportBlock) {
      return reportBlock.value?.data || null
    }
  }
  return null
})

// 去除下载按钮占位符后的文本内容
const textContentWithoutButton = computed(() => {
  return textContent.value.replace(/\[DOWNLOAD_BUTTON:.+?\]/g, '').trim()
})

// 检查是否是正在流式传输的消息
const isStreamingMessage = computed(() => {
  return props.message.id === props.activeAssistantMessageId
})
</script>
