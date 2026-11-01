<script setup lang="ts">
/* eslint-disable @typescript-eslint/no-unused-vars, @typescript-eslint/no-explicit-any -- 模板内使用的工具/组件与注入作用域，静态分析无法完整识别 */
/**
 * 文件管理 tab：根路径工具条 + 文件树 + 行级管理操作（上传/下载/删除/导入语义模型）。
 * 文件状态仍由 ConversationWorkspacePanel 持有，通过 WorkspaceFilesContextKey 共享，
 * 避免一个大组件同时承担四种 tab 的渲染；这里只做粗粒度 tab 组件拆分。
 */
import { inject } from 'vue'
import FileManagerToolbar from './FileManagerToolbar.vue'
import {
  CONSOLE_TREE_SIZE_COLUMN_CLASS,
  CONSOLE_TREE_TIME_COLUMN_CLASS,
  flattenFileTreeNodes,
} from '../../utils/consoleFileTree'
import { fileIconClass, formatEpochDateTime } from '../../utils'
import { formatFileSize } from '../../utils/consoleFileTree'
import { WorkspaceFilesContextKey } from '../../composables/workspaceFilesContext'

const {
  activePanelTab,
  producedFileTree,
  producedFiles,
  isWorkspaceFilesLoading,
  consoleTreeCollapsiblePaths,
  isConsoleTreeFullyCollapsed,
  isConsoleDirectoryCollapsed,
  toggleConsoleDirectory,
  consoleTreeIndentStyle,
  consoleTreeLeadSlotStyle,
  syncTruncatedTitle,
  isWorkspaceDirectoryArchiveDownloadable,
  getFileCount,
  isConsoleArchiveDownloading,
  handleDownloadConsoleDirectoryArchive,
  SHOW_CONSOLE_EMPTY_STATE,
  openFile,
  selectedConsoleFilePath,
  isFileTreeNodeCollapsed,
  toggleConsoleTreeCollapsed,
  isWorkspaceFileDownloadable,
  handleDownloadConsoleFile,
  isPdfFilePath,
  canImportSemanticModel,
  isGovernanceYaml,
  handleImportSemanticYaml,
  runBusy,
  deletingConsoleFilePath,
  handleDeleteConsoleFile,
  isWorkspaceFileUploading,
  openWorkspaceUploadPicker,
  importingSemanticFilePath,
  isWorkspaceFileDeletable,
} = inject(WorkspaceFilesContextKey, {}) as Record<string, any>
</script>

