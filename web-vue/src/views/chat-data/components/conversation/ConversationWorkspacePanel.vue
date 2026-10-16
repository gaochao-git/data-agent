<script setup lang="ts">
/**
 * 会话右侧面板：工作区文件 tab + 工具详情 tab。
 *
 * 从 conversation/Conversation.vue 拆出（原 900 行面板逻辑 + 385 行模板）。职责边界：
 * - 文件列表/树/预览（文本、JSON 表格、图表、Markdown、HTML、PDF、图片、Office、YAML）；
 * - 行级操作（预览、下载、删除、uploads 上传）与手动/自动刷新；
 * - 工具详情 tab 只做透传渲染（ToolCallDetailPanel 的数据由父级选中后传入）。
 *
 * 父级通过 ref 调用刷新入口（工具完成后的自动刷新 / 附件与产物点击后的选中打开），
 * 其余状态都在本组件内闭环；会话切换由 conversationId prop 驱动重置与重载。
 *
 * 注意：Props 用运行时声明 + PropType（不用外部类型做 defineProps<T> 泛型），
 * 见 AGENTS.md 4.3 —— chat-data 会被嵌入未安装 typescript 的宿主项目。
 */
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import type { PropType } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { SemanticYamlFile, ToolCallInfo } from '../../types'
import ConversationSidePanel from './ConversationSidePanel.vue'
import ChartSpecRenderer from '../ChartSpecRenderer.vue'
import MarkdownFilePreview from './MarkdownFilePreview.vue'
import HtmlPagePreview from './HtmlPagePreview.vue'
import CodeFilePreview from './CodeFilePreview.vue'
import MermaidFilePreview from './MermaidFilePreview.vue'
import ToolCallDetailPanel from './ToolCallDetailPanel.vue'
import {
  deleteConversationWorkspaceFile,
  getConversationWorkspaceFileContent,
  getConversationWorkspaceFiles,
  getConversationWorkspaceOfficeHtmlPreview,
  getConversationWorkspaceRawFile,
  getConversationWorkspaceArchive,
  uploadFile,
  type ConversationWorkspaceFile,
} from '../../api'
import {
  collapseAllDirectories,
  collectCollapsibleDirectoryPaths,
  DEFAULT_COLLAPSED_CONSOLE_DIRECTORIES,
  expandAllDirectories,
  formatFileSize,
  flattenFileTreeNodes,
  consoleTreeIndentStyle,
  consoleTreeLeadSlotStyle,
  CONSOLE_TREE_TIME_COLUMN_CLASS,
  CONSOLE_TREE_SIZE_COLUMN_CLASS,
  getFileCount,
  isTreeFullyCollapsed,
  isWorkspaceFileDeletable,
  isWorkspaceDirectoryArchiveDownloadable,
  syncTruncatedTitle,
  isWorkspaceFileDownloadable,
  normalizeVisiblePath,
  buildProducedFileTree,
  buildProducedFiles,
  toSandboxDisplayPath,
  type ProducedFile,
} from '../../utils/consoleFileTree'
import { evictWorkspaceFileCache } from '../../utils/workspacePreviewCache'
import { formatEpochDateTime, IMAGE_PREVIEW_EXTENSIONS, IMAGE_PREVIEW_MIME, OFFICE_HTML_PREVIEW_EXTENSIONS, isBinaryPreviewPath } from '../../utils'
import { formatToolTableCell, parseChartSpecValue, replaceVisiblePathsForDisplay } from '../../utils/messageArtifacts'
import { extractChangedFilePathsFromResult, extractWrittenFilePathCandidates } from '../../utils/toolDisplay'
import { ACCEPTED_FILE_TYPES, MAX_FILE_SIZE } from '../../utils/uploadLimits'

const props = defineProps({
  conversationId: { type: String as PropType<string | null>, default: null },
  open: { type: Boolean, default: false },
  tab: { type: String as PropType<'console' | 'tool'>, default: 'console' },
  activeToolCall: { type: Object as PropType<ToolCallInfo | null>, default: null },
  /** Run 执行中禁用删除（沙箱回写冲突），与父级 runBusy 同源 */
  runBusy: { type: Boolean, default: false },
  /** 是否显示「导入为语义模型」行操作（管理员权限，与配置入口同源） */
  canImportSemanticModel: { type: Boolean, default: false },
  formatText: { type: Function as PropType<(value: string) => string>, default: (value: string) => value },
  /** 面板内上传需要新建会话时调用；创建成功后父级负责同步会话状态 */
  ensureConversationId: { type: Function as PropType<() => Promise<string | null>>, default: null },
})

const emit = defineEmits<{
  'update:open': [value: boolean]
  'update:tab': [value: 'console' | 'tool']
  conversationCreated: [conversationId: string]
  /** 工作区 yaml 一键导入语义模型：文件内容已在面板内读取完毕 */
  importSemanticYaml: [files: SemanticYamlFile[]]
}>()

const selectedConsoleFilePath = ref<string | null>(null)
// 默认只收起 uploads / workspace，outputs 保持展开——理由见 consoleFileTree.ts 里的常量注释。
const collapsedConsoleDirectories = ref<Set<string>>(new Set(DEFAULT_COLLAPSED_CONSOLE_DIRECTORIES))
const consoleFilePreviewMode = ref<'render' | 'table' | 'source'>('render')
const workspaceFiles = ref<ConversationWorkspaceFile[]>([])
const workspaceFileContents = ref<Record<string, string>>({})
const workspaceFileBlobs = ref<Record<string, { url: string; arrayBuffer: ArrayBuffer; ext: string; mime: string; error?: boolean }>>({})
const workspaceFilePreviewRef = ref<HTMLElement | null>(null)
const isWorkspaceFilePreviewFullscreen = ref(false)
const selectedConsoleOfficeHtml = ref<string | null>(null)
const selectedConsoleOfficePreviewReason = ref<string | null>(null)
const loadingOfficePreviewKey = ref<string | null>(null)
const workspaceFilePreviewRequestSequence = ref(0)
const loadingOfficePreviewSequence = ref<number | null>(null)
const loadingWorkspaceFileSequence = ref<number | null>(null)
const isWorkspaceFilesLoading = ref(false)
const loadingWorkspaceFilePath = ref<string | null>(null)
// 手动刷新按钮的忙碌态：列表请求与当前预览内容请求（文本/二进制/Office）任一进行中都算。
// 只绑列表态会出现「图标停转了但内容还在加载」的错觉（按钮 title 同步用这个口径）。
const isWorkspaceRefreshBusy = computed(() => {
  if (isWorkspaceFilesLoading.value) return true
  const selectedPath = selectedConsoleFilePath.value
  if (!selectedPath) return false
  if (loadingWorkspaceFilePath.value === selectedPath) return true
  return loadingOfficePreviewKey.value === `${props.conversationId}:${selectedPath}`
})
const workspaceFilesRefreshTimer = ref<number | null>(null)
const workspaceUploadInputRef = ref<HTMLInputElement | null>(null)
const isWorkspaceFileUploading = ref(false)
const deletingConsoleFilePath = ref<string | null>(null)

