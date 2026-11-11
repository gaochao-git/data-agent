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
import { computed, nextTick, onMounted, onUnmounted, provide, ref, watch } from 'vue'
import type { PropType } from 'vue'
import type { ChatDataTokenUsage } from '../../types'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { SemanticYamlFile, ToolCallInfo } from '../../types'
import ConversationSidePanel from './ConversationSidePanel.vue'
import ToolCallDetailPanel from './ToolCallDetailPanel.vue'
import AgentProcessView from './AgentProcessView.vue'
import RightSidebarTabStrip from './RightSidebarTabStrip.vue'
import FileManagerTab from './FileManagerTab.vue'
import FilePreviewTab from './FilePreviewTab.vue'
import { WorkspaceFilesContextKey } from '../../composables/workspaceFilesContext'
import { useRightSidebarTabs, WORKSPACE_TAB_ID, type RightSidebarTab } from '../../composables/useRightSidebarTabs'
import {
  deleteConversationWorkspaceFile,
  getConversationWorkspaceFileContent,
  getConversationWorkspaceFiles,
  getConversationWorkspaceOfficeHtmlPreview,
  getConversationWorkspaceRawFile,
  getConversationWorkspaceArchive,
  uploadFile,
  type ConversationWorkspaceDirectory,
  type ConversationWorkspaceFile,
  type ConversationWorkspaceUsage,
} from '../../api'
import {
  collapseAllDirectories,
  collectCollapsibleDirectoryPaths,
  DEFAULT_COLLAPSED_CONSOLE_DIRECTORIES,
  expandAllDirectories,
  consoleTreeIndentStyle,
  consoleTreeLeadSlotStyle,
  getFileCount,
  isTreeFullyCollapsed,
  isWorkspaceFileDeletable,
  isWorkspaceDirectoryArchiveDownloadable,
  syncTruncatedTitle,
  isWorkspaceFileDownloadable,
  normalizeVisiblePath,
  buildProducedFileTree,
  buildProducedFiles,
  type ProducedFile,
} from '../../utils/consoleFileTree'
import { evictWorkspaceFileCache } from '../../utils/workspacePreviewCache'
import { IMAGE_PREVIEW_EXTENSIONS, IMAGE_PREVIEW_MIME, OFFICE_HTML_PREVIEW_EXTENSIONS, isBinaryPreviewPath } from '../../utils'
import { formatToolTableCell, parseChartSpecValue, replaceVisiblePathsForDisplay } from '../../utils/messageArtifacts'
import ConversationTokenUsage from './ConversationTokenUsage.vue'
import { extractChangedFilePathsFromResult, extractWrittenFilePathCandidates } from '../../utils/toolDisplay'
import { ACCEPTED_FILE_TYPES, MAX_FILE_SIZE } from '../../utils/uploadLimits'

