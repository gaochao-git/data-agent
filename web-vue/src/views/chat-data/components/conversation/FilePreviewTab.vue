<script lang="ts">
/* eslint-disable @typescript-eslint/no-unused-vars, @typescript-eslint/no-explicit-any -- 模板内使用的子组件（components 注册）与注入作用域，静态分析无法完整识别 */
/**
 * 文件预览 tab：内容渲染 + 路径条 + 预览操作。
 * 状态仍由 ConversationWorkspacePanel 持有，通过 WorkspaceFilesContextKey 提供，
 * 这里只做「粗粒度 tab 组件」拆分，不做更细的状态重组。
 */
import { defineComponent, inject } from 'vue'
import ChartSpecRenderer from '../ChartSpecRenderer.vue'
import MarkdownFilePreview from './MarkdownFilePreview.vue'
import HtmlPagePreview from './HtmlPagePreview.vue'
import CodeFilePreview from './CodeFilePreview.vue'
import MermaidFilePreview from './MermaidFilePreview.vue'
import { formatToolTableCell } from '../../utils/messageArtifacts'
import { WorkspaceFilesContextKey } from '../../composables/workspaceFilesContext'

export default defineComponent({
  name: 'FilePreviewTab',
  components: { ChartSpecRenderer, MarkdownFilePreview, HtmlPagePreview, CodeFilePreview, MermaidFilePreview },
  setup() {
    const context = inject(WorkspaceFilesContextKey)
    if (!context) throw new Error('FilePreviewTab 必须在 ConversationWorkspacePanel 内使用')
    // 注入的是同一个面板作用域：key 由面板保证齐全，这里按原变量名暴露给模板
    return { ...context, formatToolTableCell } as Record<string, any>
  },
})
</script>