// 两处占位文案（树为空时的「暂无文件」、未选中文件时的「点击工作区文件查看内容。」）
// 被认为冗余——文件数量已在面板头部、树本身空不空一眼可见。这里只隐藏渲染，
// 模板与样式全部保留，需要恢复时把对应常量改回 true 即可。
const SHOW_CONSOLE_EMPTY_STATE = false
const SHOW_CONSOLE_FILE_PLACEHOLDER = false

const producedFiles = computed<ProducedFile[]>(() => buildProducedFiles(workspaceFiles.value))
const producedFileTree = computed(() => buildProducedFileTree(producedFiles.value))

const sidePanelTitle = computed(() => props.tab === 'console' ? '工作区' : '工具详情')
// 工作区 tab 不再在头部重复显示根路径——文件树根节点已经写着同一个路径，
// 需要恢复时把下面的 '' 换回 producedFileTree.value.root
const sidePanelSubtitle = computed(() => (
  props.tab === 'console'
    ? ''
    : '工具调用参数与结果'
))

const selectedConsoleFile = computed(() => {
  const selectedPath = selectedConsoleFilePath.value
  if (!selectedPath) return null
  return producedFiles.value.find(file => file.relativePath === selectedPath) || null
})

const selectedWorkspaceFile = computed(() => {
  const selectedPath = selectedConsoleFilePath.value
  if (!selectedPath) return null
  return workspaceFiles.value.find(file => normalizeVisiblePath(file.relative_path || file.path) === selectedPath) || null
})

const isConsoleDirectoryCollapsed = (name: string): boolean => collapsedConsoleDirectories.value.has(name)

const setConsoleDirectoryCollapsed = (path: string, collapsed: boolean) => {
  const normalized = normalizeVisiblePath(path)
  const next = new Set(collapsedConsoleDirectories.value)
  if (collapsed) next.add(normalized)
  else next.delete(normalized)
  collapsedConsoleDirectories.value = next
}

const isFileTreeNodeCollapsed = (path: string): boolean => collapsedConsoleDirectories.value.has(path)

const toggleConsoleDirectory = (path: string) => {
  const normalized = normalizeVisiblePath(path)
  setConsoleDirectoryCollapsed(normalized, !isFileTreeNodeCollapsed(normalized))
}

// 一键折叠/展开整棵目录树。按钮文案直接由「是否已全部折叠」推导，不另存 UI 状态，
// 这样逐层点击与一键操作共用同一个 collapsedConsoleDirectories，不会出现两边说法不一致。
const consoleTreeCollapsiblePaths = computed(() =>
  collectCollapsibleDirectoryPaths(producedFileTree.value.directories),
)

const isConsoleTreeFullyCollapsed = computed(() =>
  isTreeFullyCollapsed(consoleTreeCollapsiblePaths.value, collapsedConsoleDirectories.value),
)

const toggleConsoleTreeCollapsed = () => {
  const paths = consoleTreeCollapsiblePaths.value
  collapsedConsoleDirectories.value = isConsoleTreeFullyCollapsed.value
    ? expandAllDirectories(paths, collapsedConsoleDirectories.value)
    : collapseAllDirectories(paths, collapsedConsoleDirectories.value)
}

const selectConsoleFile = (path: string) => {
  const normalizedPath = normalizeVisiblePath(path)
  if (!normalizedPath) return

  const sameFileSelected = selectedConsoleFilePath.value === normalizedPath
  selectedConsoleFilePath.value = normalizedPath

  // Clicking the already selected file does not trigger the path watcher.
  // Keep the current preview visible while explicitly reloading it instead
  // of clearing the content and leaving the panel without a request.
  if (sameFileSelected) {
    void loadWorkspaceFileContent(props.conversationId, normalizedPath, { forceReload: true })
    return
  }

  selectedConsoleOfficeHtml.value = null
  selectedConsoleOfficePreviewReason.value = null
  loadingOfficePreviewKey.value = null
  consoleFilePreviewMode.value = 'render'
}

const syncWorkspaceFilePreviewFullscreen = () => {
  isWorkspaceFilePreviewFullscreen.value = document.fullscreenElement === workspaceFilePreviewRef.value
}

const toggleWorkspaceFilePreviewFullscreen = async () => {
  const preview = workspaceFilePreviewRef.value
  if (!preview) return
  if (document.fullscreenElement === preview) {
    await document.exitFullscreen()
    return
  }
  await preview.requestFullscreen()
}

onMounted(() => {
  document.addEventListener('fullscreenchange', syncWorkspaceFilePreviewFullscreen)
})

onUnmounted(() => {
  document.removeEventListener('fullscreenchange', syncWorkspaceFilePreviewFullscreen)
})

const loadWorkspaceFiles = async (conversationId: string | null | undefined) => {
  if (!conversationId) {
    workspaceFiles.value = []
    Object.values(workspaceFileBlobs.value).forEach(b => { if (b.url) URL.revokeObjectURL(b.url) })
    workspaceFileBlobs.value = {}
    workspaceFileContents.value = {}
    selectedConsoleOfficeHtml.value = null
    selectedConsoleOfficePreviewReason.value = null
    loadingOfficePreviewKey.value = null
    return
  }
  isWorkspaceFilesLoading.value = true
  try {
    const response = await getConversationWorkspaceFiles(conversationId)
    if (conversationId !== props.conversationId) return
    workspaceFiles.value = response.data?.files || []
  } catch (err) {
    if (conversationId !== props.conversationId) return
    workspaceFiles.value = []
    console.error('加载智能分析工作区文件失败:', err)
  } finally {
    if (conversationId === props.conversationId) {
      isWorkspaceFilesLoading.value = false
    }
  }
}

const scheduleWorkspaceFilesRefresh = () => {
  if (!props.conversationId) return
  if (workspaceFilesRefreshTimer.value !== null) {
    window.clearTimeout(workspaceFilesRefreshTimer.value)
  }
  workspaceFilesRefreshTimer.value = window.setTimeout(() => {
    workspaceFilesRefreshTimer.value = null
    loadWorkspaceFiles(props.conversationId)
  }, 500)
}

// 手动刷新（预览区头部按钮）：刷新文件列表之外，还要强制重载当前预览的内容——
// 列表刷新不会作废内容缓存（见 utils/workspacePreviewCache.ts），只刷列表会让人以为
// 「点了刷新但预览还是旧的」。
const refreshWorkspaceFiles = () => {
  if (!props.conversationId) return
  void loadWorkspaceFiles(props.conversationId)
  if (selectedConsoleFilePath.value) {
    void loadWorkspaceFileContent(props.conversationId, selectedConsoleFilePath.value, { forceReload: true })
  }
}