<template>
      <FileManagerToolbar
        v-if="activePanelTab.kind === 'workspace'"
        :root="producedFileTree.root"
        :file-count="producedFiles.length"
        :loading="isWorkspaceFilesLoading"
        :collapsible="consoleTreeCollapsiblePaths.length > 0"
        :fully-collapsed="isConsoleTreeFullyCollapsed"
        @toggle-collapsed="toggleConsoleTreeCollapsed"
      />

      <!-- 树的左缩进体系：顶层目录行用 px-1.5（6px）作基准，子层用 consoleTreeIndentStyle(depth)。
           改这里的 px-1.5 时必须同步 CONSOLE_TREE_INDENT_BASE_PX，否则第一级跨度会与后续级不等。
           根路径（/mnt/user-data）那一行已上移到面板头部标题行，这里直接从顶层目录开始。
           目录区域套一圈细边框，把「树」从面板的空白底上框出来：目录行是 7px 高、缩进逐级变化的
           文字行，没有边界时视线容易散到面板空白处；框住之后区域范围一眼可见（也顺带标出
           滚动区的范围）。空树时整块不渲染——留一个空框比不画更难解释。
           树容器仍带 @container：「时间 / 大小」两列的显隐按它的宽度算，与边框无关。 -->
      <div
        v-show="activePanelTab.kind === 'workspace'"
        class="flex min-h-0 flex-1 flex-col px-1 py-1 text-xs @container"
      >
        <div class="min-h-0 flex-1 space-y-0.5 overflow-auto rounded-md border border-slate-200 px-1 py-1">
          <div
            v-for="directory in producedFileTree.directories"
            :key="directory.name"
          >
            <div class="flex h-7 min-w-0 items-center gap-1 rounded pr-1 transition-colors hover:bg-slate-50">
              <button
                type="button"
                class="flex h-7 min-w-0 flex-1 items-center gap-1.5 border-none bg-transparent px-1.5 text-left text-slate-700"
                @click="toggleConsoleDirectory(directory.path)"
              >
                <i class="w-3 shrink-0 text-[10px] text-slate-400" :style="consoleTreeLeadSlotStyle" :class="isConsoleDirectoryCollapsed(directory.name) ? 'fa-solid fa-chevron-right' : 'fa-solid fa-chevron-down'"></i>
                <i class="fa-solid w-4 shrink-0 text-center text-amber-400" :class="isConsoleDirectoryCollapsed(directory.name) ? 'fa-folder' : 'fa-folder-open'"></i>
                <span class="truncate font-mono font-semibold" @mouseenter="syncTruncatedTitle">{{ directory.name }}</span>
                <span class="shrink-0 text-slate-400">{{ directory.label }}</span>
              </button>
              <!-- 上传入口挂在 uploads 目录行：它是 uploads 的唯一写入方，
                   与「往这个目录补文件」在语义上就近绑定，比塞在面板头部更好找。
                   该目录行由 PRODUCED_FILE_DIRECTORIES 常驻渲染，空会话也有入口。
                   文件只落 uploads（不进消息附件），智能体需要时经 parse_document / file_list 自行读取。 -->
              <button
                v-if="directory.name === 'uploads'"
                type="button"
                class="flex h-6 w-6 shrink-0 items-center justify-center rounded-md border-none bg-transparent text-[11px] leading-none text-slate-400 transition-colors hover:bg-slate-200/70 hover:text-slate-700 disabled:opacity-50"
                title="上传文件到工作区"
                aria-label="上传文件到工作区"
                :disabled="isWorkspaceFileUploading"
                data-console-upload="true"
                @click="openWorkspaceUploadPicker"
              >
                <i class="fa-solid" :class="isWorkspaceFileUploading ? 'fa-spinner fa-spin' : 'fa-upload'"></i>
              </button>
              <!-- 打包下载入口挂在 outputs 目录行：outputs 是智能体产出的汇聚地，
                   是「一次拿走全部结果」的真实诉求；workspace 是中间文件、uploads
                   内容用户本就有原件，不放开（见 utils/consoleFileTree.ts 白名单）。
                   空目录不显示（打包只会得到报错）；大小限制在压缩产物，前端无预检。 -->
              <button
                v-if="directory.name === 'outputs' && getFileCount(directory)"
                type="button"
                class="flex h-6 w-6 shrink-0 items-center justify-center rounded-md border-none bg-transparent text-[11px] leading-none text-slate-400 transition-colors hover:bg-blue-100 hover:text-blue-600 disabled:opacity-50"
                title="打包下载整个目录（zip）"
                aria-label="打包下载整个目录"
                :disabled="isConsoleArchiveDownloading === directory.name"
                data-console-archive-download="true"
                @click="handleDownloadConsoleDirectoryArchive(directory.name)"
              >
                <i class="fa-solid" :class="isConsoleArchiveDownloading === directory.name ? 'fa-spinner fa-spin' : 'fa-download'"></i>
              </button>
              <!-- 数量徽标统一放在每行最右端（按钮在其左侧），与面板头部同序：
                   所有数量落在同一条竖线上，扫一眼就能纵向比对。 -->
              <span v-if="getFileCount(directory)" class="shrink-0 rounded bg-slate-100 px-1.5 py-0.5 text-[10px] leading-none text-slate-500">{{ getFileCount(directory) }}</span>
            </div>
            <div v-if="directory.children.length && !isConsoleDirectoryCollapsed(directory.name)" class="space-y-0.5">
              <div
                v-for="item in flattenFileTreeNodes(directory.children, 1, isFileTreeNodeCollapsed)"
                :key="item.node.path"
                class="min-w-0"
              >
                <!-- 文件行：单行承载「选中预览 + 行尾操作」。预览/下载按钮默认透明，
                     hover 或该文件已选中时显现——面板窄、文件名长，常显会把文件名压没。
                     预览按钮与点文件名等价（选中并在下方面板渲染），只是把动作显式化。 -->
                <div
                  v-if="item.node.file"
                  :class="[
                    'group flex h-7 min-w-0 items-center gap-0.5 rounded pr-1 transition-colors hover:bg-blue-50',
                    selectedConsoleFilePath === item.node.file.relativePath ? 'bg-blue-50' : ''
                  ]"
                >
                  <button
                    type="button"
                    :class="[
                      'flex h-7 min-w-0 flex-1 items-center gap-1.5 border-none bg-transparent px-1.5 text-left transition-colors',
                      selectedConsoleFilePath === item.node.file.relativePath ? 'text-blue-700' : 'text-slate-600 hover:text-blue-700'
                    ]"
                    :style="consoleTreeIndentStyle(item.depth)"
                    @click="openFile(item.node.file.relativePath)"
                  >
                    <!-- 占位与图标必须 shrink-0：文件名的 flex-basis 是 auto，取的是 max-content
                         （长文件名可达 460px+）；只靠 truncate 的话，行内放不下时收缩量会按基础宽度
                         比例摊到所有子项，这个 12px 占位和 16px 图标会被一起挤窄，
                         导致「长文件名那一行」的图标与文字比上下行左移 6px/12px。 -->
                    <span class="w-3 shrink-0"></span>
                    <i class="w-4 shrink-0 text-center" :class="fileIconClass(item.node.name)"></i>
                    <!-- 名字被截断时悬浮显示完整文件名：面板窄、长文件名必然省略号截断 -->
                    <span class="min-w-0 flex-1 truncate font-mono text-[13px]" @mouseenter="syncTruncatedTitle">{{ item.node.name }}</span>
                    <!-- 修改时间 + 文件大小：行尾两个扫读列（行内样式常量见 CONSOLE_TREE_TIME/SIZE_COLUMN_CLASS），
                         名字让出空间（flex-1 + truncate），长名靠悬浮提示看全。
                         时间用绝对格式 YYYY-MM-DD HH:mm:ss——文件多是版本对照，需要能直接比出具体时刻。 -->
                    <span :class="CONSOLE_TREE_TIME_COLUMN_CLASS">{{ formatEpochDateTime(item.node.file.mtime) }}</span>
                    <span :class="CONSOLE_TREE_SIZE_COLUMN_CLASS">{{ formatFileSize(item.node.file.size) }}</span>
                  </button>
                  <div
                    class="flex shrink-0 items-center gap-0.5 transition-opacity"
                    :class="selectedConsoleFilePath === item.node.file.relativePath ? 'opacity-100' : 'opacity-0 group-hover:opacity-100 group-focus-within:opacity-100'"
                  >
                    <button
                      type="button"
                      class="flex h-6 w-6 items-center justify-center rounded-md border-none bg-transparent text-[11px] text-slate-400 transition-colors hover:bg-blue-100 hover:text-blue-600"
                      title="预览文件"
                      data-console-file-preview="true"
                      @click="openFile(item.node.file.relativePath)"
                    >
                      <i class="fa-solid fa-eye"></i>
                    </button>
                    <!-- SQL 查询结果（outputs/query_result、outputs/.tool-offload）不提供下载，
                         防数据泄漏；后端两侧已拦截，这里只是前端侧的纵深防御。 -->
                    <button
                      v-if="isWorkspaceFileDownloadable(item.node.file.relativePath) && !isPdfFilePath(item.node.file.relativePath)"
                      type="button"
                      class="flex h-6 w-6 items-center justify-center rounded-md border-none bg-transparent text-[11px] text-slate-400 transition-colors hover:bg-blue-100 hover:text-blue-600"
                      title="下载文件"
                      data-console-file-download="true"
                      @click="handleDownloadConsoleFile(item.node.file)"
                    >
                      <i class="fa-solid fa-download"></i>
                    </button>
                    <!-- 导入为语义模型：仅治理目录（outputs/governance/、workspace/governance/）下的 yaml 且管理员可见（canImportSemanticModel）。
                         点击读取文件内容并交由父级打开 Ossie 导入弹窗，免去下载后再上传。 -->
                    <button
                      v-if="canImportSemanticModel && isGovernanceYaml(item.node.file.relativePath)"
                      type="button"
                      class="flex h-6 w-6 items-center justify-center rounded-md border-none bg-transparent text-[11px] text-slate-400 transition-colors hover:bg-blue-100 hover:text-blue-600"
                      title="导入为语义模型"
                      data-console-file-import-semantic="true"
                      @click="handleImportSemanticYaml(item.node.file)"
                    >
                      <i class="fa-solid" :class="importingSemanticFilePath === item.node.file.relativePath ? 'fa-spinner fa-spin' : 'fa-file-import'"></i>
                    </button>
                    <!-- 删除：与预览/下载同列（行尾操作组），危险动作用红色区分。
                         查询结果（outputs/query_result、outputs/.tool-offload）不提供删除，
                         那是审计留存；run 执行中禁用，避免打断沙箱回写。 -->
                    <button
                      v-if="isWorkspaceFileDeletable(item.node.file.relativePath)"
                      type="button"
                      class="flex h-6 w-6 shrink-0 items-center justify-center rounded-md border-none bg-transparent text-[11px] text-slate-400 transition-colors hover:bg-red-100 hover:text-red-600 disabled:cursor-not-allowed disabled:opacity-40"
                      title="删除文件"
                      :disabled="runBusy || deletingConsoleFilePath === item.node.file.relativePath"
                      data-console-file-delete="true"
                      @click="handleDeleteConsoleFile(item.node.file)"
                    >
                      <i class="fa-solid" :class="deletingConsoleFilePath === item.node.file.relativePath ? 'fa-spinner fa-spin' : 'fa-trash-can'"></i>
                    </button>
                  </div>
                </div>
                <div
                  v-else
                  class="group flex h-7 min-w-0 items-center gap-1 rounded pr-1 transition-colors hover:bg-slate-50"
                >
                  <div
                    role="button"
                    tabindex="0"
                    class="flex h-7 min-w-0 flex-1 items-center gap-1.5 border-none bg-transparent px-1.5 text-left text-slate-600"
                    :style="consoleTreeIndentStyle(item.depth)"
                    @click="toggleConsoleDirectory(item.node.path)"
                    @keydown.enter.prevent="toggleConsoleDirectory(item.node.path)"
                    @keydown.space.prevent="toggleConsoleDirectory(item.node.path)"
                  >
                    <i class="w-3 shrink-0 text-[10px] text-slate-400" :style="consoleTreeLeadSlotStyle" :class="isFileTreeNodeCollapsed(item.node.path) ? 'fa-solid fa-chevron-right' : 'fa-solid fa-chevron-down'"></i>
                    <i class="fa-solid w-4 shrink-0 text-center text-amber-400" :class="isFileTreeNodeCollapsed(item.node.path) ? 'fa-folder' : 'fa-folder-open'"></i>
                    <span class="truncate font-mono text-[13px] font-semibold" @mouseenter="syncTruncatedTitle">{{ item.node.name }}</span>
                  </div>
                  <!-- 子目录打包下载：outputs 下的任意层级子目录都可单独打包（顶层范围仍是
                       outputs，见 utils/consoleFileTree.ts）。图标沿用文件行的 hover 显现——
                       子目录可能很多，常显会满屏都是下载图标压过目录名。
                       系统结果目录（query_result / .tool-offload）不显示，包里本就不会有文件。 -->
                  <button
                    v-if="isWorkspaceDirectoryArchiveDownloadable(item.node.path) && getFileCount(item.node)"
                    type="button"
                    class="flex h-6 w-6 shrink-0 items-center justify-center rounded-md border-none bg-transparent text-[11px] leading-none text-slate-400 transition-all hover:bg-blue-100 hover:text-blue-600 disabled:opacity-50"
                    :class="isConsoleArchiveDownloading === item.node.path ? 'opacity-100' : 'opacity-0 group-hover:opacity-100 group-focus-within:opacity-100'"
                    title="打包下载该目录（zip）"
                    aria-label="打包下载该目录"
                    :disabled="isConsoleArchiveDownloading === item.node.path"
                    data-console-archive-download="true"
                    @click="handleDownloadConsoleDirectoryArchive(item.node.path)"
                  >
                    <i class="fa-solid" :class="isConsoleArchiveDownloading === item.node.path ? 'fa-spinner fa-spin' : 'fa-download'"></i>
                  </button>
                  <!-- 数量徽标与顶层目录行同构：放在可点击区域之外的行尾，
                       所有数量徽标的右边缘落在同一条竖线上（放在按钮内时会被 px-1.5 推走 2px）。 -->
                  <span v-if="getFileCount(item.node)" class="shrink-0 rounded bg-slate-100 px-1.5 py-0.5 text-[10px] leading-none text-slate-500">{{ getFileCount(item.node) }}</span>
                </div>
              </div>
            </div>
          </div>
        </div>
        <div v-if="SHOW_CONSOLE_EMPTY_STATE && !producedFiles.length" class="mt-2 rounded bg-slate-50 px-2 py-2 text-xs text-slate-400">
          暂无文件
        </div>
      </div>
</template>