<template>
<div v-if="activePanelTab.kind === 'file' && selectedConsoleFile" class="flex min-h-0 flex-1 flex-col rounded-lg border border-slate-100 bg-slate-50 p-3">
        <div class="mb-2 flex items-center justify-between gap-2">
          <div
            ref="previewPathRef"
            class="flex min-w-0 flex-1 items-center justify-end overflow-hidden font-mono text-xs"
            :style="previewPathClipped ? { maskImage: 'linear-gradient(to right, transparent, #000 28px)' } : undefined"
            :title="selectedConsoleFile?.displayPath"
            data-preview-path
          >
            <span ref="previewPathInnerRef" class="mr-auto whitespace-nowrap">
              <span v-if="selectedConsolePathParts.directory" class="text-slate-400">{{ selectedConsolePathParts.directory }}</span>
              <span class="font-medium text-slate-600">{{ selectedConsolePathParts.name }}</span>
            </span>
          </div>
          <div class="flex shrink-0 items-center gap-2">
            <div v-if="selectedConsoleFile && (selectedConsoleChartSpec || hasSelectedConsoleTable || selectedConsoleIsMarkdown || selectedConsoleIsMermaid || selectedConsoleIsHtml)" class="flex rounded-md border border-slate-200 bg-white p-0.5">
              <button
                v-if="selectedConsoleChartSpec"
                type="button"
                :class="[
                  'h-6 rounded px-2 text-[10px] transition-colors',
                  consoleFilePreviewMode === 'render' ? 'bg-blue-50 text-blue-700' : 'text-slate-500 hover:bg-slate-50'
                ]"
                @click="consoleFilePreviewMode = 'render'"
              >
                图表
              </button>
              <button
                v-if="hasSelectedConsoleTable"
                type="button"
                :class="[
                  'h-6 rounded px-2 text-[10px] transition-colors',
                  consoleFilePreviewMode === 'table' ? 'bg-blue-50 text-blue-700' : 'text-slate-500 hover:bg-slate-50'
                ]"
                @click="consoleFilePreviewMode = 'table'"
              >
                表格
              </button>
              <button
                v-if="selectedConsoleIsMarkdown || selectedConsoleIsMermaid || selectedConsoleIsHtml"
                type="button"
                :class="[
                  'h-6 rounded px-2 text-[10px] transition-colors',
                  consoleFilePreviewMode === 'render' ? 'bg-blue-50 text-blue-700' : 'text-slate-500 hover:bg-slate-50'
                ]"
                @click="consoleFilePreviewMode = 'render'"
              >
                {{ selectedConsoleIsMermaid ? '图' : '预览' }}
              </button>
              <button
                type="button"
                :class="[
                  'h-6 rounded px-2 text-[10px] transition-colors',
                  consoleFilePreviewMode === 'source' ? 'bg-blue-50 text-blue-700' : 'text-slate-500 hover:bg-slate-50'
                ]"
                @click="consoleFilePreviewMode = 'source'"
              >
                源码
              </button>
            </div>
            <!-- 复制内容/路径：内容口径=预览所见（表格模式=所见表格 TSV，文本类=全文，超大文件受预览截断）。
                 路径=/mnt/user-data 沙箱可见路径（与 @ 引用、智能体视角一致）。 -->
            <button
              v-if="selectedConsoleFile && !selectedConsoleIsPdf"
              type="button"
              class="flex h-6 w-6 items-center justify-center rounded border border-slate-200 bg-white text-[11px] text-slate-500 transition-colors hover:bg-slate-50 hover:text-slate-700"
              title="复制文件内容（表格模式复制 TSV）"
              data-console-preview-copy-content="true"
              @click="handleCopyWorkspacePreviewContent"
            >
              <i class="fa-regular fa-copy"></i>
            </button>
            <button
              v-if="selectedConsoleFile && !selectedConsoleIsPdf"
              type="button"
              class="flex h-6 w-6 items-center justify-center rounded border border-slate-200 bg-white text-[11px] text-slate-500 transition-colors hover:bg-slate-50 hover:text-slate-700"
              title="复制文件路径"
              data-console-preview-copy-path="true"
              @click="handleCopyWorkspacePreviewPath"
            >
              <i class="fa-solid fa-link"></i>
            </button>
            <!-- 下载已下沉到文件树每行右端（和文件名同处一行，不必先选中再来这里找）。
                 这里改为刷新：tool_result / run 结束本就会自动刷新，此按钮是手动兜底。 -->
            <button
              v-if="selectedConsoleFile && !selectedConsoleIsPdf"
              type="button"
              class="flex h-6 w-6 items-center justify-center rounded border border-slate-200 bg-white text-[11px] text-slate-500 transition-colors hover:bg-slate-50 hover:text-slate-700 disabled:opacity-50"
              :title="isWorkspaceRefreshBusy ? '正在刷新' : '刷新文件列表与预览内容'"
              :disabled="isWorkspaceRefreshBusy"
              data-console-files-refresh="true"
              @click="refreshWorkspaceFiles"
            >
              <i class="fa-solid fa-rotate-right" :class="isWorkspaceRefreshBusy ? 'fa-spin' : ''"></i>
            </button>
            <button
              v-if="selectedConsoleFile && !selectedConsoleIsPdf"
              type="button"
              class="flex h-6 w-6 items-center justify-center rounded border border-slate-200 bg-white text-[11px] text-slate-500 transition-colors hover:bg-slate-50 hover:text-slate-700"
              title="放大预览"
              @click="toggleWorkspaceFilePreviewFullscreen"
            >
              <i class="fa-solid fa-expand"></i>
            </button>
          </div>
        </div>
        <div
          ref="workspaceFilePreviewRef"
          class="workspace-file-preview relative bg-white"
          :class="isWorkspaceFilePreviewFullscreen
            ? 'h-screen w-screen overflow-auto p-6'
            : (activePanelTab.kind === 'file' ? 'flex min-h-0 flex-1 flex-col' : '')"
        >
          <button
            v-if="isWorkspaceFilePreviewFullscreen"
            type="button"
            class="absolute right-14 top-3 z-20 flex h-8 w-8 items-center justify-center rounded border border-slate-200 bg-white text-xs text-slate-600 shadow-sm hover:bg-slate-50"
            title="复制文件内容"
            @click="handleCopyWorkspacePreviewContent"
          >
            <i class="fa-regular fa-copy"></i>
          </button>
          <button
            v-if="isWorkspaceFilePreviewFullscreen"
            type="button"
            class="absolute right-3 top-3 z-20 flex h-8 w-8 items-center justify-center rounded border border-slate-200 bg-white text-xs text-slate-600 shadow-sm hover:bg-slate-50"
            title="退出放大预览"
            @click="toggleWorkspaceFilePreviewFullscreen"
          >
            <i class="fa-solid fa-compress"></i>
          </button>
          <ChartSpecRenderer
            v-if="selectedConsoleChartSpec && consoleFilePreviewMode === 'render'"
            :spec="selectedConsoleChartSpec"
          />
          <MarkdownFilePreview
            v-else-if="selectedConsoleIsMarkdown && consoleFilePreviewMode === 'render'"
            :content="selectedConsoleFileText"
            :class="workspaceFilePreviewHeightClass"
          />
          <MermaidFilePreview
            v-else-if="selectedConsoleIsMermaid && consoleFilePreviewMode === 'render'"
            :content="selectedConsoleFileText"
            :class="workspaceFilePreviewHeightClass"
          />
          <HtmlPagePreview
            v-else-if="selectedConsoleIsHtml && consoleFilePreviewMode === 'render'"
            :html="selectedConsoleHtmlDocument"
            :class="workspaceFilePreviewHeightClass"
          />
          <div v-else-if="hasSelectedConsoleTable && consoleFilePreviewMode === 'table'" :class="isWorkspaceFilePreviewFullscreen ? 'h-[calc(100vh-48px)] overflow-auto rounded-md border border-slate-100 bg-white' : 'min-h-0 flex-1 overflow-auto rounded-md border border-slate-100 bg-white'">
            <table class="min-w-full border-separate border-spacing-0 text-left text-xs">
            <thead class="sticky top-0 z-10 bg-slate-50">
              <tr>
                <th
                  v-for="column in selectedConsoleTableColumns"
                  :key="column"
                  class="border-b border-slate-100 px-3 py-2 font-medium text-slate-500"
                >
                  {{ column }}
                </th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(row, rowIndex) in selectedConsolePreviewRows" :key="rowIndex" class="hover:bg-slate-50">
                <td
                  v-for="column in selectedConsoleTableColumns"
                  :key="column"
                  class="max-w-[220px] truncate border-b border-slate-50 px-3 py-2 text-slate-700"
                  :title="formatToolTableCell(row[column])"
                >
                  {{ formatToolTableCell(row[column]) }}
                </td>
              </tr>
            </tbody>
          </table>
          </div>
          <CodeFilePreview
            v-else-if="selectedConsoleIsYaml && consoleFilePreviewMode === 'render'"
            :content="selectedConsoleFileText"
            :class="workspaceFilePreviewHeightClass"
          />
          <iframe
            v-else-if="selectedConsoleIsPdf && selectedConsolePdfUrl"
            :src="selectedConsolePdfUrl"
            :class="isWorkspaceFilePreviewFullscreen ? 'm-0 h-[calc(100vh-48px)] min-w-0 w-full rounded-md border border-slate-100 bg-white' : 'm-0 min-h-0 flex-1 min-w-0 w-full rounded-md border border-slate-100 bg-white'"
          ></iframe>
          <div
            v-else-if="selectedConsoleIsPdf && loadingWorkspaceFilePath === selectedConsoleFilePath"
            :class="isWorkspaceFilePreviewFullscreen ? 'flex h-[calc(100vh-48px)] items-center justify-center text-xs text-slate-400' : 'flex min-h-0 flex-1 items-center justify-center text-xs text-slate-400'"
          >PDF 加载中...</div>
          <div
            v-else-if="selectedConsoleIsOfficeFile && selectedConsoleOfficeHtml"
            :class="isWorkspaceFilePreviewFullscreen ? 'h-[calc(100vh-48px)] min-w-0 overflow-auto rounded-md border border-slate-100 bg-white' : 'min-h-0 flex-1 min-w-0 overflow-auto rounded-md border border-slate-100 bg-white'"
          >
            <iframe
              :srcdoc="selectedConsoleOfficeHtml"
              sandbox="allow-scripts"
              class="h-full min-w-0 w-full border-0 bg-white"
              title="Office 文件预览"
            ></iframe>
          </div>
          <div
            v-else-if="selectedConsoleIsOfficeFile && loadingOfficePreviewKey === `${conversationId}:${selectedConsoleFilePath}`"
            :class="isWorkspaceFilePreviewFullscreen ? 'flex h-[calc(100vh-48px)] items-center justify-center text-xs text-slate-400' : 'flex min-h-0 flex-1 items-center justify-center text-xs text-slate-400'"
          >Office 文件加载中...</div>
          <div
            v-else-if="selectedConsoleIsOfficeFile && selectedConsoleOfficePreviewReason"
            :class="isWorkspaceFilePreviewFullscreen ? 'flex h-[calc(100vh-48px)] flex-col items-center justify-center gap-2 text-xs text-slate-400' : 'flex min-h-0 flex-1 flex-col items-center justify-center gap-2 text-xs text-slate-400'"
          >
            <span v-if="selectedConsoleOfficePreviewReason === 'preview_too_large'">文件过大，暂不支持预览，请下载查看。</span>
            <span v-else>当前环境不能预览 Office 文件，请下载查看。</span>
          </div>
          <div
            v-else-if="selectedConsoleIsImage && selectedConsoleBinaryBlob?.url"
            :class="isWorkspaceFilePreviewFullscreen ? 'flex h-[calc(100vh-48px)] items-center justify-center overflow-auto rounded-md border border-slate-100 bg-white p-2' : 'flex min-h-0 flex-1 items-center justify-center overflow-auto rounded-md border border-slate-100 bg-white p-2'"
          >
            <img :src="selectedConsoleBinaryBlob.url" :alt="selectedConsoleFilePath || ''" class="max-h-full max-w-full object-contain" />
          </div>
          <div
            v-else-if="selectedConsoleIsImage && loadingWorkspaceFilePath === selectedConsoleFilePath"
            :class="isWorkspaceFilePreviewFullscreen ? 'flex h-[calc(100vh-48px)] items-center justify-center text-xs text-slate-400' : 'flex min-h-0 flex-1 items-center justify-center text-xs text-slate-400'"
          >图片加载中...</div>
          <div v-else-if="selectedConsoleBinaryBlob?.error" :class="isWorkspaceFilePreviewFullscreen ? 'flex h-[calc(100vh-48px)] items-center justify-center text-xs text-red-400' : 'flex min-h-0 flex-1 items-center justify-center text-xs text-red-400'">文件加载失败，请重试。</div>
          <pre v-else :class="isWorkspaceFilePreviewFullscreen ? 'm-0 h-[calc(100vh-48px)] overflow-auto whitespace-pre-wrap break-words text-xs leading-relaxed text-slate-700' : 'm-0 min-h-0 flex-1 overflow-auto whitespace-pre-wrap break-words text-xs leading-relaxed text-slate-700'">{{ selectedConsoleFileText || '暂无可预览内容' }}</pre>
        </div>
      </div>
      <div v-else-if="activePanelTab.kind === 'workspace' && SHOW_CONSOLE_FILE_PLACEHOLDER" class="rounded-lg border border-slate-100 bg-slate-50 p-3 text-xs text-slate-400">
        点击工作区文件查看内容。
      </div>
</template>