// 超过渲染上限的文本只保留前 CONSOLE_TEXT_RENDER_MAX_CHARS 字符并附提示（下载路径不受影响）。
const capWorkspaceTextPreview = (content: string) => {
  if (content.length <= CONSOLE_TEXT_RENDER_MAX_CHARS) return content
  return `${content.slice(0, CONSOLE_TEXT_RENDER_MAX_CHARS)}\n\n...（文件过大，仅显示前 1MB，完整内容请下载查看）...`
}

type WorkspaceFileLoadOptions = {
  forceReload?: boolean
}

// 文本预览渲染上限：后端文本预览源上限为 5MB，但超大文本全量进 DOM（JSON 还要 parse+stringify）会卡死页面，
// 超限只渲染前 1MB 并提示下载查看（对齐 Office 预览「源 50MB / 渲染 12MB」的双层设计）。
const CONSOLE_TEXT_RENDER_MAX_CHARS = 1 * 1024 * 1024

const loadWorkspaceFileContent = async (
  conversationId: string | null | undefined,
  path: string | null,
  options: WorkspaceFileLoadOptions = {},
) => {
  if (!conversationId || !path) return
  const ext = path.split('.').pop()?.toLowerCase() || ''
  const officeRequestKey = `${conversationId}:${path}`
  if (
    !options.forceReload
    && OFFICE_HTML_PREVIEW_EXTENSIONS.has(ext)
    && loadingOfficePreviewKey.value === officeRequestKey
    && loadingOfficePreviewSequence.value === workspaceFilePreviewRequestSequence.value
  ) return
  const requestSequence = ++workspaceFilePreviewRequestSequence.value
  const refreshToken = options.forceReload ? `${Date.now()}-${requestSequence}` : undefined
  if (isBinaryPreviewPath(path)) {
    if (OFFICE_HTML_PREVIEW_EXTENSIONS.has(ext)) {
      await loadWorkspaceOfficeHtmlPreview(
        conversationId,
        path,
        requestSequence,
        options.forceReload === true,
      )
    } else {
      await loadWorkspaceBinaryFile(
        conversationId,
        path,
        requestSequence,
        options.forceReload === true,
        refreshToken,
      )
    }
    return
  }
  if (!options.forceReload && workspaceFileContents.value[path] !== undefined) return
  loadingWorkspaceFilePath.value = path
  loadingWorkspaceFileSequence.value = requestSequence
  try {
    const response = await getConversationWorkspaceFileContent(conversationId, path, refreshToken)
    if (!isCurrentWorkspaceFilePreviewRequest(conversationId, path, requestSequence)) return
    workspaceFileContents.value = {
      ...workspaceFileContents.value,
      [path]: capWorkspaceTextPreview(response.data?.content || ''),
    }
  } catch (err) {
    if (!isCurrentWorkspaceFilePreviewRequest(conversationId, path, requestSequence)) return
    if (!options.forceReload || workspaceFileContents.value[path] === undefined) {
      workspaceFileContents.value = {
        ...workspaceFileContents.value,
        [path]: '文件内容加载失败。',
      }
    }
    console.error('加载智能分析工作区文件内容失败:', err)
  } finally {
    if (loadingWorkspaceFileSequence.value === requestSequence) {
      loadingWorkspaceFilePath.value = null
      loadingWorkspaceFileSequence.value = null
    }
  }
}

const isCurrentWorkspaceFilePreviewRequest = (
  conversationId: string,
  path: string,
  requestSequence: number,
) => (
  requestSequence === workspaceFilePreviewRequestSequence.value
  && conversationId === props.conversationId
  && path === selectedConsoleFilePath.value
)

// 工作区文件被工具改写后，若改的正是当前预览的文件，强制重载预览内容。
// 预览内容缓存按路径命中短路（见 utils/workspacePreviewCache.ts），磁盘上的新内容不会自愈，
// 必须由「知道文件已变」的一方显式 forceReload；工具 args 里的路径先归一化成与
// selectedConsoleFilePath 同一口径（剥掉 /mnt/user-data 前缀）再比较。
// bash_run / officecli_run 的间接写入没有结构化 args 路径，从结果 payload 的
// changed_files（服务端 manifest 前后对比产出）补齐归因。
const reloadPreviewIfSelected = (toolInfo?: ToolCallInfo | null) => {
  const conversationId = props.conversationId
  const selectedPath = selectedConsoleFilePath.value
  if (!conversationId || !selectedPath) return
  const args = (toolInfo?.args || {}) as Record<string, unknown>
  const writtenPaths = [
    ...extractWrittenFilePathCandidates(args).map(normalizeVisiblePath),
    ...extractChangedFilePathsFromResult(toolInfo?.result).map(normalizeVisiblePath),
  ]
  if (writtenPaths.some(path => path === selectedPath)) {
    void loadWorkspaceFileContent(conversationId, selectedPath, { forceReload: true })
  }
}

const loadWorkspaceOfficeHtmlPreview = async (
  conversationId: string,
  path: string,
  requestSequence: number,
  forceReload = false,
) => {
  const requestKey = `${conversationId}:${path}`
  const refreshToken = forceReload ? `${Date.now()}-${requestSequence}` : undefined
  loadingOfficePreviewKey.value = requestKey
  loadingOfficePreviewSequence.value = requestSequence
  try {
    const response = await getConversationWorkspaceOfficeHtmlPreview(conversationId, path, refreshToken)
    if (!isCurrentWorkspaceFilePreviewRequest(conversationId, path, requestSequence)) return
    const preview = response.data
    selectedConsoleOfficeHtml.value = preview?.available && !preview.truncated && preview.html
      ? preview.html
      : null
    selectedConsoleOfficePreviewReason.value = selectedConsoleOfficeHtml.value
      ? null
      : (preview?.reason || 'preview_unavailable')
  } catch (err) {
    if (!isCurrentWorkspaceFilePreviewRequest(conversationId, path, requestSequence)) return
    if (!forceReload || !selectedConsoleOfficeHtml.value) {
      selectedConsoleOfficeHtml.value = null
      selectedConsoleOfficePreviewReason.value = 'preview_failed'
    }
    console.warn('OfficeCLI 文件预览不可用:', err)
  } finally {
    if (loadingOfficePreviewSequence.value === requestSequence) {
      loadingOfficePreviewKey.value = null
      loadingOfficePreviewSequence.value = null
    }
  }
}