const props = defineProps({
  conversationId: { type: String as PropType<string | null>, default: null },
  open: { type: Boolean, default: false },
  activeToolCall: { type: Object as PropType<ToolCallInfo | null>, default: null },
  /** 按 tool_call_id 解析工具调用（tab 内容按 id 取，不依赖父级当前选中） */
  resolveToolCall: { type: Function as PropType<(toolCallId: string) => ToolCallInfo | null>, default: null },
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
  conversationCreated: [conversationId: string]
  /** 工作区 yaml 一键导入语义模型：文件内容已在面板内读取完毕 */
  importSemanticYaml: [files: SemanticYamlFile[]]
  /** 完整过程区内层工具再次选中：切到对应工具详情 */
  selectTool: [toolCall: ToolCallInfo]
}>()

// 右侧栏 tab 状态由 useRightSidebarTabs 唯一持有（列表/激活/复用/关闭/滚动记忆）
const {
  tabs: panelTabs,
  activeTabId: activePanelTabId,
  activeTab: activePanelTab,
  isWorkspaceTabActive,
  isToolTabActive,
  displayToolTab,
  selectTab,
  activateTab,
  activateWorkspace,
  closeTab,
  rememberScroll,
  scrollTopFor,
  resetTabs,
} = useRightSidebarTabs()

const sidePanelRef = ref<InstanceType<typeof ConversationSidePanel> | null>(null)

const toolCallForTab = (tab: RightSidebarTab | null | undefined): ToolCallInfo | null => {
  const toolCallId = tab?.toolCallId
  if (!toolCallId) return null
  const resolved = props.resolveToolCall?.(toolCallId)
  if (resolved) return resolved
  return props.activeToolCall?.tool_call_id === toolCallId ? props.activeToolCall : null
}

const displayToolCall = computed(() => toolCallForTab(displayToolTab.value))
const displayTabIsAgentProcess = computed(() => displayToolTab.value?.kind === 'agentProcess')
const displayTabIsToolDetail = computed(() => displayToolTab.value?.kind === 'tool')
// 每个子智能体 tab 各渲染一份过程视图（v-for 常驻）：多开时内容不串、展开态与位置各自保留
const agentProcessTabs = computed(() => panelTabs.value.filter(tab => tab.kind === 'agentProcess'))

/** 选中 task 调用时，工具视图额外展示「完整过程」（与主对话共用同一套消息/工具组件） */
const subagentTaskCall = computed(() => {
  const toolCall = displayToolCall.value
  return toolCall?.tool_name === 'task' && (toolCall.subagentTrail?.length ?? 0) > 0 ? toolCall : null
})

/** 切换 tab：保存旧 tab 滚动位置、按需恢复新 tab 内容/位置（滚动读写由本组件持有）。 */
// 复用主对话的上下文占用组件：有计量明细直接用明细；旧会话/首轮前用快照兜底，保证点击能打开同样面版
const subagentUsageForPanel = computed<ChatDataTokenUsage | null>(() => {
  const detail = subagentTaskCall.value?.subagentTokenUsageDetail
  if (detail) return detail
  const snapshot = subagentTaskCall.value?.subagentContextUsage
  if (!snapshot) return null
  return {
    used: snapshot.used_tokens,
    total: snapshot.context_window ?? snapshot.threshold_tokens,
    percentage: snapshot.percent,
    current_input_tokens: snapshot.used_tokens,
    compression_trigger_tokens: snapshot.threshold_tokens,
    context_window: snapshot.context_window,
    estimated: true,
  }
})

const selectPanelTab = (tabId: string) => {
  const previousTabId = activePanelTabId.value
  if (tabId !== previousTabId) rememberScroll(previousTabId, sidePanelRef.value?.getBodyScrollTop() ?? 0)
  const tab = panelTabs.value.find(item => item.id === tabId)
  // 文件 tab 各自持有路径：切回某个文件 tab 时恢复它的内容
  if (tab?.kind === 'file' && tab.path && tab.path !== selectedConsoleFilePath.value) {
    selectConsoleFile(tab.path)
    void loadWorkspaceFileContent(props.conversationId, tab.path)
  }
  selectTab(tabId)
  emit('update:open', true)
  void nextTick(() => {
    const sidePanel = sidePanelRef.value
    if (!sidePanel) return
    const savedTop = scrollTopFor(tabId)
    const followActive = isToolTabActive.value && toolViewMode.value === 'agent' && Boolean(subagentTaskCall.value)
    // 首次打开跟随型 tab → 停在最新；切回已有 tab → 恢复上次位置
    if (savedTop === undefined && followActive) {
      sidePanel.scrollBodyToBottom()
      return
    }
    sidePanel.setBodyScrollTop(savedTop ?? 0)
  })
}

const closePanelTab = (tabId: string) => {
  const fallbackId = closeTab(tabId)
  // 关闭当前 tab 时回到相邻 tab：file tab 需要恢复自己的预览内容
  if (fallbackId) selectPanelTab(fallbackId)
}

const openWorkspacePanelTab = () => {
  void activateWorkspace()
  selectPanelTab(WORKSPACE_TAB_ID)
}

const openToolPanelTab = (toolCall: ToolCallInfo) => {
  const isTask = toolCall.tool_name === 'task' && (toolCall.subagentTrail?.length ?? 0) > 0
  const subagentType = typeof toolCall.args?.subagent_type === 'string' ? toolCall.args.subagent_type.trim() : ''
  activateTab({
    id: `${isTask ? 'agent' : 'tool'}:${toolCall.tool_call_id}`,
    kind: isTask ? 'agentProcess' : 'tool',
    title: isTask ? (subagentType ? `${subagentType} 子智能体` : '子智能体') : '工具详情',
    toolCallId: toolCall.tool_call_id,
  })
  selectPanelTab(`${isTask ? 'agent' : 'tool'}:${toolCall.tool_call_id}`)
}

const openFilePanelTab = (path: string) => {
  const fileName = path.split('/').pop() || path
  activateTab({ id: `file:${path}`, kind: 'file', title: fileName, path })
  selectPanelTab(`file:${path}`)
}

/**
 * 工具详情 tab 的展示方式（仅子智能体 task 调用可选）：
 * - agent：智能体模式，按聊天记录复用主对话消息组件；
 * - generic：通用模式，与普通工具一致，只看参数与结果。
 * 两种模式互斥渲染，不堆叠。
 */
const toolViewMode = ref<'agent' | 'generic'>('agent')

watch(() => displayToolCall.value?.tool_call_id, () => {
  // 每次选中新的子智能体任务默认回到聊天方式；普通工具不涉及该状态
  if (subagentTaskCall.value) toolViewMode.value = 'agent'
})

const selectedConsoleFilePath = ref<string | null>(null)
// 默认只收起 uploads / workspace，outputs 保持展开——理由见 consoleFileTree.ts 里的常量注释。
const collapsedConsoleDirectories = ref<Set<string>>(new Set(DEFAULT_COLLAPSED_CONSOLE_DIRECTORIES))
const consoleFilePreviewMode = ref<'render' | 'table' | 'source'>('render')
const workspaceFiles = ref<ConversationWorkspaceFile[]>([])
const workspaceDirectories = ref<ConversationWorkspaceDirectory[]>([])
const workspaceUsage = ref<ConversationWorkspaceUsage>({ used_bytes: 0, limit_bytes: 0, limit_mb: 0, percentage: 0 })
const workspaceFileContents = ref<Record<string, string>>({})
const workspaceFileBlobs = ref<Record<string, { url: string; arrayBuffer: ArrayBuffer; ext: string; mime: string; error?: boolean }>>({})
const workspaceFilePreviewRef = ref<HTMLElement | null>(null)
const isWorkspaceFilePreviewFullscreen = ref(false)
const selectedConsoleOfficeHtml = ref<string | null>(null)
const selectedConsoleOfficePreviewReason = ref<string | null>(null)
// Office 预览按路径缓存：切回已打开的文件 tab 时直接复用，不再重新请求 HTML
const officePreviewCache = ref<Record<string, { html: string | null; reason: string | null }>>({})
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
const producedFileTree = computed(() => buildProducedFileTree(producedFiles.value, workspaceDirectories.value))


// 工作区 tab 不再在头部重复显示根路径——文件树根节点已经写着同一个路径，
// 需要恢复时把下面的 '' 换回 producedFileTree.value.root


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

// 二级及更深目录首次出现时默认折叠：展开 workspace 只显示直接子目录，
// 不会把 semantic/月报 等整棵子树一次性摊开。用户手动展开后不再自动收起。
const knownNestedDirectories = new Set<string>()
watch(
  producedFileTree,
  (tree) => {
    const nestedPaths = collectCollapsibleDirectoryPaths(tree.directories).filter(path => path.includes('/'))
    const next = new Set(collapsedConsoleDirectories.value)
    let changed = false
    nestedPaths.forEach((path) => {
      if (knownNestedDirectories.has(path)) return
      knownNestedDirectories.add(path)
      next.add(path)
      changed = true
    })
    if (changed) collapsedConsoleDirectories.value = next
  },
  { immediate: true },
)

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

  const cachedOffice = officePreviewCache.value[normalizedPath]
  selectedConsoleOfficeHtml.value = cachedOffice?.html ?? null
  selectedConsoleOfficePreviewReason.value = cachedOffice?.reason ?? null
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

// 面板开关：关闭前记住当前 tab 的滚动位置，重新打开时恢复（关闭会卸载 body，滚动位置否则归零）
watch(() => props.open, (open) => {
  if (!open) {
    rememberScroll(activePanelTabId.value, sidePanelRef.value?.getBodyScrollTop() ?? 0)
    return
  }
  void nextTick(() => {
    const sidePanel = sidePanelRef.value
    if (!sidePanel) return
    const followActive = isToolTabActive.value && toolViewMode.value === 'agent' && Boolean(subagentTaskCall.value)
    const savedTop = scrollTopFor(activePanelTabId.value)
    if (savedTop === undefined && followActive) {
      sidePanel.scrollBodyToBottom()
      return
    }
    sidePanel.setBodyScrollTop(savedTop ?? 0)
  })
})

onMounted(() => {
  document.addEventListener('fullscreenchange', syncWorkspaceFilePreviewFullscreen)
})

onUnmounted(() => {
  document.removeEventListener('fullscreenchange', syncWorkspaceFilePreviewFullscreen)
})

const loadWorkspaceFiles = async (conversationId: string | null | undefined) => {
  if (!conversationId) {
    workspaceFiles.value = []
    workspaceDirectories.value = []
    workspaceUsage.value = { used_bytes: 0, limit_bytes: 0, limit_mb: 0, percentage: 0 }
    Object.values(workspaceFileBlobs.value).forEach(b => { if (b.url) URL.revokeObjectURL(b.url) })
    workspaceFileBlobs.value = {}
    workspaceFileContents.value = {}
    officePreviewCache.value = {}
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
    workspaceDirectories.value = response.data?.directories || []
    if (response.data?.usage) workspaceUsage.value = response.data.usage
  } catch (err) {
    if (conversationId !== props.conversationId) return
    workspaceFiles.value = []
    workspaceDirectories.value = []
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
  if (!options.forceReload && OFFICE_HTML_PREVIEW_EXTENSIONS.has(ext)) {
    const cachedOffice = officePreviewCache.value[path]
    if (cachedOffice) {
      selectedConsoleOfficeHtml.value = cachedOffice.html
      selectedConsoleOfficePreviewReason.value = cachedOffice.reason
      return
    }
  }
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
    officePreviewCache.value = {
      ...officePreviewCache.value,
      [path]: { html: selectedConsoleOfficeHtml.value, reason: selectedConsoleOfficePreviewReason.value },
    }
  } catch (err) {
    if (!isCurrentWorkspaceFilePreviewRequest(conversationId, path, requestSequence)) return
    if (!forceReload || !selectedConsoleOfficeHtml.value) {
      selectedConsoleOfficeHtml.value = null
      selectedConsoleOfficePreviewReason.value = 'preview_failed'
      officePreviewCache.value = {
        ...officePreviewCache.value,
        [path]: { html: null, reason: 'preview_failed' },
      }
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
// PDF 只做工作区预览：用 Chrome PDF Viewer 参数隐藏下载/打印/侧栏等原生工具条。
// 注意：部分 Chromium 版本会忽略部分参数，前端另外隐藏了本面板的操作按钮。
const selectedConsolePdfUrl = computed(() => {
  const url = selectedConsoleBinaryBlob.value?.url || ''
  return url ? `${url}#toolbar=0&navpanes=0&scrollbar=0&statusbar=0&messages=0&view=FitH` : ''
})
const isPdfFilePath = (path: string | null | undefined) => String(path || '').toLowerCase().endsWith('.pdf')
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
const workspaceFilePreviewHeightClass = computed(() => {
  if (isWorkspaceFilePreviewFullscreen.value) return 'h-[calc(100vh-48px)]'
  return activePanelTab.value.kind === 'file' ? 'h-full' : 'h-[46vh]'
})
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

// 预览头部路径：左侧固定前缀（/mnt/user-data/...）截掉不占宽度，优先露出有区分度的
// 文件名与上一层目录；完整路径放到 title/hover
// 预览头部路径：参照 DSH PathLabel —— 目录弱化成 tertiary、文件名保持主色；
// 放不下时容器右对齐裁掉左侧（固定前缀先被裁），左侧用 28px 渐变淡出代替省略号。
const selectedConsolePathParts = computed(() => {
  const full = selectedConsoleFile.value?.displayPath || ''
  const root = producedFileTree.value.root
  const relative = root && full.startsWith(`${root}/`) ? full.slice(root.length + 1) : full
  const index = relative.lastIndexOf('/')
  return index >= 0
    ? { directory: relative.slice(0, index + 1), name: relative.slice(index + 1) }
    : { directory: '', name: relative }
})
const previewPathRef = ref<HTMLElement | null>(null)
const previewPathInnerRef = ref<HTMLElement | null>(null)
const previewPathClipped = ref(false)
let previewPathObserver: ResizeObserver | null = null
const syncPreviewPathClip = () => {
  const outer = previewPathRef.value
  const inner = previewPathInnerRef.value
  if (outer && inner) previewPathClipped.value = inner.offsetWidth > outer.clientWidth
}
onMounted(() => {
  void nextTick(syncPreviewPathClip)
  if (typeof ResizeObserver === 'undefined') return
  previewPathObserver = new ResizeObserver(syncPreviewPathClip)
  if (previewPathRef.value) previewPathObserver.observe(previewPathRef.value)
  if (previewPathInnerRef.value) previewPathObserver.observe(previewPathInnerRef.value)
})
onUnmounted(() => { previewPathObserver?.disconnect() })
watch(() => selectedConsoleFile.value?.displayPath, () => { void nextTick(syncPreviewPathClip) })

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
  openFilePanelTab(relativePath)
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
      knownNestedDirectories.clear()
      collapsedConsoleDirectories.value = new Set(DEFAULT_COLLAPSED_CONSOLE_DIRECTORIES)
      // 会话切换：面板 tab 回到工作区根，不把上个会话的工具/文件 tab 带过来
      resetTabs()
      officePreviewCache.value = {}
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

provide(WorkspaceFilesContextKey, {
  activePanelTab,
  canImportSemanticModel: computed(() => props.canImportSemanticModel),
  consoleTreeCollapsiblePaths,
  consoleTreeIndentStyle,
  consoleTreeLeadSlotStyle,
  deletingConsoleFilePath,
  getFileCount,
  workspaceUsage,
  handleDeleteConsoleFile,
  handleDownloadConsoleDirectoryArchive,
  handleDownloadConsoleFile,
  handleImportSemanticYaml,
  isConsoleArchiveDownloading,
  isConsoleDirectoryCollapsed,
  isConsoleTreeFullyCollapsed,
  isFileTreeNodeCollapsed,
  isGovernanceYaml,
  isPdfFilePath,
  isWorkspaceDirectoryArchiveDownloadable,
  isWorkspaceFileDeletable,
  isWorkspaceFileDownloadable,
  importingSemanticFilePath,
  isWorkspaceFileUploading,
  isWorkspaceFilesLoading,
  openFile,
  openWorkspaceUploadPicker,
  producedFileTree,
  producedFiles,
  runBusy: computed(() => props.runBusy),
  selectedConsoleFilePath,
  SHOW_CONSOLE_EMPTY_STATE,
  syncTruncatedTitle,
  toggleConsoleDirectory,
  toggleConsoleTreeCollapsed,
  conversationId: computed(() => props.conversationId),
  consoleFilePreviewMode,
  formatToolTableCell,
  handleCopyWorkspacePreviewContent,
  handleCopyWorkspacePreviewPath,
  hasSelectedConsoleTable,
  isWorkspaceFilePreviewFullscreen,
  isWorkspaceRefreshBusy,
  loadingOfficePreviewKey,
  loadingWorkspaceFilePath,
  previewPathClipped,
  previewPathInnerRef,
  previewPathRef,
  refreshWorkspaceFiles,
  selectedConsoleBinaryBlob,
  selectedConsoleChartSpec,
  selectedConsoleFile,
  selectedConsoleFileText,
  selectedConsoleHtmlDocument,
  selectedConsoleIsHtml,
  selectedConsoleIsImage,
  selectedConsoleIsMarkdown,
  selectedConsoleIsMermaid,
  selectedConsoleIsOfficeFile,
  selectedConsoleIsPdf,
  selectedConsoleIsYaml,
  selectedConsoleOfficeHtml,
  selectedConsoleOfficePreviewReason,
  selectedConsolePathParts,
  selectedConsolePdfUrl,
  selectedConsolePreviewRows,
  selectedConsoleTableColumns,
  SHOW_CONSOLE_FILE_PLACEHOLDER,
  toggleWorkspaceFilePreviewFullscreen,
  workspaceFilePreviewHeightClass,
  workspaceFilePreviewRef,
})

defineExpose({
  /** 工具完成后的防抖刷新（列表 + 当前预览内容由各自请求自行触发） */
  scheduleRefresh: scheduleWorkspaceFilesRefresh,
  /** 主对话点击工具卡：打开/激活对应工具详情或子智能体 tab */
  openTool: openToolPanelTab,
  /** 右上角面板入口：回到工作区 tab */
  openWorkspace: openWorkspacePanelTab,
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
    ref="sidePanelRef"
    :open="open"
    :auto-follow="isToolTabActive && toolViewMode === 'agent' && Boolean(subagentTaskCall)"
    :fill-body="isWorkspaceTabActive"
    :show-subheader="isToolTabActive && Boolean(subagentTaskCall)"
    :show-trigger="false"
    :show-close="false"
    @open="$emit('update:open', true)"
    @close="$emit('update:open', false)"
  >
    <!-- 面板 tab 条：工作区常驻，工具详情 / 子智能体过程 / 文件各占一个 tab；
         同对象再次打开只激活已有 tab，不替换其他内容。 -->
    <template #tabs>
      <RightSidebarTabStrip
        :tabs="panelTabs"
        :active-tab-id="activePanelTabId"
        @select="selectPanelTab"
        @close="closePanelTab"
      />
    </template>

    <template #subheader>
      <div v-if="isToolTabActive && subagentTaskCall" class="flex w-full items-center justify-between gap-2">
        <ConversationTokenUsage
          v-if="subagentUsageForPanel"
          :usage="subagentUsageForPanel"
          :messages="[]"
          compact
          data-subagent-usage-trigger
        />
        <span
          v-if="subagentTaskCall.subagentCompactions?.length"
          class="shrink-0 cursor-default rounded-full bg-slate-100 px-1.5 py-0.5 text-[10px] leading-none text-slate-500 tabular-nums"
          data-subagent-compactions
          :title="subagentTaskCall.subagentCompactions.map(item => `${item.timestamp || ''} ${item.estimated_tokens_before ?? 0} → ${item.estimated_tokens_after ?? 0} tokens，压缩 ${item.compressed_messages ?? 0} 条`).join('\n')"
        >已压缩 {{ subagentTaskCall.subagentCompactions.length }} 次</span>
        <div class="inline-flex rounded-lg border border-slate-200 bg-slate-50 p-0.5 text-[11px]">
          <button
            type="button"
            class="inline-flex items-center gap-1 rounded-md px-1.5 py-1 transition-colors"
            :class="toolViewMode === 'agent' ? 'bg-white text-[#1f82d9] shadow-sm' : 'text-slate-500 hover:text-slate-700'"
            title="聊天模式：按聊天方式查看子智能体过程"
            @click="toolViewMode = 'agent'"
          >
            <i class="fa-solid fa-robot text-[10px]"></i>
            <span>聊天</span>
          </button>
          <button
            type="button"
            class="inline-flex items-center gap-1 rounded-md px-1.5 py-1 transition-colors"
            :class="toolViewMode === 'generic' ? 'bg-white text-[#1f82d9] shadow-sm' : 'text-slate-500 hover:text-slate-700'"
            title="工具模式：与普通工具一致，只查看参数与结果"
            @click="toolViewMode = 'generic'"
          >
            <i class="fa-solid fa-wrench text-[10px]"></i>
            <span>工具</span>
          </button>
        </div>
      </div>
    </template>

    <div v-show="isWorkspaceTabActive" class="flex min-h-0 flex-1 flex-col">
      <!-- 文件管理 tab 自己的工具条：根路径 / 一键折叠 / 文件数量。
           这类信息属于「文件管理」，不能放进 tab 条（会和 tab 胶囊挤在一起）。 -->
      <FileManagerTab />
      <FilePreviewTab />
    </div>

    <div v-show="isToolTabActive" class="min-h-0">
      <!-- 每个子智能体 tab 一份常驻过程视图：多开/点内层工具/切文件 tab 都不卸载 -->
      <AgentProcessView
        v-for="processTab in agentProcessTabs"
        :key="processTab.id"
        v-show="displayToolTab?.id === processTab.id && toolViewMode === 'agent'"
        :tool-call="toolCallForTab(processTab) as ToolCallInfo"
        :conversation-id="conversationId"
        :active-tool-call-id="displayToolCall?.tool_call_id || null"
        class="mb-3"
        @select-tool="emit('selectTool', $event)"
      />
      <!-- 注意：这里必须用 v-if 而不是 v-show——DetailPanel 根节点自带 v-if，
           v-show 无法可靠作用到其根元素，智能体模式下会漏出参数卡片 -->
      <ToolCallDetailPanel
        v-if="displayToolCall && (displayTabIsToolDetail || (displayTabIsAgentProcess && toolViewMode === 'generic'))"
        :tool-call="displayToolCall"
        :conversation-id="conversationId || ''"
        :format-text="formatText"
      />
    </div>
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