const loadWorkspaceBinaryFile = async (
  conversationId: string,
  path: string,
  requestSequence: number,
  forceReload = false,
  refreshToken?: string,
) => {
  if (!forceReload && workspaceFileBlobs.value[path] !== undefined) return
  loadingWorkspaceFilePath.value = path
  loadingWorkspaceFileSequence.value = requestSequence
  try {
    const blob = await getConversationWorkspaceRawFile(conversationId, path, refreshToken)
    if (!isCurrentWorkspaceFilePreviewRequest(conversationId, path, requestSequence)) return
    const arrayBuffer = await blob.arrayBuffer()
    if (!isCurrentWorkspaceFilePreviewRequest(conversationId, path, requestSequence)) return
    const ext = path.split('.').pop()?.toLowerCase() || ''
    const mime = IMAGE_PREVIEW_MIME[ext] || blob.type || 'application/octet-stream'
    const url = URL.createObjectURL(new Blob([arrayBuffer], { type: mime }))
    const previousUrl = workspaceFileBlobs.value[path]?.url
    if (previousUrl) URL.revokeObjectURL(previousUrl)
    workspaceFileBlobs.value = {
      ...workspaceFileBlobs.value,
      [path]: { url, arrayBuffer, ext, mime },
    }
  } catch (err) {
    if (!isCurrentWorkspaceFilePreviewRequest(conversationId, path, requestSequence)) return
    if (!forceReload || workspaceFileBlobs.value[path] === undefined) {
      workspaceFileBlobs.value = {
        ...workspaceFileBlobs.value,
        [path]: { url: '', arrayBuffer: new ArrayBuffer(0), ext: '', mime: '', error: true },
      }
    }
    console.error('加载工作区二进制文件失败:', err)
  } finally {
    if (loadingWorkspaceFileSequence.value === requestSequence) {
      loadingWorkspaceFilePath.value = null
      loadingWorkspaceFileSequence.value = null
    }
  }
}

const selectedConsoleFileExt = computed(() => {
  const p = selectedConsoleFilePath.value || ''
  return p.split('.').pop()?.toLowerCase() || ''
})
const selectedConsoleIsPdf = computed(() => selectedConsoleFileExt.value === 'pdf')
const selectedConsoleIsOfficeFile = computed(() => OFFICE_HTML_PREVIEW_EXTENSIONS.has(selectedConsoleFileExt.value))
const selectedConsoleIsImage = computed(() => IMAGE_PREVIEW_EXTENSIONS.has(selectedConsoleFileExt.value))
const selectedConsoleIsMarkdown = computed(() => selectedConsoleFileExt.value === 'md')
const selectedConsoleIsMermaid = computed(() => selectedConsoleFileExt.value === 'mmd')
const selectedConsoleIsYaml = computed(() => ['yaml', 'yml'].includes(selectedConsoleFileExt.value))
const selectedConsoleIsHtml = computed(() => selectedConsoleFileExt.value === 'html' || selectedConsoleFileExt.value === 'htm')
// HTML 渲染必须用原始文本：展示层的路径改写会破坏内联 JSON 数据与代码。仅源码视图走替换后的展示文案。
const selectedConsoleHtmlDocument = computed(() => {
  const selectedPath = selectedConsoleFilePath.value
  if (!selectedPath || !selectedConsoleIsHtml.value) return ''
  return workspaceFileContents.value[selectedPath] ?? ''
})
const workspaceFilePreviewHeightClass = computed(() => (
  isWorkspaceFilePreviewFullscreen.value ? 'h-[calc(100vh-48px)]' : 'h-[46vh]'
))
const selectedConsoleBinaryBlob = computed(() => {
  const p = selectedConsoleFilePath.value
  if (!p) return null
  return workspaceFileBlobs.value[p] || null
})

const selectedConsoleFileText = computed(() => {
  const selectedPath = selectedConsoleFilePath.value
  if (!selectedPath || !selectedWorkspaceFile.value) return ''
  if (loadingWorkspaceFilePath.value === selectedPath) return '文件内容加载中...'
  const content = workspaceFileContents.value[selectedPath] ?? ''
  if (!selectedPath.toLowerCase().endsWith('.json')) return replaceVisiblePathsForDisplay(content)
  try {
    return replaceVisiblePathsForDisplay(JSON.stringify(JSON.parse(content), null, 2))
  } catch {
    return replaceVisiblePathsForDisplay(content)
  }
})

const selectedConsoleChartSpec = computed<Record<string, unknown> | null>(() => {
  const selectedPath = selectedConsoleFilePath.value
  if (!selectedPath || !selectedWorkspaceFile.value || !selectedPath.toLowerCase().endsWith('.json')) return null
  return parseChartSpecValue(selectedConsoleFileText.value)
})

const selectedConsoleJsonValue = computed<unknown>(() => {
  const text = selectedConsoleFileText.value.trim()
  if (!text || loadingWorkspaceFilePath.value === selectedConsoleFilePath.value) return null
  try {
    return JSON.parse(text)
  } catch {
    return null
  }
})

const selectedConsoleTableRows = computed<Record<string, unknown>[]>(() => {
  const value = selectedConsoleJsonValue.value
  const rows = Array.isArray(value)
    ? value
    : (
        value && typeof value === 'object' && !Array.isArray(value)
          ? ((value as Record<string, unknown>).data || (value as Record<string, unknown>).rows)
          : null
      )
  if (!Array.isArray(rows)) return []
  return rows.filter((row): row is Record<string, unknown> => row !== null && typeof row === 'object' && !Array.isArray(row))
})

const selectedConsoleTableColumns = computed<string[]>(() => {
  const value = selectedConsoleJsonValue.value
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    const payloadColumns = (value as Record<string, unknown>).columns
    if (Array.isArray(payloadColumns)) {
      const columns = payloadColumns.map(col => String(col || '').trim()).filter(Boolean)
      if (columns.length) return columns
    }
  }
  const columns = new Set<string>()
  selectedConsoleTableRows.value.slice(0, 20).forEach((row) => {
    Object.keys(row).forEach(key => columns.add(key))
  })
  return Array.from(columns)
})

const selectedConsolePreviewRows = computed(() => selectedConsoleTableRows.value.slice(0, 50))
const hasSelectedConsoleTable = computed(() => selectedConsoleTableColumns.value.length > 0 && selectedConsoleTableRows.value.length > 0)

// ===== 预览区复制（内容 / 路径） =====
// 复制口径 = 预览所见：文本受 capWorkspaceTextPreview 截断、表格取展示的前 50 行，复制不超出所见范围。
// navigator.clipboard 需 secure context，内网 http 部署走 textarea 兜底（同 AgentScheduleManager 口径）。
const copyPreviewText = async (text: string, successMessage: string) => {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text)
    } else {
      const textarea = document.createElement('textarea')
      textarea.value = text
      textarea.style.position = 'fixed'
      textarea.style.opacity = '0'
      document.body.appendChild(textarea)
      textarea.select()
      document.execCommand('copy')
      textarea.remove()
    }
    ElMessage.success(successMessage)
  } catch (err) {
    console.error('复制失败:', err)
    ElMessage.error('复制失败，请手动复制')
  }
}

const handleCopyWorkspacePreviewContent = () => {
  // 表格模式复制所见表格（TSV，粘进 Excel/WPS 即成表格）；单元格用全值文本，非界面截断的显示文本。
  if (hasSelectedConsoleTable.value && consoleFilePreviewMode.value === 'table') {
    const escapeTsvCell = (value: string) => value.replace(/\t/g, '    ').replace(/\r?\n/g, ' ')
    const lines = [selectedConsoleTableColumns.value.join('\t')]
    selectedConsolePreviewRows.value.forEach((row) => {
      lines.push(selectedConsoleTableColumns.value.map((col) => escapeTsvCell(formatToolTableCell(row[col]))).join('\t'))
    })
    void copyPreviewText(lines.join('\n'), `已复制表格内容（TSV，${selectedConsolePreviewRows.value.length} 行）`)
    return
  }
  if (loadingWorkspaceFilePath.value === selectedConsoleFilePath.value) {
    ElMessage.warning('文件内容加载中，请稍后再复制')
    return
  }
  const text = selectedConsoleFileText.value
  if (!text) {
    ElMessage.warning('暂无可复制的内容')
    return
  }
  void copyPreviewText(text, '已复制文件内容')
}

const handleCopyWorkspacePreviewPath = () => {
  const path = selectedConsoleFile.value?.displayPath || ''
  if (!path) return
  void copyPreviewText(path, '已复制文件路径')
}

// 工作区里的文件（uploads / workspace / outputs）都可直接下载，不依赖智能体是否调用过展示工具。
// 后端 raw 接口对三个目录一视同仁，此前的 outputs 限制只是前端产品决策，已放开。
// SQL 查询结果与工具结果卸载缓存除外——见 utils/consoleFileTree.ts 的下载白名单说明。
// file 由文件树每行的下载按钮传入；不传时回退到当前选中文件（兼容旧调用点）。
const handleDownloadConsoleFile = async (file: ProducedFile | null = selectedConsoleFile.value) => {
  const conversationId = props.conversationId
  if (!file) return
  if (!isWorkspaceFileDownloadable(file.relativePath)) {
    ElMessage.warning('该文件不允许下载')
    return
  }
  if (!conversationId) {
    ElMessage.warning('会话未就绪，无法下载文件')
    return
  }
  try {
    const blob = await getConversationWorkspaceRawFile(conversationId, file.relativePath)
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = file.relativePath.split('/').pop() || file.relativePath
    document.body.appendChild(link)
    link.click()
    link.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (err) {
    console.error('下载工作区文件失败:', err)
    ElMessage.error('文件下载失败，请稍后重试')
  }
}

// 目录打包下载：把 outputs 或其下子目录打成 zip（用户拍板 2026-09-19 只放开 outputs，
// 2026-09-24 追加放开其下层子目录；顶层范围仍是 outputs 一个）。
// 传入的是目录相对路径（顶层为目录名，子目录为 'outputs/月报' 形式），下载文件名取末段
// ——全路径含斜杠不能进文件名。
// 大小限制针对压缩产物（后端 CHAT_DATA_WORKSPACE_ARCHIVE_MAX_MB，默认 50MB）——压缩前
// 无法预测产物大小，所以没有前端预检，超限时后端边打包边计量、中止并返回业务错误 JSON。
// 后端业务错误走 HTTP 200 + JSON 体（与 raw 接口的错误形态一致），而本请求 responseType
// 是 blob，因此拿到响应后先看 blob.type：application/json 说明是业务错误，解析 msg 提示。
const isConsoleArchiveDownloading = ref<string>('')
const handleDownloadConsoleDirectoryArchive = async (directoryPath: string) => {
  if (!isWorkspaceDirectoryArchiveDownloadable(directoryPath)) {
    ElMessage.warning('该目录不支持打包下载')
    return
  }
  if (!props.conversationId) {
    ElMessage.warning('会话未就绪，无法打包下载')
    return
  }
  if (isConsoleArchiveDownloading.value) return
  isConsoleArchiveDownloading.value = directoryPath
  try {
    const blob = await getConversationWorkspaceArchive(props.conversationId, directoryPath)
    if (blob.type.includes('application/json')) {
      const text = await blob.text()
      let msg = '打包下载失败，请稍后重试'
      try {
        const payload = JSON.parse(text)
        msg = payload?.data?.msg || payload?.msg || msg
      } catch {
        // 保留默认文案
      }
      ElMessage.warning(msg)
      return
    }
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `${directoryPath.split('/').pop() || directoryPath}.zip`
    document.body.appendChild(link)
    link.click()
    link.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (err) {
    console.error('打包下载工作区目录失败:', err)
    ElMessage.error('打包下载失败，请稍后重试')
  } finally {
    isConsoleArchiveDownloading.value = ''
  }
}

// 删除工作区文件：uploads / workspace / outputs 一视同仁，唯独 SQL 查询结果与工具结果缓存例外
// ——它们是审计留存，后端删除接口同样拒绝（前端这层是纵深防御，见 utils/consoleFileTree.ts）。
// 物理删除不可恢复，因此必须走二次确认；uploads 里的文件还会由后端连带清理 uploaded_files 记录，
// 否则历史消息里的附件卡片会指向一个已不存在的文件。
// 删除被设计成「单文件」：目录递归删需要独立的确认交互，不在这一版里提供。
// 删除成功后必须连同预览缓存一起作废，否则「删除 → 重传同名 → 点预览」会命中旧缓存：
// 文本预览显示上一份文件的内容、二进制预览复用一个指向已删文件的 blob URL，而且缓存命中
// 就不再发请求，这个状态不会自愈。这不是理论风险——uploads 的重名策略是「已存在才加 -1 后缀」
// （后端 uploaded_file_service.py::_unique_storage_name），文件删掉后重传同名拿回的就是
// 删除前的原路径，缓存必然命中。
// 失效规则本身在 utils/workspacePreviewCache.ts（纯函数，有单测），这里只做反射式赋值与 revoke。
const forgetWorkspaceFileCache = (path: string) => {
  const { contents, blobs, revokedUrl } = evictWorkspaceFileCache(
    path,
    workspaceFileContents.value,
    workspaceFileBlobs.value,
  )
  workspaceFileContents.value = contents
  workspaceFileBlobs.value = blobs
  if (revokedUrl) URL.revokeObjectURL(revokedUrl)
}

// 「导入为语义模型」仅对治理目录下的 Ossie yaml 开放：outputs/governance/（自检通过的交付副本）
// 与 workspace/governance/（治理工作副本，用户确认后也可直接导）。任意 yaml（其他技能产物、
// 用户上传）不提供导入入口；workspace/semantic/ 是原模型只读快照，不开放。
// 扩展名判断与上方 selectedConsoleFileExt 同一套取后缀习惯用法。
const isGovernanceYaml = (path: string) => {
  const lower = path.toLowerCase()
  if (!lower.startsWith('outputs/governance/') && !lower.startsWith('workspace/governance/')) return false
  return ['yaml', 'yml'].includes(path.split('.').pop()?.toLowerCase() || '')
}
const importingSemanticFilePath = ref<string | null>(null)

const handleImportSemanticYaml = async (file: ProducedFile) => {
  const conversationId = props.conversationId
  if (!conversationId) {
    ElMessage.warning('会话未就绪，无法导入')
    return
  }
  if (importingSemanticFilePath.value) return
  importingSemanticFilePath.value = file.relativePath
  try {
    const response = await getConversationWorkspaceFileContent(conversationId, file.relativePath)
    const content = response.status === 'success' ? String(response.data?.content || '') : ''
    if (!content.trim()) {
      ElMessage.error('文件内容为空或读取失败，无法导入')
      return
    }
    emit('importSemanticYaml', [{
      name: file.relativePath.split('/').pop() || file.relativePath,
      content,
    }] satisfies SemanticYamlFile[])
  } catch {
    ElMessage.error('读取文件失败，无法导入')
  } finally {
    importingSemanticFilePath.value = null
  }
}

const handleDeleteConsoleFile = async (file: ProducedFile | null = null) => {
  const conversationId = props.conversationId
  if (!file) return
  const fileName = file.relativePath.split('/').pop() || file.relativePath
  if (!isWorkspaceFileDeletable(file.relativePath)) {
    ElMessage.warning('查询结果需要留存审计，不支持删除')
    return
  }
  if (!conversationId) {
    ElMessage.warning('会话未就绪，无法删除文件')
    return
  }
  // Run 执行中删工作区文件，会让沙箱回写被判成「应用侧已变更」而使该轮落盘失败。
  // 前端按 runBusy 拦截，后端对 pending/active/cancelling 同样返回 409，双保险。
  if (props.runBusy) {
    ElMessage.warning('智能体执行中，暂不能删除文件')
    return
  }
  try {
    await ElMessageBox.confirm(`确定删除「${fileName}」？删除后不可恢复。`, '删除文件', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }

  deletingConsoleFilePath.value = file.relativePath
  try {
    const response = await deleteConversationWorkspaceFile(conversationId, file.relativePath)
    if (response.status !== 'success') {
      ElMessage.error(response.msg || '删除失败')
      return
    }
    ElMessage.success(`已删除 ${fileName}`)
    // 删掉的正是当前预览的文件时清空选中，否则预览区会继续渲染一份已不存在的文件。
    // key 取归一化路径：预览缓存的 key 就是 selectedConsoleFilePath 的坐标系（见 selectConsoleFile）。
    const previewKey = normalizeVisiblePath(file.relativePath)
    if (selectedConsoleFilePath.value === previewKey) {
      selectedConsoleFilePath.value = null
    }
    // 缓存也要一并作废，否则重传同名文件后点预览会读回旧内容（理由见 forgetWorkspaceFileCache）
    forgetWorkspaceFileCache(previewKey)
    await loadWorkspaceFiles(conversationId)
  } catch {
    ElMessage.error('文件删除失败，请稍后重试')
  } finally {
    deletingConsoleFilePath.value = null
  }
}

// 面板内补充文件：只把文件落进 uploads（会话工作区），**不**进入消息附件——
// 智能体需要时通过 parse_document/file_list 自行读取。
// 这条链路是现成的：app 侧 uploads 为权威副本，沙箱在执行前按 manifest 增量镜像，
// 而 agent 的文件工具对 uploads 只读，用户是 uploads 唯一合法写入者。
const openWorkspaceUploadPicker = () => {
  if (isWorkspaceFileUploading.value) return
  workspaceUploadInputRef.value?.click()
}

const handleWorkspaceUploadSelected = async (event: Event) => {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return

  const ext = '.' + (file.name.split('.').pop() || '').toLowerCase()
  if (!ACCEPTED_FILE_TYPES.includes(ext)) {
    ElMessage.warning(`仅支持 ${ACCEPTED_FILE_TYPES.join('、')} 格式`)
    return
  }
  if (file.size > MAX_FILE_SIZE) {
    ElMessage.warning('文件大小不能超过 50MB')
    return
  }

  let convId = props.conversationId
  if (!convId) {
    convId = (await props.ensureConversationId?.()) ?? null
    if (!convId) {
      ElMessage.error('创建会话失败，请稍后重试')
      return
    }
    emit('conversationCreated', convId)
  }

  isWorkspaceFileUploading.value = true
  try {
    const res = await uploadFile(convId, file)
    if (res.status !== 'success') {
      ElMessage.error(res.msg || '上传失败')
      return
    }
    ElMessage.success(`已上传 ${res.data.original_filename}`)
    scheduleWorkspaceFilesRefresh()
  } catch {
    ElMessage.error('文件上传失败')
  } finally {
    isWorkspaceFileUploading.value = false
  }
}

// 消息附件 / 交付产物点击后的统一入口：文件不在列表时先刷一次列表，
// 再选中并直接加载内容（不依赖 watch 中的 selectedWorkspaceFile 判断）。
const openFile = async (path: string) => {
  const conversationId = props.conversationId
  if (!conversationId) {
    ElMessage.warning('会话未就绪，无法查看文件')
    return
  }
  const relativePath = normalizeVisiblePath(path)
  if (!relativePath) {
    ElMessage.warning('文件路径无效')
    return
  }
  if (!workspaceFiles.value.some(f => normalizeVisiblePath(f.relative_path || f.path) === relativePath)) {
    await loadWorkspaceFiles(conversationId)
  }
  selectConsoleFile(relativePath)
  emit('update:tab', 'console')
  emit('update:open', true)
  await loadWorkspaceFileContent(conversationId, relativePath)
}

watch(
  () => props.conversationId,
  (nextId, prevId) => {
    if (nextId === prevId) return
    // 会话实际切换由父组件 conversationKey 强制重挂载兜底；这里只处理
    // 「切换到另一个已有会话」时的面板状态重置（null 边界交给重挂载与 loadWorkspaceFiles 清空）。
    if (prevId && nextId) {
      selectedConsoleFilePath.value = null
      selectedConsoleOfficeHtml.value = null
      selectedConsoleOfficePreviewReason.value = null
      loadingOfficePreviewKey.value = null
      consoleFilePreviewMode.value = 'render'
    }
    void loadWorkspaceFiles(nextId)
  },
  { immediate: true }
)

watch(selectedConsoleFilePath, (path) => {
  if (!path || !selectedWorkspaceFile.value) return
  loadWorkspaceFileContent(props.conversationId, path)
})

watch(
  () => [
    selectedConsoleFilePath.value,
    Boolean(selectedConsoleChartSpec.value),
    Boolean(hasSelectedConsoleTable.value),
    selectedConsoleIsMarkdown.value,
    selectedConsoleIsMermaid.value,
    selectedConsoleIsHtml.value,
    selectedConsoleIsYaml.value,
  ] as const,
  () => {
    if (selectedConsoleChartSpec.value) {
      consoleFilePreviewMode.value = 'render'
      return
    }
    if (hasSelectedConsoleTable.value) {
      consoleFilePreviewMode.value = 'table'
      return
    }
    if (selectedConsoleIsMarkdown.value || selectedConsoleIsMermaid.value || selectedConsoleIsHtml.value) {
      consoleFilePreviewMode.value = 'render'
      return
    }
    if (selectedConsoleIsYaml.value) {
      consoleFilePreviewMode.value = 'render'
      return
    }
    consoleFilePreviewMode.value = 'source'
  }
)

defineExpose({
  /** 工具完成后的防抖刷新（列表 + 当前预览内容由各自请求自行触发） */
  scheduleRefresh: scheduleWorkspaceFilesRefresh,
  /** 手动兜底刷新：同时刷列表与当前预览内容 */
  refreshFiles: refreshWorkspaceFiles,
  /** 工具写文件后的预览强制重载（仅当写的是当前预览文件） */
  reloadPreviewIfSelected,
  /** 消息附件 / 交付产物点击后的选中打开 */
  openFile,
})
</script>

<template>
  <ConversationSidePanel
    :open="open"
    :title="sidePanelTitle"
    :subtitle="sidePanelSubtitle"
    :show-trigger="false"
    :show-close="false"
    @open="$emit('update:tab', 'console'); $emit('update:open', true)"
    @close="$emit('update:open', false)"
  >
    <!-- 头部这一行从左到右：标题「工作区」→ 工作区根路径 →「折叠/展开全部」按钮 →（行尾）文件数量。
         根路径原先占着文件树的第一行（/mnt/user-data 文件夹行），上移到标题行后树里不再有
         这一级、同一件事不重复表达，树也少一行高度；一键折叠/展开按钮跟着根路径走——它折叠的
         就是这棵树里的全部目录，与各目录行左侧的箭头属同一族操作，紧挨根路径放语义最直白。
         上传是 uploads 目录专属的写入入口，已下沉到 uploads 目录行（就近绑定）。
         数量徽标贴行尾：pr-[13px] 让它与下方文件树里的数量徽标落在同一条竖线上——头部容器有 px-4(16px)，
         文件树里的数量徽标右边缘距面板右沿 29px（内容区 p-4 16 + 树容器 px-1 4 + 目录区域边框 1 + 滚动容器 px-1 4 + 行 pr-1 4），
         这里补 13px 补齐差值。改文件树的内边距或边框时要同步这一处。 -->
    <template v-if="tab === 'console'" #header-inline>
      <span class="min-w-0 truncate font-mono text-xs font-normal text-slate-400">{{ producedFileTree.root }}</span>
      <button
        v-if="consoleTreeCollapsiblePaths.length"
        type="button"
        class="flex h-6 w-6 shrink-0 items-center justify-center rounded-md border border-slate-200 bg-white text-[11px] leading-none text-slate-500 shadow-sm transition-colors hover:border-slate-300 hover:bg-slate-50 hover:text-slate-700 active:bg-slate-100"
        :title="isConsoleTreeFullyCollapsed ? '展开全部目录' : '折叠全部目录'"
        :aria-label="isConsoleTreeFullyCollapsed ? '展开全部目录' : '折叠全部目录'"
        data-console-tree-toggle="true"
        @click="toggleConsoleTreeCollapsed"
      >
        <i class="fa-solid" :class="isConsoleTreeFullyCollapsed ? 'fa-angles-down' : 'fa-angles-up'"></i>
      </button>
    </template>

    <template v-if="tab === 'console'" #header-extra>
      <div class="flex shrink-0 items-center pr-[13px]">
        <div class="shrink-0 rounded bg-slate-100 px-1.5 py-0.5 text-[10px] leading-none text-slate-500">
          {{ isWorkspaceFilesLoading ? '加载中' : `${producedFiles.length} 个文件` }}
        </div>
      </div>
    </template>

    <template v-if="tab === 'console'">
      <!-- 树的左缩进体系：顶层目录行用 px-1.5（6px）作基准，子层用 consoleTreeIndentStyle(depth)。
           改这里的 px-1.5 时必须同步 CONSOLE_TREE_INDENT_BASE_PX，否则第一级跨度会与后续级不等。
           根路径（/mnt/user-data）那一行已上移到面板头部标题行，这里直接从顶层目录开始。
           目录区域套一圈细边框，把「树」从面板的空白底上框出来：目录行是 7px 高、缩进逐级变化的
           文字行，没有边界时视线容易散到面板空白处；框住之后区域范围一眼可见（也顺带标出
           滚动区的范围）。空树时整块不渲染——留一个空框比不画更难解释。
           树容器仍带 @container：「时间 / 大小」两列的显隐按它的宽度算，与边框无关。 -->
      <div v-if="producedFileTree.directories.length" class="mb-3 px-1 py-1 text-xs @container">
        <div class="max-h-[300px] space-y-0.5 overflow-auto rounded-md border border-slate-200 px-1 py-1">
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
                <i class="fa-regular w-4 shrink-0 text-center text-amber-500" :class="isConsoleDirectoryCollapsed(directory.name) ? 'fa-folder' : 'fa-folder-open'"></i>
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
                    @click="selectConsoleFile(item.node.file.relativePath)"
                  >
                    <!-- 占位与图标必须 shrink-0：文件名的 flex-basis 是 auto，取的是 max-content
                         （长文件名可达 460px+）；只靠 truncate 的话，行内放不下时收缩量会按基础宽度
                         比例摊到所有子项，这个 12px 占位和 16px 图标会被一起挤窄，
                         导致「长文件名那一行」的图标与文字比上下行左移 6px/12px。 -->
                    <span class="w-3 shrink-0"></span>
                    <i class="fa-regular fa-file-lines w-4 shrink-0 text-center text-slate-400"></i>
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
                      @click="selectConsoleFile(item.node.file.relativePath)"
                    >
                      <i class="fa-solid fa-eye"></i>
                    </button>
                    <!-- SQL 查询结果（outputs/query_result、outputs/.tool-offload）不提供下载，
                         防数据泄漏；后端两侧已拦截，这里只是前端侧的纵深防御。 -->
                    <button
                      v-if="isWorkspaceFileDownloadable(item.node.file.relativePath)"
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
                    <i class="fa-regular w-4 shrink-0 text-center text-amber-500" :class="isFileTreeNodeCollapsed(item.node.path) ? 'fa-folder' : 'fa-folder-open'"></i>
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

      <div v-if="selectedConsoleFile" class="rounded-lg border border-slate-100 bg-slate-50 p-3">
        <div class="mb-2 flex items-center justify-between gap-2">
          <div class="min-w-0 truncate font-mono text-xs font-medium text-slate-500">
            {{ selectedConsoleFile?.displayPath }}
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
              v-if="selectedConsoleFile"
              type="button"
              class="flex h-6 w-6 items-center justify-center rounded border border-slate-200 bg-white text-[11px] text-slate-500 transition-colors hover:bg-slate-50 hover:text-slate-700"
              title="复制文件内容（表格模式复制 TSV）"
              data-console-preview-copy-content="true"
              @click="handleCopyWorkspacePreviewContent"
            >
              <i class="fa-regular fa-copy"></i>
            </button>
            <button
              v-if="selectedConsoleFile"
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
              v-if="selectedConsoleFile"
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
              v-if="selectedConsoleFile"
              type="button"
              class="flex h-6 w-6 items-center justify-center rounded border border-slate-200 bg-white text-[11px] text-slate-500 transition-colors hover:bg-slate-50 hover:text-slate-700"
              title="放大预览"
              @click="toggleWorkspaceFilePreviewFullscreen"
            >
              <i class="fa-solid fa-expand"></i>
            </button>
          </div>
        </div>
        <div ref="workspaceFilePreviewRef" class="workspace-file-preview relative bg-white" :class="isWorkspaceFilePreviewFullscreen ? 'h-screen w-screen overflow-auto p-6' : ''">
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
          <div v-else-if="hasSelectedConsoleTable && consoleFilePreviewMode === 'table'" :class="isWorkspaceFilePreviewFullscreen ? 'h-[calc(100vh-48px)] overflow-auto rounded-md border border-slate-100 bg-white' : 'max-h-[46vh] overflow-auto rounded-md border border-slate-100 bg-white'">
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
            v-else-if="selectedConsoleIsPdf && selectedConsoleBinaryBlob?.url"
            :src="selectedConsoleBinaryBlob.url"
            :class="isWorkspaceFilePreviewFullscreen ? 'm-0 h-[calc(100vh-48px)] min-w-0 w-full rounded-md border border-slate-100 bg-white' : 'm-0 h-[46vh] min-w-0 w-full rounded-md border border-slate-100 bg-white'"
          ></iframe>
          <div
            v-else-if="selectedConsoleIsPdf && loadingWorkspaceFilePath === selectedConsoleFilePath"
            :class="isWorkspaceFilePreviewFullscreen ? 'flex h-[calc(100vh-48px)] items-center justify-center text-xs text-slate-400' : 'flex h-[46vh] items-center justify-center text-xs text-slate-400'"
          >PDF 加载中...</div>
          <div
            v-else-if="selectedConsoleIsOfficeFile && selectedConsoleOfficeHtml"
            :class="isWorkspaceFilePreviewFullscreen ? 'h-[calc(100vh-48px)] min-w-0 overflow-auto rounded-md border border-slate-100 bg-white' : 'h-[46vh] min-w-0 overflow-auto rounded-md border border-slate-100 bg-white'"
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
            :class="isWorkspaceFilePreviewFullscreen ? 'flex h-[calc(100vh-48px)] items-center justify-center text-xs text-slate-400' : 'flex h-[46vh] items-center justify-center text-xs text-slate-400'"
          >Office 文件加载中...</div>
          <div
            v-else-if="selectedConsoleIsOfficeFile && selectedConsoleOfficePreviewReason"
            :class="isWorkspaceFilePreviewFullscreen ? 'flex h-[calc(100vh-48px)] flex-col items-center justify-center gap-2 text-xs text-slate-400' : 'flex h-[46vh] flex-col items-center justify-center gap-2 text-xs text-slate-400'"
          >
            <span v-if="selectedConsoleOfficePreviewReason === 'preview_too_large'">文件过大，暂不支持预览，请下载查看。</span>
            <span v-else>当前环境不能预览 Office 文件，请下载查看。</span>
          </div>
          <div
            v-else-if="selectedConsoleIsImage && selectedConsoleBinaryBlob?.url"
            :class="isWorkspaceFilePreviewFullscreen ? 'flex h-[calc(100vh-48px)] items-center justify-center overflow-auto rounded-md border border-slate-100 bg-white p-2' : 'flex h-[46vh] items-center justify-center overflow-auto rounded-md border border-slate-100 bg-white p-2'"
          >
            <img :src="selectedConsoleBinaryBlob.url" :alt="selectedConsoleFilePath || ''" class="max-h-full max-w-full object-contain" />
          </div>
          <div
            v-else-if="selectedConsoleIsImage && loadingWorkspaceFilePath === selectedConsoleFilePath"
            :class="isWorkspaceFilePreviewFullscreen ? 'flex h-[calc(100vh-48px)] items-center justify-center text-xs text-slate-400' : 'flex h-[46vh] items-center justify-center text-xs text-slate-400'"
          >图片加载中...</div>
          <div v-else-if="selectedConsoleBinaryBlob?.error" :class="isWorkspaceFilePreviewFullscreen ? 'flex h-[calc(100vh-48px)] items-center justify-center text-xs text-red-400' : 'flex h-[46vh] items-center justify-center text-xs text-red-400'">文件加载失败，请重试。</div>
          <pre v-else :class="isWorkspaceFilePreviewFullscreen ? 'm-0 h-[calc(100vh-48px)] overflow-auto whitespace-pre-wrap break-words text-xs leading-relaxed text-slate-700' : 'm-0 max-h-[46vh] overflow-auto whitespace-pre-wrap break-words text-xs leading-relaxed text-slate-700'">{{ selectedConsoleFileText || '暂无可预览内容' }}</pre>
        </div>
      </div>
      <div v-else-if="SHOW_CONSOLE_FILE_PLACEHOLDER" class="rounded-lg border border-slate-100 bg-slate-50 p-3 text-xs text-slate-400">
        点击工作区文件查看内容。
      </div>
    </template>

    <template v-else>
      <ToolCallDetailPanel
        :tool-call="activeToolCall"
        :conversation-id="conversationId || ''"
        :format-text="formatText"
      />
    </template>
  </ConversationSidePanel>
  <input
    ref="workspaceUploadInputRef"
    type="file"
    class="hidden"
    :accept="ACCEPTED_FILE_TYPES.join(',')"
    data-workspace-upload-input="true"
    @change="handleWorkspaceUploadSelected"
  />
</template>
