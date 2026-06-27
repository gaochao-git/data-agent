<script setup lang="ts">
/**
 * 智能分析独立会话页
 * 左侧展示流式消息与工具调用，右侧展示当前工具结果/分析产物预览。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, toRef, watch } from 'vue'
import { ElMessage } from 'element-plus'
import MarkdownIt from 'markdown-it'
import type { ChatDataModelOption, ChatDataTokenUsage, ChatDataTokenUsageBreakdown, DashboardSpecPayload, DataSource, ExecutionTargetOption, LangGraphMessage, Message, MessageSegment, SemanticConfigSelectionInfo, SemanticConfigSelectRequiredInfo, SkillInfo, TaskStageInfo, ToolCallInfo } from '../types'
import { useSqlGenerator } from '../composables/useSqlGenerator'
import { useConversationHistory } from '../composables/useConversationHistory'
import { useDeepThinking } from '../composables/useDeepThinking'
import { createDashboardFromSpec, getChatDataConfig, getDataAnalyzeWorkspaceFileContent, getDataAnalyzeWorkspaceFiles, getSkills, importDataAnalyzeWorkspaceSkill } from '../api'
import type { Dashboard } from '../api/dashboard'
import type { DataAnalyzeWorkspaceFile } from '../api'
import ToolCallList from './ToolCallList.vue'
import QuickQueries from './QuickQueries.vue'
import ChartSpecRenderer from './ChartSpecRenderer.vue'
import { normalizeChartType } from '../chartTypes'
import SkillManager from './SkillManager.vue'
import { hideSemanticConfigSelectionPayloads } from '../utils'

interface Props {
  datasource?: DataSource | null
  conversationId?: string | null
  conversationAgentId?: string
  selectedDatasourceId?: string
  semanticConfigId?: string | null
  semanticConfigSelection?: SemanticConfigSelectionInfo | null
  ensureConversationId?: (agentId?: string) => Promise<string | null>
  executionTargets?: ExecutionTargetOption[]
  executionTargetMultiple?: boolean
  selectedMemberDatasourceIds?: string[]
}

const props = withDefaults(defineProps<Props>(), {
  datasource: null,
  semanticConfigId: null,
  semanticConfigSelection: null,
  executionTargets: () => [],
  executionTargetMultiple: true,
  selectedMemberDatasourceIds: () => [],
})

const emit = defineEmits<{
  firstMessageComplete: [conversationId: string]
  agentChange: [agentId: 'chat_data' | 'chat_data_data_analyze']
  'update:selectedMemberDatasourceIds': [value: string[]]
  dashboardCreated: [dashboard: Dashboard]
  semanticConfigSelected: [payload: SemanticConfigSelectionInfo]
}>()

const md = new MarkdownIt({ breaks: true, html: false, linkify: true })
const { generateSqlStream, stopGeneration } = useSqlGenerator()

const dataAnalyzeEnabled = ref(false)
const agentOptions = computed(() => [
  { id: 'chat_data', label: '问数', disabled: false },
  { id: 'chat_data_data_analyze', label: '分析', disabled: !dataAnalyzeEnabled.value },
] as const)
const selectedAgent = ref<'chat_data' | 'chat_data_data_analyze'>(
  'chat_data'
)
const isAgentSelectorOpen = ref(false)
const agentSelectorRef = ref<HTMLElement | null>(null)
const recommendationContentType = computed<'skills' | 'queries'>(() =>
  selectedAgent.value === 'chat_data_data_analyze' ? 'skills' : 'queries'
)
const modelOptions = ref<ChatDataModelOption[]>([])
const selectedModel = ref('')
const { enableDeepThinking } = useDeepThinking()

const messages = ref<Message[]>([])
const input = ref('')
const inputFocused = ref(false)
const isGenerating = ref(false)
const tokenUsage = ref<ChatDataTokenUsage | null>(null)
const tokenUsagePanelOpen = ref(false)
const tokenUsageSort = ref<'round' | 'desc' | 'asc'>('round')
const expandedTokenMessageKeys = ref<Set<string>>(new Set())
const collapsedReasoningStageKeys = ref<Set<string>>(new Set())
const hasLocalConversationStarted = ref(false)
const chatContainerRef = ref<HTMLDivElement | null>(null)
const textareaRef = ref<HTMLTextAreaElement | null>(null)
const isNearBottom = ref(true)
const formatTokenUsageDisplay = (usage: ChatDataTokenUsage | null) => {
  if (!usage || !Number.isFinite(usage.used) || !Number.isFinite(usage.total) || usage.total <= 0) {
    return ''
  }
  const current = usage
  const formatValue = (value: number) => {
    if (value >= 1000000) {
      const scaled = value / 1000000
      return `${scaled >= 100 ? scaled.toFixed(0) : scaled.toFixed(1).replace(/\.0$/, '')}m`
    }
    if (value >= 1000) {
      const scaled = value / 1000
      return `${scaled >= 100 ? scaled.toFixed(0) : scaled.toFixed(1).replace(/\.0$/, '')}k`
    }
    return String(Math.round(value))
  }
  return `${current.estimated ? '约 ' : ''}${formatValue(current.used)} / ${formatValue(current.total)}`
}

const tokenUsageDisplay = computed(() => formatTokenUsageDisplay(tokenUsage.value))
const tokenUsageBreakdown = computed(() => {
  const usage = tokenUsage.value
  if (!usage?.breakdown?.length) return []
  return usage.breakdown
    .filter(item => Number.isFinite(item.tokens) && item.tokens !== 0)
    .map(item => ({
      ...item,
      percentage: usage.used > 0 ? Math.round((item.tokens / usage.used) * 1000) / 10 : 0,
    }))
})

const tokenUsageDetailRows = computed(() => {
  const usage = tokenUsage.value
  if (!usage) return []
  const rows = [
    { label: '当前上下文', value: usage.current_input_tokens ?? usage.input_tokens ?? usage.used },
    { label: '总上下文', value: usage.total },
  ]
  return rows.filter(row => Number.isFinite(row.value))
})

type TokenUsageRow = {
  key: string
  label: string
  tokens: number
  percentage: number
  description?: string
  content?: string
  role?: string
  round?: number
  messageIndex?: number
  children?: TokenUsageRow[]
}

type AnalysisSubmitOptions = {
  semanticConfigIdOverride?: string | null
  semanticConfigSelection?: SemanticConfigSelectionInfo | null
  assistantMessageId?: string
  appendUserMessage?: boolean
}

const toTokenUsageRow = (item: ChatDataTokenUsageBreakdown, labelPrefix = '', keyPrefix = ''): TokenUsageRow => ({
  key: keyPrefix ? `${keyPrefix}_${item.key}` : item.key,
  label: labelPrefix ? `${labelPrefix} · ${item.label}` : item.label,
  tokens: item.tokens,
  percentage: tokenUsage.value?.used ? Math.round((item.tokens / tokenUsage.value.used) * 1000) / 10 : 0,
  description: item.description,
  content: item.content || item.description,
  role: item.role,
  round: item.round,
  messageIndex: item.message_index,
})

const tokenUsageMessageRows = computed(() => {
  const rows: TokenUsageRow[] = []
  for (const item of tokenUsageBreakdown.value) {
    if (!item.children?.length) continue
    if (item.key === 'tool_schema') {
      rows.push({ ...toTokenUsageRow(item, '', item.key), messageIndex: rows.length, children: item.children.map(child => toTokenUsageRow(child, item.label, item.key)) })
      continue
    }
    for (const child of item.children || []) {
      if (!Number.isFinite(child.tokens) || child.tokens === 0) continue
      rows.push({ ...toTokenUsageRow(child, item.label, item.key), messageIndex: rows.length })
    }
  }
  return rows.sort((a, b) => {
    if (tokenUsageSort.value === 'desc') return b.tokens - a.tokens
    if (tokenUsageSort.value === 'asc') return a.tokens - b.tokens
    return (a.messageIndex ?? 0) - (b.messageIndex ?? 0)
  })
})

const toggleTokenMessage = (key: string) => {
  const next = new Set(expandedTokenMessageKeys.value)
  if (next.has(key)) {
    next.delete(key)
  } else {
    next.add(key)
  }
  expandedTokenMessageKeys.value = next
}

const resetTokenUsagePanel = () => {
  tokenUsage.value = null
  tokenUsagePanelOpen.value = false
  expandedTokenMessageKeys.value = new Set()
}

const getReasoningStageKey = (messageId: string, stageIndex: number) => `${messageId}:${stageIndex}`

const isReasoningStageCollapsed = (message: Message, stageIndex: number): boolean => {
  const stage = message.taskStages?.[stageIndex]
  if (stage?.source !== 'reasoning') return false
  if (stage.status === 'running' && message.isStreaming) return false
  return collapsedReasoningStageKeys.value.has(getReasoningStageKey(message.id, stageIndex))
}

const toggleReasoningStage = (message: Message, stageIndex: number) => {
  const stage = message.taskStages?.[stageIndex]
  if (stage?.source !== 'reasoning') return
  const key = getReasoningStageKey(message.id, stageIndex)
  const next = new Set(collapsedReasoningStageKeys.value)
  if (next.has(key)) {
    next.delete(key)
  } else {
    next.add(key)
  }
  collapsedReasoningStageKeys.value = next
}

const toolCallsMap = ref<Map<string, ToolCallInfo>>(new Map())
const activeToolCallId = ref<string | null>(null)
const sidePanelOpen = ref(false)
const sidePanelTab = ref<'console' | 'tool'>('console')
const selectedConsoleFilePath = ref<string | null>(null)
const selectedConsoleDirectoryPath = ref<string | null>(null)
const collapsedConsoleDirectories = ref<Set<string>>(new Set(['uploads', 'runtime-skills']))
const consoleFilePreviewMode = ref<'render' | 'table' | 'source'>('render')
const workspaceFiles = ref<DataAnalyzeWorkspaceFile[]>([])
const workspaceFileContents = ref<Record<string, string>>({})
const isWorkspaceFilesLoading = ref(false)
const loadingWorkspaceFilePath = ref<string | null>(null)
const workspaceFilesRefreshTimer = ref<number | null>(null)
const selectedSkill = ref<SkillInfo | null>(null)
const commandSkills = ref<SkillInfo[]>([])
const isCommandSkillLoading = ref(false)
const isSkillCommandOpen = ref(false)
const isSkillManagerOpen = ref(false)
const importedSkillIdForManager = ref<string | null>(null)
const importingWorkspaceSkillPath = ref<string | null>(null)
const highlightedSkillIndex = ref(0)
const activeAssistantMessageId = ref<string | null>(null)
const localConversationId = ref<string | null>(props.conversationId || null)
const conversationIdRef = toRef(props, 'conversationId')
const creatingDashboardToolCallId = ref<string | null>(null)
const createdDashboardByToolId = ref<Record<string, Dashboard>>({})

const selectedDatasource = computed(() => props.selectedDatasourceId || props.datasource?.id || '')
const selectedMemberDatasourceIds = computed({
  get: () => props.selectedMemberDatasourceIds,
  set: (value: string[]) => emit('update:selectedMemberDatasourceIds', props.executionTargetMultiple ? value : value.slice(0, 1)),
})
const selectedModelValue = computed(() => modelOptions.value.find(m => m.id === selectedModel.value)?.value || selectedModel.value || null)
const selectedSkillTitle = computed(() => selectedSkill.value?.title || selectedSkill.value?.name || '')
const selectedAgentLabel = computed(() => agentOptions.value.find(agent => agent.id === selectedAgent.value)?.label || '问数')
const buildSkillRequestText = (text: string, skillName: string) => {
  const normalizedText = String(text || '').trim()
  return skillName ? `使用「${skillName}」技能回答：${normalizedText}` : normalizedText
}
const skillCommandKeyword = computed(() => {
  if (selectedAgent.value !== 'chat_data_data_analyze') return null
  const match = input.value.match(/^\/([^\s/]*)/)
  return match ? (match[1] || '').toLowerCase() : null
})
const filteredCommandSkills = computed(() => {
  const keyword = skillCommandKeyword.value
  if (keyword === null) return []
  const result = commandSkills.value.filter((skill) => {
    if (!keyword) return true
    const text = `${skill.name} ${skill.title || ''} ${skill.description || ''}`.toLowerCase()
    return text.includes(keyword)
  })
  return result.slice(0, 8)
})
const showSkillCommand = computed(() => isSkillCommandOpen.value && skillCommandKeyword.value !== null)

const findToolCallById = (id: string | null) => {
  if (!id) return null
  const fromMap = toolCallsMap.value.get(id)
  if (fromMap) return fromMap
  for (const message of messages.value) {
    const matched = message.toolCalls?.find(item => item.tool_call_id === id)
    if (matched) return matched
  }
  return null
}

const activeToolCall = computed(() => {
  const id = activeToolCallId.value
  return findToolCallById(id)
})

const allToolCalls = computed(() => Array.from(toolCallsMap.value.values()))

const outputArtifactFiles = computed(() => workspaceFiles.value
  .filter(file => normalizeVisiblePath(file.relative_path || file.path).startsWith('outputs/'))
  .filter(file => typeof file.artifact_type === 'string' && file.artifact_type.trim())
  .slice()
  .sort((a, b) => Number(b.mtime || 0) - Number(a.mtime || 0)))

const outputArtifactTools = computed<ToolCallInfo[]>(() => outputArtifactFiles.value.map((file) => {
  const filePath = file.path || file.relative_path
  const payload: Record<string, unknown> = {
    file_path: filePath,
    artifact_type: file.artifact_type,
    renderer: file.renderer,
    artifact_payload: file.artifact_payload,
  }
  if (file.chart_spec !== undefined) payload.chart_spec = file.chart_spec
  if (file.dashboard_spec !== undefined) payload.dashboard_spec = file.dashboard_spec
  return {
    tool_call_id: `artifact:${normalizeVisiblePath(filePath)}`,
    tool_name: 'output_artifact',
    status: 'completed',
    args: { file_path: filePath },
    result: JSON.stringify(payload),
  } as ToolCallInfo
}))

const consoleDashboardTool = computed(() => {
  for (const tool of outputArtifactTools.value) {
    if (getDashboardSpecFromTool(tool)) return tool
  }
  for (let index = allToolCalls.value.length - 1; index >= 0; index -= 1) {
    const tool = allToolCalls.value[index]
    if (getDashboardSpecFromTool(tool)) return tool
  }
  return null
})

interface ProducedFile {
  path: string
  fullPath: string
  relativePath: string
  displayPath: string
  source: string
  status: string
}

interface ProducedFileTreeNode {
  name: string
  path: string
  label?: string
  depth: number
  file?: ProducedFile
  children: ProducedFileTreeNode[]
}

interface VisibleProducedFileTreeNode {
  node: ProducedFileTreeNode
  depth: number
  isLast: boolean
}

interface ProducedFileDirectory extends ProducedFileTreeNode {
  label: string
}

const PRODUCED_FILE_DIRECTORIES: Array<{ name: string; label: string }> = [
  { name: 'uploads', label: '用户上传文件' },
  { name: 'runtime-skills', label: '运行技能文件' },
  { name: 'workspace', label: '智能体中间文件' },
  { name: 'outputs', label: '智能体输出文件' },
]

const toSandboxDisplayPath = (path: unknown): string => {
  const raw = String(path || '').trim().replace(/\\/g, '/')
  const relativePath = normalizeVisiblePath(raw)
  if (!relativePath) return '/mnt/user-data'
  return `/mnt/user-data/${relativePath}`
}

const pushProducedFile = (files: ProducedFile[], path: string, source: string, status: string) => {
  const relativePath = normalizeVisiblePath(path)
  const displayPath = relativePath ? toSandboxDisplayPath(relativePath) : ''
  if (!path || !relativePath) return
  files.push({ path, fullPath: displayPath, relativePath, displayPath, source, status })
}

const formatConsoleDisplayPath = (path: unknown): string => {
  return toSandboxDisplayPath(path)
}

const producedFiles = computed(() => {
  const files: ProducedFile[] = []
  for (const file of workspaceFiles.value) {
    pushProducedFile(files, file.relative_path || file.path, file.source || 'workspace', 'completed')
  }
  for (const tool of allToolCalls.value) {
    for (const path of getToolProducedPaths(tool)) {
      if (!files.some(file => file.relativePath === normalizeVisiblePath(path))) {
        pushProducedFile(files, path, tool.tool_name, tool.status)
      }
    }
  }
  return files
})

const producedFileTree = computed(() => {
  const directoryMap = new Map<string, ProducedFileDirectory>()
  PRODUCED_FILE_DIRECTORIES.forEach((directory) => {
    directoryMap.set(directory.name, {
      ...directory,
      path: directory.name,
      depth: 0,
      children: [],
    })
  })

  const seenPaths = new Set<string>()
  producedFiles.value.forEach((file) => {
    const relativePath = normalizeVisiblePath(file.path)
    if (!relativePath || seenPaths.has(relativePath)) return
    seenPaths.add(relativePath)
    const [directoryName, ...restParts] = relativePath.split('/').filter(Boolean)
    if (!directoryName) return
    if (!directoryMap.has(directoryName)) {
      directoryMap.set(directoryName, {
        name: directoryName,
        label: '其他文件',
        path: directoryName,
        depth: 0,
        children: [],
      })
    }
    const rootDirectory = directoryMap.get(directoryName)!
    let current: ProducedFileTreeNode = rootDirectory
    restParts.forEach((part, index) => {
      const nodePath = [directoryName, ...restParts.slice(0, index + 1)].join('/')
      const isFile = index === restParts.length - 1
      let child = current.children.find(item => item.name === part && item.path === nodePath)
      if (!child) {
        child = {
          name: part,
          path: nodePath,
          depth: index + 1,
          children: [],
        }
        current.children.push(child)
      }
      if (isFile) {
        child.file = {
          ...file,
          path: part,
        }
      }
      current = child
    })
    if (!restParts.length) {
      rootDirectory.file = {
        ...file,
        path: relativePath,
      }
    }
  })

  const sortNodes = (nodes: ProducedFileTreeNode[]) => {
    nodes.sort((left, right) => {
      if (Boolean(left.file) !== Boolean(right.file)) return left.file ? 1 : -1
      return left.name.localeCompare(right.name)
    })
    nodes.forEach(node => sortNodes(node.children))
  }
  directoryMap.forEach(directory => sortNodes(directory.children))

  return {
    root: '/mnt/user-data',
    directories: Array.from(directoryMap.values()),
  }
})

const isConsoleDirectoryCollapsed = (name: string): boolean => collapsedConsoleDirectories.value.has(name)

const setConsoleDirectoryCollapsed = (path: string, collapsed: boolean) => {
  const normalized = normalizeVisiblePath(path)
  const next = new Set(collapsedConsoleDirectories.value)
  if (collapsed) next.add(normalized)
  else next.delete(normalized)
  collapsedConsoleDirectories.value = next
}

const toggleConsoleDirectory = (name: string) => {
  const next = new Set(collapsedConsoleDirectories.value)
  if (next.has(name)) next.delete(name)
  else next.add(name)
  collapsedConsoleDirectories.value = next
}

const getFileCount = (node: ProducedFileTreeNode): number => {
  return (node.file ? 1 : 0) + node.children.reduce((count, child) => count + getFileCount(child), 0)
}

const isFileTreeNodeCollapsed = (path: string): boolean => collapsedConsoleDirectories.value.has(path)

const selectConsoleDirectory = (path: string) => {
  selectedConsoleDirectoryPath.value = normalizeVisiblePath(path)
  selectedConsoleFilePath.value = null
}

const selectConsoleDirectoryAndToggle = (path: string) => {
  const normalized = normalizeVisiblePath(path)
  selectConsoleDirectory(normalized)
  setConsoleDirectoryCollapsed(normalized, !isFileTreeNodeCollapsed(normalized))
}

const importWorkspaceSkillDir = async (path: string) => {
  const normalized = normalizeVisiblePath(path)
  selectConsoleDirectory(normalized)
  await nextTick()
  await handleImportWorkspaceSkill()
}

const selectConsoleFile = (path: string) => {
  selectedConsoleFilePath.value = normalizeVisiblePath(path)
  selectedConsoleDirectoryPath.value = null
  consoleFilePreviewMode.value = 'render'
}

const toggleFileTreeNode = (path: string) => {
  const next = new Set(collapsedConsoleDirectories.value)
  if (next.has(path)) next.delete(path)
  else next.add(path)
  collapsedConsoleDirectories.value = next
}

const flattenFileTreeNodes = (nodes: ProducedFileTreeNode[], depth = 1): VisibleProducedFileTreeNode[] => {
  const visible: VisibleProducedFileTreeNode[] = []
  nodes.forEach((node, index) => {
    visible.push({ node, depth, isLast: index === nodes.length - 1 })
    if (!node.file && node.children.length && !isFileTreeNodeCollapsed(node.path)) {
      visible.push(...flattenFileTreeNodes(node.children, depth + 1))
    }
  })
  return visible
}

const parseToolResultPayload = (result?: string): Record<string, unknown> | null => {
  const text = String(result || '').trim()
  if (!text) return null
  const artifactLine = text.match(/^artifact_payload=(.+)$/m)?.[1]
  if (artifactLine) {
    try {
      const parsed = JSON.parse(artifactLine)
      return parsed && typeof parsed === 'object' ? parsed as Record<string, unknown> : null
    } catch {
      // fall through to normal tool JSON parsing
    }
  }
  const jsonText = text.replace(/^\[[^\]]+\]\s*/, '')
  try {
    const parsed = JSON.parse(jsonText)
    return parsed && typeof parsed === 'object' ? parsed as Record<string, unknown> : null
  } catch {
    return null
  }
}

const parseSkillLoadList = (result: string, field: string): string[] => {
  const match = String(result || '').match(new RegExp(`^${field}=(.+)$`, 'm'))
  if (!match) return []
  try {
    const parsed = JSON.parse(match[1] || '[]')
    return Array.isArray(parsed) ? parsed.map(item => String(item || '').trim()).filter(Boolean) : []
  } catch {
    return []
  }
}

const getSkillLoadProducedPaths = (tool: ToolCallInfo): string[] => {
  const result = String(tool.result || '')
  const runtimePath = result.match(/^runtime_path=([^\n]+)/m)?.[1]?.trim()
  if (!runtimePath) return []
  const paths = [`${runtimePath}/SKILL.md`]
  for (const reference of parseSkillLoadList(result, 'references')) {
    paths.push(`${runtimePath}/references/${reference}.md`)
  }
  for (const script of parseSkillLoadList(result, 'scripts')) {
    paths.push(`${runtimePath}/scripts/${script}`)
  }
  for (const asset of parseSkillLoadList(result, 'assets')) {
    paths.push(`${runtimePath}/assets/${asset}`)
  }
  return paths
}

const getToolProducedPaths = (tool: ToolCallInfo): string[] => {
  if (tool.tool_name === 'skill_load') return getSkillLoadProducedPaths(tool)
  if (!['file_edit', 'semantic_query_tool', 'file_read'].includes(tool.tool_name)) return []
  const payload = parseToolResultPayload(tool.result)
  const argPath = typeof tool.args?.file_path === 'string' ? tool.args.file_path : ''
  const argSaveAs = typeof tool.args?.save_as === 'string' ? tool.args.save_as : ''
  const resultPath = getFileEditResultPath(tool.result)
  const path = resultPath || argPath || argSaveAs
  return path ? [path] : []
}

const getPresentedArtifacts = (tool?: ToolCallInfo | null): Array<Record<string, unknown>> => {
  if (!tool || tool.tool_name !== 'present_artifacts') return []
  const payload = parseToolResultPayload(tool.result)
  const artifacts = payload?.artifacts
  if (!Array.isArray(artifacts)) return []
  return artifacts.filter((item): item is Record<string, unknown> => item !== null && typeof item === 'object' && !Array.isArray(item))
}

const getOutputArtifactToolByPath = (path: unknown): ToolCallInfo | null => {
  const normalized = normalizeVisiblePath(path)
  if (!normalized) return null
  return outputArtifactTools.value.find((tool) => {
    const payload = parseToolResultPayload(tool.result)
    return normalizeVisiblePath(payload?.file_path || tool.args?.file_path) === normalized
  }) || null
}

const getPresentedArtifactTools = (message: Message, artifactType: string): ToolCallInfo[] => {
  const tools: ToolCallInfo[] = []
  for (const tool of message.toolCalls || []) {
    if (tool.tool_name !== 'present_artifacts') continue
    getPresentedArtifacts(tool).forEach((artifact, index) => {
      if (String(artifact.artifact_type || '').trim() !== artifactType) return
      const filePath = String(artifact.file_path || index).trim()
      const outputArtifactTool = getOutputArtifactToolByPath(filePath)
      const outputArtifactPayload = parseToolResultPayload(outputArtifactTool?.result)
      const messageFileEditContent = findMessageFileEditContentByPath(message, filePath)
      const messageDashboardSpec = artifactType === 'dashboard_spec' ? parseDashboardSpecValue(messageFileEditContent) : null
      const messageChartSpec = artifactType === 'chart_spec' ? parseChartSpecValue(messageFileEditContent) : null
      const artifactPayload = messageDashboardSpec || messageChartSpec || outputArtifactPayload?.artifact_payload
      const semanticConfigId = messageDashboardSpec?.semantic_config_id || outputArtifactPayload?.semantic_config_id
      const payload = {
        ...artifact,
        artifact_payload: artifactPayload,
        chart_spec: messageChartSpec || outputArtifactPayload?.chart_spec,
        dashboard_spec: messageDashboardSpec || outputArtifactPayload?.dashboard_spec,
        semantic_config_id: semanticConfigId,
      }
      tools.push({
        ...tool,
        tool_call_id: `${tool.tool_call_id}:${artifactType}:${filePath || index}`,
        result: JSON.stringify(payload),
      })
    })
  }
  return tools
}

const parseDashboardSpecValue = (value: unknown): Record<string, unknown> | null => {
  let parsed = value
  if (typeof parsed === 'string') {
    const text = parsed.trim()
    if (!text) return null
    try {
      parsed = JSON.parse(text.replace(/^```(?:json)?\s*/i, '').replace(/```\s*$/i, '').trim())
    } catch {
      const match = text.match(/\{[\s\S]*\}/)
      if (!match) return null
      try {
        parsed = JSON.parse(match[0])
      } catch {
        return null
      }
    }
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null
  const payload = parsed as Record<string, unknown>
  const spec = payload.dashboard_spec && typeof payload.dashboard_spec === 'object' && !Array.isArray(payload.dashboard_spec)
    ? payload.dashboard_spec as Record<string, unknown>
    : payload
  return Array.isArray(spec.cards) ? spec : null
}

const parseJsonLikeValue = (value: unknown): unknown => {
  if (typeof value !== 'string') return value
  const text = value.trim()
  if (!text) return null
  try {
    return JSON.parse(text.replace(/^```(?:json)?\s*/i, '').replace(/```\s*$/i, '').trim())
  } catch {
    return value
  }
}

const parseChartSpecValue = (value: unknown): Record<string, unknown> | null => {
  const parsed = parseJsonLikeValue(value)
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null
  const payload = parsed as Record<string, unknown>
  const spec = payload.chart_spec && typeof payload.chart_spec === 'object' && !Array.isArray(payload.chart_spec)
    ? payload.chart_spec as Record<string, unknown>
    : payload
  const data = spec.data
  const encoding = spec.encoding
  const chartType = normalizeChartType(spec.chart_type)
  const yFields = Array.isArray((encoding as Record<string, unknown> | undefined)?.y)
    ? ((encoding as Record<string, unknown>).y as unknown[]).map(item => String(item || '').trim()).filter(Boolean)
    : []
  const graphPayload = data && typeof data === 'object' && !Array.isArray(data) ? data as Record<string, unknown> : spec
  const nodes = Array.isArray(graphPayload.nodes) ? graphPayload.nodes : []
  const links = Array.isArray(graphPayload.links) ? graphPayload.links : Array.isArray(graphPayload.edges) ? graphPayload.edges : []
  if (String(spec.version || '').trim() !== '1.0') return null
  if (chartType === 'graph' && nodes.length > 0 && links.length > 0) return spec
  if (!Array.isArray(data)) return null
  if (!encoding || typeof encoding !== 'object' || Array.isArray(encoding)) return null
  if (['line', 'bar', 'horizontal_bar', 'area', 'pie', 'map', 'combo', 'kpi', 'funnel', 'graph'].includes(chartType) && yFields.length === 0) return null
  if (['line', 'bar', 'horizontal_bar', 'area', 'pie', 'map', 'combo', 'funnel', 'graph'].includes(chartType) && !String((encoding as Record<string, unknown>).x || '').trim()) return null
  return spec
}

const isChartSpecValue = (value: unknown): value is Record<string, unknown> => {
  return parseChartSpecValue(value) !== null
}

const normalizeVisiblePath = (path: unknown): string => {
  let value = String(path || '').trim().replace(/\\/g, '/')
  value = value.replace(/^\/mnt\/user-data\/?/, '')
  value = value.replace(/^\/+/, '')
  return value
}

const replaceVisiblePathsForDisplay = (text: string): string => {
  return String(text || '')
    .replace(/(?<!\/mnt\/user-data)\/(workspace|outputs|uploads)(?=\/|["'\s,}\]]|$)/g, '/mnt/user-data/$1')
    .replace(/(?<!\/mnt\/user-data)\/runtime-skills(?=\/|["'\s,}\]]|$)/g, '/mnt/user-data/runtime-skills')
}

const getFileEditResultPath = (result?: string): string => {
  const text = String(result || '')
  const payload = parseToolResultPayload(result)
  if (typeof payload?.file_path === 'string') return payload.file_path
  if (typeof payload?.target_path === 'string') return payload.target_path
  if (typeof payload?.result_path === 'string') return payload.result_path
  return (
    text.match(/file_path=([^\n]+)/)?.[1]
    || text.match(/target_path=([^\n]+)/)?.[1]
    || text.match(/result_path=([^\n]+)/)?.[1]
    || ''
  ).trim()
}

const isDashboardSpecArtifactTool = (tool: ToolCallInfo): boolean => {
  if (!['present_artifacts', 'output_artifact'].includes(tool.tool_name)) return false
  return getArtifactType(tool) === 'dashboard_spec'
}

const isChartSpecArtifactTool = (tool: ToolCallInfo): boolean => {
  if (!['present_artifacts', 'output_artifact'].includes(tool.tool_name)) return false
  return getArtifactType(tool) === 'chart_spec'
}

const getArtifactType = (tool?: ToolCallInfo | null): string => {
  const payload = parseToolResultPayload(tool?.result)
  return String(payload?.artifact_type || '').trim()
}

const getArtifactPayload = (tool?: ToolCallInfo | null): unknown => {
  const payload = parseToolResultPayload(tool?.result)
  if (payload?.artifact_payload !== undefined) return payload.artifact_payload
  if (payload?.chart_spec !== undefined) return payload.chart_spec
  if (payload?.dashboard_spec !== undefined) return payload.dashboard_spec
  return payload?.content
}

const findFileEditContentByPath = (path: unknown): unknown => {
  const normalized = normalizeVisiblePath(path)
  if (!normalized) return null
  return findFileEditContentByPathInTools(normalized, allToolCalls.value)
}

const findFileEditContentByPathInTools = (path: unknown, tools: ToolCallInfo[]): unknown => {
  const normalized = normalizeVisiblePath(path)
  if (!normalized) return null
  const candidates = tools.filter((tool) => {
    if (tool.tool_name !== 'file_edit') return false
    if (normalizeVisiblePath(tool.args?.file_path) === normalized) return true
    return normalizeVisiblePath(getFileEditResultPath(tool.result)) === normalized
  })
  for (let index = candidates.length - 1; index >= 0; index -= 1) {
    const candidate = candidates[index]
    const content = candidate?.args?.content
    if (content !== undefined && content !== null && String(content).trim() !== '') {
      return content
    }
    const payload = getArtifactPayload(candidate)
    if (payload !== undefined && payload !== null) {
      return payload
    }
  }
  return null
}

const findMessageFileEditContentByPath = (message: Message, path: unknown): unknown => {
  return findFileEditContentByPathInTools(path, message.toolCalls || [])
}

const getDashboardSpecFromTool = (tool?: ToolCallInfo | null, message?: Message | null): DashboardSpecPayload | null => {
  if (!tool) return null
  if (!isDashboardSpecArtifactTool(tool)) return null
  const resultPayload = parseToolResultPayload(tool.result)
  const artifactPath = resultPayload?.file_path || tool.args?.file_path
  const messageFileEditContent = message ? findMessageFileEditContentByPath(message, artifactPath) : null
  const content = messageFileEditContent ?? getArtifactPayload(tool) ?? tool.args?.content ?? findFileEditContentByPath(tool.args?.source_path)
  const spec = parseDashboardSpecValue(content)
  if (!spec) return null
  const cards = Array.isArray(spec.cards) ? spec.cards : []
  if (cards.length === 0) return null

  const warnings = Array.isArray(resultPayload?.warnings) ? resultPayload.warnings.map(item => String(item)) : []
  const validCount = typeof resultPayload?.valid_count === 'number'
    ? resultPayload.valid_count
    : cards.length
  const specSemanticConfigId = typeof spec.semantic_config_id === 'string' ? spec.semantic_config_id.trim() : ''
  const resultSemanticConfigId = typeof resultPayload?.semantic_config_id === 'string' ? resultPayload.semantic_config_id.trim() : ''

  return {
    dashboard_spec: spec,
    validation_warnings: warnings,
    writable_card_count: Math.max(0, validCount),
    datasource_id: typeof resultPayload?.datasource_id === 'string' ? resultPayload.datasource_id : selectedDatasource.value || null,
    semantic_config_id: resultSemanticConfigId || specSemanticConfigId || props.semanticConfigId || null,
    conversation_id: props.conversationId || localConversationId.value || null,
  }
}

const getDashboardPreviewTools = (message: Message): ToolCallInfo[] => {
  const previewTools: ToolCallInfo[] = []
  const indexByKey = new Map<string, number>()
  const candidates = getPresentedArtifactTools(message, 'dashboard_spec')
  for (const tool of candidates) {
    const payload = getDashboardSpecFromTool(tool, message)
    if (!payload) continue
    const title = getDashboardPreviewTitle(payload).trim()
    const cards = getDashboardPreviewCards(payload)
    const fallbackKey = cards.map((card, index) => `${getCardTitle(card, index)}:${getCardChartType(card)}`).join('|')
    const key = title || fallbackKey || tool.tool_call_id
    const existingIndex = indexByKey.get(key)
    if (existingIndex === undefined) {
      indexByKey.set(key, previewTools.length)
      previewTools.push(tool)
    } else {
      previewTools[existingIndex] = tool
    }
  }
  return previewTools
}

const getChartSpecFromTool = (tool?: ToolCallInfo | null): Record<string, unknown> | null => {
  if (!tool || !isChartSpecArtifactTool(tool)) return null
  const resultPayload = parseToolResultPayload(tool.result)
  const candidates = [
    getArtifactPayload(tool),
    resultPayload?.content,
    tool.args?.content,
    findFileEditContentByPath(tool.args?.source_path),
    findFileEditContentByPath(tool.args?.target_path),
    findFileEditContentByPath(getFileEditResultPath(tool.result)),
  ]
  for (const candidate of candidates) {
    const spec = parseChartSpecValue(candidate)
    if (spec) return spec
  }
  return null
}

const getChartPreviewTools = (message: Message): ToolCallInfo[] => {
  const previewTools: ToolCallInfo[] = []
  for (const tool of getPresentedArtifactTools(message, 'chart_spec')) {
    const spec = getChartSpecFromTool(tool)
    if (!spec) continue
    previewTools.push(tool)
  }
  return previewTools
}

const getDashboardPreviewCards = (payload: DashboardSpecPayload | null): Array<Record<string, unknown>> => {
  const cards = payload?.dashboard_spec?.cards
  return Array.isArray(cards) ? cards.filter(item => item && typeof item === 'object') as Array<Record<string, unknown>> : []
}

const getDashboardPreviewTitle = (payload: DashboardSpecPayload | null): string => {
  return String(payload?.dashboard_spec?.dashboard_name || payload?.dashboard_spec?.title || '看板预览')
}

const getDashboardPreviewWritableCount = (payload: DashboardSpecPayload | null): number => {
  if (!payload) return 0
  if (typeof payload.writable_card_count === 'number') return payload.writable_card_count
  return getDashboardPreviewCards(payload).length
}

const getDashboardPreviewWarningCount = (payload: DashboardSpecPayload | null): number => {
  return payload?.validation_warnings?.length || 0
}

const getCardTitle = (card: Record<string, unknown>, index: number): string => {
  return String(card.title || card.name || `卡片 ${index + 1}`)
}

const getCardChartType = (card: Record<string, unknown>): string => {
  const labels: Record<string, string> = {
    kpi: '指标卡',
    line: '折线图',
    bar: '柱状图',
    horizontal_bar: '横向柱状图',
    pie: '饼图',
    table: '明细表',
    map: '地图',
    combo: '柱线组合图',
    area: '面积图',
    funnel: '漏斗图',
    graph: '关系图',
  }
  const type = normalizeChartType(card.chart_type || card.type)
  return labels[type] || type
}

const getCardLayoutText = (card: Record<string, unknown>): string => {
  const layout = card.layout
  if (!layout || typeof layout !== 'object' || Array.isArray(layout)) return ''
  const item = layout as Record<string, unknown>
  const x = Number(item.x ?? 0)
  const y = Number(item.y ?? 0)
  const w = Number(item.w ?? 0)
  const h = Number(item.h ?? 0)
  if ([x, y, w, h].some(value => Number.isNaN(value))) return ''
  return `${x},${y} / ${w}x${h}`
}

const selectedConsoleFile = computed(() => {
  const selectedPath = selectedConsoleFilePath.value
  if (!selectedPath) return null
  return producedFiles.value.find(file => file.relativePath === selectedPath) || null
})

const selectedConsoleFileTool = computed(() => {
  const selectedPath = selectedConsoleFilePath.value
  if (!selectedPath) return null
  for (let index = allToolCalls.value.length - 1; index >= 0; index -= 1) {
    const tool = allToolCalls.value[index]
    if (!tool) continue
    if (getToolProducedPaths(tool).some(path => normalizeVisiblePath(path) === selectedPath)) return tool
  }
  return null
})

const selectedWorkspaceFile = computed(() => {
  const selectedPath = selectedConsoleFilePath.value
  if (!selectedPath) return null
  return workspaceFiles.value.find(file => normalizeVisiblePath(file.relative_path || file.path) === selectedPath) || null
})

const workspaceSkillDirectories = computed(() => {
  const skillDirs = new Set<string>()
  producedFiles.value.forEach((file) => {
    const relativePath = normalizeVisiblePath(file.relativePath || file.path)
    if (!relativePath.endsWith('/SKILL.md')) return
    const dirPath = relativePath.slice(0, -'/SKILL.md'.length)
    if (dirPath.startsWith('workspace/') || dirPath.startsWith('outputs/')) {
      skillDirs.add(dirPath)
    }
  })
  return skillDirs
})

const selectedWorkspaceSkillDir = computed(() => {
  const selectedPath = normalizeVisiblePath(selectedConsoleDirectoryPath.value)
  if (!selectedPath) return null
  return workspaceSkillDirectories.value.has(selectedPath) ? selectedPath : null
})

const canImportSelectedWorkspaceSkill = computed(() => Boolean(selectedWorkspaceSkillDir.value && localConversationId.value))

const canImportWorkspaceSkillDir = (path: string): boolean => {
  const normalized = normalizeVisiblePath(path)
  return Boolean(normalized && localConversationId.value && workspaceSkillDirectories.value.has(normalized))
}

const handleImportWorkspaceSkill = async () => {
  const conversationId = localConversationId.value
  const skillDirPath = selectedWorkspaceSkillDir.value
  if (!conversationId || !skillDirPath) return
  const confirmed = window.confirm(`确定导入技能目录「${skillDirPath.split('/').pop() || skillDirPath}」吗？导入后会作为用户技能草稿，可在技能管理中继续编辑。`)
  if (!confirmed) return
  importingWorkspaceSkillPath.value = skillDirPath
  try {
    const response = await importDataAnalyzeWorkspaceSkill(conversationId, { skill_dir_path: skillDirPath })
    const skill = response.data
    importedSkillIdForManager.value = skill?.skill_id || skill?.name || null
    commandSkills.value = []
    await loadCommandSkills()
    isSkillManagerOpen.value = true
    ElMessage.success('技能已导入，可在技能管理中继续编辑')
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.msg || '导入技能失败')
  } finally {
    importingWorkspaceSkillPath.value = null
  }
}

const loadWorkspaceFiles = async (conversationId: string | null | undefined) => {
  if (!conversationId || selectedAgent.value !== 'chat_data_data_analyze') {
    workspaceFiles.value = []
    workspaceFileContents.value = {}
    return
  }
  isWorkspaceFilesLoading.value = true
  try {
    const response = await getDataAnalyzeWorkspaceFiles(conversationId)
    if (conversationId !== localConversationId.value || selectedAgent.value !== 'chat_data_data_analyze') return
    workspaceFiles.value = response.data?.files || []
  } catch (err) {
    if (conversationId !== localConversationId.value) return
    workspaceFiles.value = []
    console.error('加载智能分析工作区文件失败:', err)
  } finally {
    if (conversationId === localConversationId.value) {
      isWorkspaceFilesLoading.value = false
    }
  }
}

const scheduleWorkspaceFilesRefresh = () => {
  if (selectedAgent.value !== 'chat_data_data_analyze' || !localConversationId.value) return
  if (workspaceFilesRefreshTimer.value !== null) {
    window.clearTimeout(workspaceFilesRefreshTimer.value)
  }
  workspaceFilesRefreshTimer.value = window.setTimeout(() => {
    workspaceFilesRefreshTimer.value = null
    loadWorkspaceFiles(localConversationId.value)
  }, 500)
}

const loadWorkspaceFileContent = async (conversationId: string | null | undefined, path: string | null) => {
  if (!conversationId || !path || workspaceFileContents.value[path] !== undefined) return
  loadingWorkspaceFilePath.value = path
  try {
    const response = await getDataAnalyzeWorkspaceFileContent(conversationId, path)
    if (conversationId !== localConversationId.value) return
    workspaceFileContents.value = {
      ...workspaceFileContents.value,
      [path]: response.data?.content || '',
    }
  } catch (err) {
    if (conversationId !== localConversationId.value) return
    workspaceFileContents.value = {
      ...workspaceFileContents.value,
      [path]: '文件内容加载失败。',
    }
    console.error('加载智能分析工作区文件内容失败:', err)
  } finally {
    if (loadingWorkspaceFilePath.value === path) {
      loadingWorkspaceFilePath.value = null
    }
  }
}

const selectedConsoleFileText = computed(() => {
  const selectedPath = selectedConsoleFilePath.value
  if (!selectedPath) return ''
  if (selectedWorkspaceFile.value) {
    if (loadingWorkspaceFilePath.value === selectedPath) return '文件内容加载中...'
    const content = workspaceFileContents.value[selectedPath] ?? ''
    if (!selectedPath.toLowerCase().endsWith('.json')) return replaceVisiblePathsForDisplay(content)
    try {
      return replaceVisiblePathsForDisplay(JSON.stringify(JSON.parse(content), null, 2))
    } catch {
      return replaceVisiblePathsForDisplay(content)
    }
  }
  const tool = selectedConsoleFileTool.value
  const payload = parseToolResultPayload(tool?.result)
  const payloadContent = getArtifactPayload(tool) ?? payload?.content
  const fileEditContent = findFileEditContentByPath(selectedPath)
  const content = payloadContent ?? tool?.args?.content ?? fileEditContent ?? tool?.result ?? ''
  if (content === null || content === undefined) return ''
  if (typeof content !== 'string') return replaceVisiblePathsForDisplay(JSON.stringify(content, null, 2))
  if (!selectedPath.toLowerCase().endsWith('.json')) return replaceVisiblePathsForDisplay(content)
  try {
    return replaceVisiblePathsForDisplay(JSON.stringify(JSON.parse(content), null, 2))
  } catch {
    return replaceVisiblePathsForDisplay(content)
  }
})

const selectedConsoleChartSpec = computed<Record<string, unknown> | null>(() => {
  const selectedPath = selectedConsoleFilePath.value
  if (!selectedPath || !selectedPath.toLowerCase().endsWith('.json')) return null
  const tool = selectedConsoleFileTool.value
  const payload = parseToolResultPayload(tool?.result)
  const fileEditContent = findFileEditContentByPath(selectedPath)
  const workspaceContent = selectedWorkspaceFile.value ? selectedConsoleFileText.value : null
  const candidates = selectedWorkspaceFile.value
    ? [workspaceContent]
    : [
        getArtifactPayload(tool),
        payload?.content,
        tool?.args?.content,
        fileEditContent,
        selectedConsoleFileText.value,
      ]
  for (const candidate of candidates) {
    const spec = parseChartSpecValue(candidate)
    if (spec) return spec
  }
  return null
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

const isDashboardPreviewWritable = (tool: ToolCallInfo, payload: DashboardSpecPayload | null): boolean => {
  return tool.status === 'completed' && getDashboardPreviewWritableCount(payload) > 0 && !createdDashboardByToolId.value[tool.tool_call_id]
}

const resolveDashboardPreviewSemanticConfigId = (message: Message, payload: DashboardSpecPayload): string | null => {
  const specId = String(payload.dashboard_spec?.semantic_config_id || '').trim()
  if (specId) return specId
  const payloadId = String(payload.semantic_config_id || '').trim()
  if (payloadId) return payloadId
  const selectedId = String(message.semanticConfigSelection?.semantic_config_id || '').trim()
  if (selectedId) return selectedId
  return props.semanticConfigId || null
}

const handleCreateDashboardFromPreview = async (message: Message, tool: ToolCallInfo) => {
  const payload = getDashboardSpecFromTool(tool, message)
  if (!payload?.dashboard_spec) return
  if (!isDashboardPreviewWritable(tool, payload)) return
  creatingDashboardToolCallId.value = tool.tool_call_id
  try {
    const res = await createDashboardFromSpec({
      dashboard_spec: payload.dashboard_spec,
      datasource_id: payload.datasource_id || selectedDatasource.value,
      semantic_config_id: resolveDashboardPreviewSemanticConfigId(message, payload),
      conversation_id: payload.conversation_id || props.conversationId || localConversationId.value || null,
    })
    if (res.status !== 'success') {
      const warnings = res.data?.warnings || []
      throw new Error(warnings.length ? `${res.msg || '创建看板失败'}：${warnings[0]}` : (res.msg || '创建看板失败'))
    }
    const dashboard = res.data?.dashboard
    if (dashboard?.id) {
      createdDashboardByToolId.value = {
        ...createdDashboardByToolId.value,
        [tool.tool_call_id]: dashboard,
      }
      emit('dashboardCreated', dashboard)
    }
    const warnings = res.data?.warnings || []
    ElMessage.success(warnings.length ? `看板已创建，${warnings.length} 个卡片需调整` : '看板已创建，已切换到数据看板')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '创建看板失败')
  } finally {
    creatingDashboardToolCallId.value = null
  }
}

const selectedToolResult = computed(() => {
  if (!activeToolCall.value) return ''
  return replaceVisiblePathsForDisplay(activeToolCall.value.result || (activeToolCall.value.status === 'completed' ? '已完成，工具未返回文本内容。' : '执行中...'))
})

type ToolDetailResultPayload = {
  columns?: unknown
  data?: unknown
  total_count?: unknown
}

const stripToolDetailResultHeader = (value: string) => String(value || '').replace(/^\[[^\]\n]+\]\s*\n/, '').trim()

const parseToolDetailResultPayload = (value: string): ToolDetailResultPayload | null => {
  const text = stripToolDetailResultHeader(value)
  if (!text.startsWith('{') && !text.startsWith('[')) return null
  try {
    const parsed = JSON.parse(text)
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed as ToolDetailResultPayload : null
  } catch {
    return null
  }
}

const activeToolResultPayload = computed(() => parseToolDetailResultPayload(selectedToolResult.value))
const activeToolTableRows = computed<Record<string, unknown>[]>(() => {
  const rows = activeToolResultPayload.value?.data
  if (!Array.isArray(rows)) return []
  return rows.filter((row): row is Record<string, unknown> => row !== null && typeof row === 'object' && !Array.isArray(row))
})
const activeToolTableColumns = computed<string[]>(() => {
  const payloadColumns = activeToolResultPayload.value?.columns
  if (Array.isArray(payloadColumns)) {
    const columns = payloadColumns.map(col => String(col || '').trim()).filter(Boolean)
    if (columns.length) return columns
  }
  const firstRow = activeToolTableRows.value[0]
  return firstRow ? Object.keys(firstRow) : []
})
const activeToolPreviewRows = computed(() => activeToolTableRows.value.slice(0, 20))
const activeToolTotalRows = computed(() => {
  const total = Number(activeToolResultPayload.value?.total_count)
  return Number.isFinite(total) && total >= activeToolTableRows.value.length ? total : activeToolTableRows.value.length
})
const hasActiveToolTablePreview = computed(() => (
  activeToolCall.value?.tool_name === 'semantic_query_tool' &&
  activeToolTableColumns.value.length > 0 &&
  activeToolPreviewRows.value.length > 0
))

const formatToolTableCell = (value: unknown): string => {
  if (value === null || value === undefined || value === '') return '-'
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean' || typeof value === 'bigint') return String(value)
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

// 判断是否已输出可见正文，排除占位文案和空白内容。
const hasVisibleAssistantContent = (content: string): boolean => {
  const text = hideSemanticConfigSelectionPayloads(content).trim()
  return Boolean(text && text !== '正在思考...')
}

// 首 token 前已有气泡占位时，底部状态行不重复显示。
const shouldShowStreamingStatus = (message: Message): boolean => {
  return hasVisibleAssistantContent(message.content) || Boolean(message.toolCalls?.length)
}

const formatJson = (value: unknown) => replaceVisiblePathsForDisplay(JSON.stringify(value || {}, null, 2))

const selectToolCall = (toolCall: ToolCallInfo) => {
  activeToolCallId.value = toolCall.tool_call_id
  sidePanelTab.value = 'tool'
  sidePanelOpen.value = true
}

const openConsolePanel = () => {
  sidePanelTab.value = 'console'
  sidePanelOpen.value = true
}

const syncHistoryToolCalls = () => {
  const nextMap = new Map<string, ToolCallInfo>()
  for (const message of messages.value) {
    if (message.role !== 'assistant' || !message.toolCalls?.length) continue
    const segments = message.segments || []
    const hasToolSegments = segments.some(segment => segment.type === 'toolCall')
    if (!hasToolSegments) {
      const rebuiltSegments: MessageSegment[] = message.content ? [{ type: 'text', content: message.content }] : []
      for (const toolCall of message.toolCalls) {
        rebuiltSegments.push({ type: 'toolCall', toolCallId: toolCall.tool_call_id })
      }
      message.segments = rebuiltSegments
    }
    for (const toolCall of message.toolCalls) {
      nextMap.set(toolCall.tool_call_id, toolCall)
    }
  }
  toolCallsMap.value = nextMap
}

const { isLoadingHistory, resetHistory } = useConversationHistory({
  conversationId: conversationIdRef,
  messages,
})

const renderMarkdown = (content: string) => md.render(hideSemanticConfigSelectionPayloads(content))

const handleScroll = () => {
  const container = chatContainerRef.value
  if (!container) return
  const { scrollTop, scrollHeight, clientHeight } = container
  isNearBottom.value = scrollHeight - scrollTop - clientHeight < 100
}

const isToolErrorResult = (content?: string) => {
  const text = String(content || '').trim()
  return text.includes('执行失败') || text.toLowerCase().startsWith('error:')
}

const getMessageToolCall = (message: Message, toolCallId: string) => {
  return message.toolCalls?.find(item => item.tool_call_id === toolCallId) || findToolCallById(toolCallId)
}

const handleSkillSelect = (skill: SkillInfo) => {
  selectedSkill.value = skill
  nextTick(() => textareaRef.value?.focus())
}

const loadCommandSkills = async () => {
  if (commandSkills.value.length > 0 || isCommandSkillLoading.value) return
  try {
    isCommandSkillLoading.value = true
    const response = await getSkills({ agent: 'analyze' })
    commandSkills.value = response.data?.skills || []
  } catch (err) {
    commandSkills.value = []
    console.error('加载技能列表失败:', err)
  } finally {
    isCommandSkillLoading.value = false
  }
}

const loadModelOptions = async () => {
  try {
    const response = await getChatDataConfig()
    const models = response.data?.models || []
    dataAnalyzeEnabled.value = !!response.data?.data_analyze_enabled
    if (props.conversationAgentId === 'chat_data_data_analyze' && dataAnalyzeEnabled.value) {
      selectedAgent.value = 'chat_data_data_analyze'
    } else if (selectedAgent.value === 'chat_data_data_analyze' && !dataAnalyzeEnabled.value) {
      selectedAgent.value = 'chat_data'
    }
    modelOptions.value = models
    const defaultModel = models.find(item => item.is_default)?.id || models[0]?.id || ''
    if (defaultModel && !models.some(item => item.id === selectedModel.value)) {
      selectedModel.value = defaultModel
    }
  } catch (err) {
    modelOptions.value = []
    selectedModel.value = ''
    console.error('加载模型列表失败:', err)
  }
}

onMounted(() => {
  document.addEventListener('click', handleDocumentClick)
  loadModelOptions()
})

onBeforeUnmount(() => {
  document.removeEventListener('click', handleDocumentClick)
})

const selectCommandSkill = (skill: SkillInfo) => {
  selectedSkill.value = skill
  input.value = input.value.replace(/^\/[^\s/]*\s?/, '')
  isSkillCommandOpen.value = false
  highlightedSkillIndex.value = 0
  nextTick(() => textareaRef.value?.focus())
}

const selectAgent = (agent: typeof agentOptions.value[number]) => {
  if (agent.disabled) {
    ElMessage.info('功能暂未开放')
    isAgentSelectorOpen.value = false
    return
  }
  selectedAgent.value = agent.id
  isAgentSelectorOpen.value = false
}

const handleDocumentClick = (event: MouseEvent) => {
  const target = event.target
  if (!(target instanceof Node)) return
  if (agentSelectorRef.value?.contains(target)) return
  isAgentSelectorOpen.value = false
}

const clearSelectedSkill = () => {
  selectedSkill.value = null
  nextTick(() => textareaRef.value?.focus())
}

watch(input, () => {
  nextTick(() => {
    if (!textareaRef.value) return
    textareaRef.value.style.height = 'auto'
    textareaRef.value.style.height = `${Math.min(textareaRef.value.scrollHeight, 200)}px`
  })
})

watch(skillCommandKeyword, (keyword) => {
  if (keyword === null) {
    isSkillCommandOpen.value = false
    highlightedSkillIndex.value = 0
    return
  }
  isSkillCommandOpen.value = true
  highlightedSkillIndex.value = 0
  loadCommandSkills()
})

watch(
  () => props.conversationAgentId,
  (agentId) => {
    selectedAgent.value = agentId === 'chat_data_data_analyze' && dataAnalyzeEnabled.value ? 'chat_data_data_analyze' : 'chat_data'
  }
)

watch(selectedAgent, (agentId) => {
  selectedSkill.value = null
  if (agentId !== props.conversationAgentId) {
    emit('agentChange', agentId)
  }
  loadWorkspaceFiles(localConversationId.value)
})

watch(
  () => props.conversationId,
  (nextId, prevId) => {
    if (nextId && nextId === localConversationId.value) return
    if (nextId !== prevId && !isGenerating.value) {
      localConversationId.value = nextId || null
      hasLocalConversationStarted.value = false
      messages.value = []
      toolCallsMap.value.clear()
      workspaceFiles.value = []
      workspaceFileContents.value = {}
      activeToolCallId.value = null
      sidePanelOpen.value = false
      sidePanelTab.value = 'console'
      activeAssistantMessageId.value = null
      collapsedReasoningStageKeys.value = new Set()
      resetTokenUsagePanel()
      resetHistory()
    }
  }
)

watch(
  () => localConversationId.value,
  (conversationId) => {
    loadWorkspaceFiles(conversationId)
  },
  { immediate: true }
)

watch(selectedConsoleFilePath, (path) => {
  if (!path || !selectedWorkspaceFile.value) return
  loadWorkspaceFileContent(localConversationId.value, path)
})

watch(
  () => [selectedConsoleFilePath.value, Boolean(selectedConsoleChartSpec.value), Boolean(hasSelectedConsoleTable.value)] as const,
  () => {
    if (selectedConsoleChartSpec.value) {
      consoleFilePreviewMode.value = 'render'
      return
    }
    if (hasSelectedConsoleTable.value) {
      consoleFilePreviewMode.value = 'table'
      return
    }
    consoleFilePreviewMode.value = 'source'
  }
)

watch(messages, () => {
  if (isGenerating.value) return
  syncHistoryToolCalls()
  if (messages.value.length > 0) {
    hasLocalConversationStarted.value = true
  }
}, { deep: true })

watch(messages, () => {
  if (!isNearBottom.value) return
  nextTick(() => {
    if (!chatContainerRef.value) return
    chatContainerRef.value.scrollTop = chatContainerRef.value.scrollHeight
  })
}, { deep: true })

const ensureAssistantMessage = () => {
  const activeId = activeAssistantMessageId.value
  const active = activeId ? messages.value.find(item => item.id === activeId) : null
  if (active && active.role === 'assistant') return active
  const last = messages.value[messages.value.length - 1]
  if (last?.role === 'assistant') {
    last.isStreaming = true
    activeAssistantMessageId.value = last.id
    return last
  }
  const assistant: Message = {
    id: `assistant-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    role: 'assistant',
    content: '',
    timestamp: new Date(),
    isStreaming: true,
  }
  messages.value.push(assistant)
  activeAssistantMessageId.value = assistant.id
  return assistant
}

const removeEmptyActiveAssistant = () => {
  const activeId = activeAssistantMessageId.value
  if (!activeId) return
  const active = messages.value.find(item => item.id === activeId)
  const hasSegments = Boolean(active?.segments?.length)
  const hasTaskStages = Boolean(active?.taskStages?.length)
  if (active && active.role === 'assistant' && !active.content && !active.error && !active.toolCalls?.length && !hasSegments && !hasTaskStages) {
    messages.value = messages.value.filter(item => item.id !== activeId)
  }
}

const upsertToolMessage = (toolInfo: ToolCallInfo) => {
  if (!toolInfo.tool_call_id) return
  removeEmptyActiveAssistant()
  toolCallsMap.value.set(toolInfo.tool_call_id, toolInfo)
  const assistant = ensureAssistantMessage()
  const toolCalls = assistant.toolCalls || []
  const existingIndex = toolCalls.findIndex(item => item.tool_call_id === toolInfo.tool_call_id)
  if (existingIndex >= 0) {
    toolCalls[existingIndex] = toolInfo
  } else {
    toolCalls.push(toolInfo)
  }
  assistant.toolCalls = [...toolCalls]
  const segments = assistant.segments || []
  if (!segments.some(segment => segment.type === 'toolCall' && segment.toolCallId === toolInfo.tool_call_id)) {
    segments.push({ type: 'toolCall', toolCallId: toolInfo.tool_call_id })
  }
  assistant.segments = [...segments]
  activeAssistantMessageId.value = assistant.id
  if (toolInfo.status === 'completed') {
    scheduleWorkspaceFilesRefresh()
  }
}

const isUnfinishedToolStatus = (status?: ToolCallInfo['status']) => status === 'generating' || status === 'running'

const finalizeUnfinishedToolCalls = (status: 'completed' | 'error', result?: string) => {
  const nextMap = new Map(toolCallsMap.value)
  for (const [id, tool] of nextMap) {
    if (!isUnfinishedToolStatus(tool.status)) continue
    nextMap.set(id, {
      ...tool,
      status,
      result: result || tool.result,
    })
  }
  toolCallsMap.value = nextMap

  for (const message of messages.value) {
    if (!message.toolCalls?.length) continue
    message.toolCalls = message.toolCalls.map((tool) => {
      if (!isUnfinishedToolStatus(tool.status)) return tool
      return {
        ...tool,
        status,
        result: result || tool.result,
      }
    })
  }
}

const completeRunningReasoningStages = (message?: Message) => {
  const targetMessages = message ? [message] : messages.value
  for (const item of targetMessages) {
    if (item.role !== 'assistant' || !item.taskStages?.length) continue
    let changed = false
    const nextStages = item.taskStages.map((stage) => {
      if (stage.source === 'reasoning' && stage.status === 'running') {
        changed = true
        return { ...stage, status: 'completed' as const }
      }
      return stage
    })
    if (changed) item.taskStages = nextStages
  }
}

const appendAssistantContent = (content: string) => {
  if (!content) return
  const assistant = ensureAssistantMessage()
  completeRunningReasoningStages(assistant)
  assistant.content += content
  const segments = assistant.segments || []
  const last = segments[segments.length - 1]
  if (last?.type === 'text') {
    last.content += content
  } else {
    segments.push({ type: 'text', content })
  }
  assistant.segments = [...segments]
}

const finishStreamingMessages = () => {
  completeRunningReasoningStages()
  for (const item of messages.value) {
    if (item.role === 'assistant') item.isStreaming = false
  }
}

const setActiveAssistantError = (error: string) => {
  const assistant = ensureAssistantMessage()
  completeRunningReasoningStages(assistant)
  assistant.error = error
  assistant.isStreaming = false
}

const appendReasoningStage = (content: string) => {
  if (!content) return
  const assistant = ensureAssistantMessage()
  const stages = [...(assistant.taskStages || [])]
  const segments = [...(assistant.segments || [])]
  const lastSegment = segments[segments.length - 1]
  if (lastSegment?.type === 'taskStage') {
    const existing = stages[lastSegment.stageIndex]
    if (existing?.stage === 'thinking' && existing.source === 'reasoning' && existing.status === 'running') {
      stages[lastSegment.stageIndex] = {
        ...existing,
        content: `${existing.content || ''}${content}`,
      }
      assistant.taskStages = stages
      assistant.segments = segments
      activeAssistantMessageId.value = assistant.id
      return
    }
  }

  const stageIndex = stages.length
  stages.push({
    stage: 'thinking',
    status: 'running',
    title: '思考',
    content,
    source: 'reasoning',
  })
  segments.push({ type: 'taskStage', stageIndex })
  assistant.taskStages = stages
  assistant.segments = segments
  activeAssistantMessageId.value = assistant.id
}

const handleLangGraphMessage = (msg: LangGraphMessage) => {
  if (msg.type === 'thinking') {
    const reasoningContent = typeof msg.content === 'string'
      ? msg.content
      : typeof msg.additional_kwargs?.reasoning_content === 'string'
        ? msg.additional_kwargs.reasoning_content
        : ''
    if (reasoningContent) {
      appendReasoningStage(reasoningContent)
    }
    return
  }

  if (msg.tool_calls?.length) {
    completeRunningReasoningStages(ensureAssistantMessage())
    for (const tc of msg.tool_calls) {
      if (!tc.id || !tc.name) continue
      const existing = toolCallsMap.value.get(tc.id)
      const toolInfo: ToolCallInfo = {
        tool_call_id: tc.id,
        tool_name: tc.name,
        args: { ...(existing?.args || {}), ...(tc.args || {}) },
        result: existing?.result,
        status: existing?.status || 'generating',
      }
      upsertToolMessage(toolInfo)
    }
  }

  if (msg.type === 'tool' && msg.tool_call_id) {
    completeRunningReasoningStages(ensureAssistantMessage())
    const existing = toolCallsMap.value.get(msg.tool_call_id)
    const result = msg.content || ''
    const toolInfo: ToolCallInfo = {
      tool_call_id: msg.tool_call_id,
      tool_name: msg.tool_name || existing?.tool_name || 'tool_call',
      args: { ...(existing?.args || {}), ...(msg.accumulated_args || {}) },
      result,
      status: msg.status === 'error' || isToolErrorResult(result) ? 'error' : 'completed',
    }
    upsertToolMessage(toolInfo)
    return
  }

  appendAssistantContent(msg.content || '')
}

const upsertTaskStage = (stage: TaskStageInfo) => {
  const assistant = ensureAssistantMessage()
  if (stage.source !== 'reasoning') {
    completeRunningReasoningStages(assistant)
  }
  const stages = [...(assistant.taskStages || [])]
  const incomingStage: TaskStageInfo = { ...stage, source: stage.source || 'task_stage' }
  const existingIndex = stages.findIndex(item => item.stage === incomingStage.stage && item.source !== 'reasoning')
  if (existingIndex >= 0) {
    const existing = stages[existingIndex] || incomingStage
    const isDelta = incomingStage.status === 'running' && incomingStage.content !== undefined && !incomingStage.title && !incomingStage.steps
    stages[existingIndex] = isDelta
      ? { ...existing, status: 'running', content: `${existing.content || ''}${incomingStage.content || ''}` }
      : { ...existing, ...incomingStage }
  }
  else stages.push(incomingStage)
  assistant.taskStages = stages
  const stageIndex = existingIndex >= 0
    ? existingIndex
    : stages.findIndex(item => item.stage === incomingStage.stage && item.source !== 'reasoning')
  const segments = assistant.segments || []
  if (!segments.some(segment => segment.type === 'taskStage' && segment.stageIndex === stageIndex)) {
    segments.push({ type: 'taskStage', stageIndex })
    assistant.segments = [...segments]
  }
  activeAssistantMessageId.value = assistant.id
}

const getTaskStageLabel = (stage: TaskStageInfo['stage']): string => {
  const labels: Record<TaskStageInfo['stage'], string> = {
    thinking: '思考',
    plan: '规划',
    execute: '执行',
    done: '完成',
  }
  return labels[stage]
}

const getTaskStageIcon = (stage: TaskStageInfo['stage']): string => {
  const icons: Record<TaskStageInfo['stage'], string> = {
    thinking: 'fa-regular fa-lightbulb',
    plan: 'fa-regular fa-list-check',
    execute: 'fa-solid fa-terminal',
    done: 'fa-regular fa-circle-check',
  }
  return icons[stage]
}

const getTaskStageByIndex = (message: Message, index: number): TaskStageInfo | null => {
  return message.taskStages?.[index] || null
}

const getTaskStageDisplayStatus = (message: Message, index: number): TaskStageInfo['status'] => {
  const stage = getTaskStageByIndex(message, index)
  if (!stage) return 'completed'
  if (stage.source === 'reasoning' && stage.status === 'running' && !message.isStreaming) {
    return 'completed'
  }
  return stage.status
}

const shouldShowTaskStageLine = (message: Message, segmentIndex: number): boolean => {
  const segments = message.segments || []
  const segment = segments[segmentIndex]
  if (!segment || segment.type !== 'taskStage') return false
  const stage = getTaskStageByIndex(message, segment.stageIndex)
  if (!stage) return false
  const hasBody = Boolean(stage.content || stage.steps?.length)
  const nextSegment = segments[segmentIndex + 1]
  if (stage.stage === 'execute' && !hasBody && (nextSegment?.type === 'text' || nextSegment?.type === 'toolCall')) {
    return false
  }
  return segments.slice(segmentIndex + 1).some((item) => {
    if (item.type === 'text') return Boolean(item.content)
    if (item.type === 'toolCall') return Boolean(getMessageToolCall(message, item.toolCallId))
    if (item.type === 'taskStage') return Boolean(getTaskStageByIndex(message, item.stageIndex))
    return false
  })
}

const isExecutionDetailSegment = (message: Message, segmentIndex: number): boolean => {
  const segments = message.segments || []
  for (let index = segmentIndex - 1; index >= 0; index -= 1) {
    const segment = segments[index]
    if (!segment || segment.type !== 'taskStage') continue
    const stage = getTaskStageByIndex(message, segment.stageIndex)
    return stage?.stage === 'execute'
  }
  return false
}

const isFirstExecutionDetailSegment = (message: Message, segmentIndex: number): boolean => {
  if (!isExecutionDetailSegment(message, segmentIndex)) return false
  const segments = message.segments || []
  for (let index = segmentIndex - 1; index >= 0; index -= 1) {
    const segment = segments[index]
    if (!segment) continue
    if (segment.type === 'text' || segment.type === 'toolCall') return false
    if (segment.type !== 'taskStage') continue
    const stage = getTaskStageByIndex(message, segment.stageIndex)
    return stage?.stage === 'execute'
  }
  return false
}

const handleSemanticConfigSelected = (payload: SemanticConfigSelectionInfo) => {
  const assistant = ensureAssistantMessage()
  assistant.semanticConfigSelection = payload
  assistant.semanticConfigSelectRequired = undefined
  if (payload.user_selected) {
    emit('semanticConfigSelected', payload)
  }
}

const handleSemanticConfigSelectRequired = (payload: SemanticConfigSelectRequiredInfo) => {
  const assistant = ensureAssistantMessage()
  completeRunningReasoningStages(assistant)
  assistant.semanticConfigSelectRequired = payload
  assistant.isStreaming = false
}

const getExistingAssistantMessage = (messageId?: string) => {
  if (!messageId) return null
  const message = messages.value.find(item => item.id === messageId)
  return message?.role === 'assistant' ? message : null
}

const submitAnalysisQuery = async (
  requestText: string,
  options: AnalysisSubmitOptions & { skillName?: string | null; messageSkill?: Message['skill']; restoreInputText?: string } = {}
) => {
  if (!requestText.trim() || isGenerating.value) return
  const effectiveSemanticConfigSelection = options.semanticConfigSelection !== undefined
    ? options.semanticConfigSelection
    : (props.semanticConfigSelection || null)
  const userMessage: Message = {
    id: `user-${Date.now()}`,
    role: 'user',
    content: requestText,
    timestamp: new Date(),
    skill: options.messageSkill,
  }
  if (options.appendUserMessage !== false) {
    messages.value.push(userMessage)
  }
  hasLocalConversationStarted.value = true
  let assistantMessage = getExistingAssistantMessage(options.assistantMessageId)
  if (assistantMessage) {
    activeAssistantMessageId.value = assistantMessage.id
    assistantMessage.content = ''
    assistantMessage.error = undefined
    assistantMessage.toolCalls = []
    assistantMessage.segments = []
    assistantMessage.taskStages = []
    assistantMessage.semanticConfigSelectRequired = undefined
    assistantMessage.semanticConfigSelection = effectiveSemanticConfigSelection || undefined
    assistantMessage.isStreaming = true
  } else {
    activeAssistantMessageId.value = null
    assistantMessage = ensureAssistantMessage()
    assistantMessage.semanticConfigSelection = effectiveSemanticConfigSelection || undefined
  }
  if (options.appendUserMessage !== false) {
    input.value = ''
    selectedSkill.value = null
  }
  isGenerating.value = true
  activeToolCallId.value = null
  resetTokenUsagePanel()

  try {
    let convId = props.conversationId
    if (!convId && props.ensureConversationId) {
      convId = await props.ensureConversationId('chat_data_data_analyze')
    }
    if (!convId) {
      ElMessage.error('无法创建会话')
      if (options.appendUserMessage !== false) {
        messages.value = messages.value.filter(item => item.id !== userMessage.id && item.id !== assistantMessage.id)
      }
      activeAssistantMessageId.value = null
      if (options.restoreInputText) input.value = options.restoreInputText
      if (options.messageSkill) selectedSkill.value = options.messageSkill
      return
    }
    localConversationId.value = convId

    await generateSqlStream(
      requestText,
      true,
      selectedDatasource.value,
      convId,
      'query',
      (msg) => handleLangGraphMessage(msg),
      () => {
        finalizeUnfinishedToolCalls('completed')
        finishStreamingMessages()
        scheduleWorkspaceFilesRefresh()
        emit('firstMessageComplete', convId)
      },
      () => {},
      (error) => {
        finalizeUnfinishedToolCalls('error', error)
        setActiveAssistantError(error)
      },
      (toolInfo) => {
        if (!toolInfo.tool_call_id) return
        const existing = toolCallsMap.value.get(toolInfo.tool_call_id)
        const updated: ToolCallInfo = {
          tool_call_id: toolInfo.tool_call_id,
          tool_name: toolInfo.tool_name || existing?.tool_name || 'tool_call',
          args: { ...(existing?.args || {}), ...(toolInfo.args || {}) },
          result: existing?.result,
          status: 'running',
        }
        upsertToolMessage(updated)
      },
      options.semanticConfigIdOverride !== undefined ? options.semanticConfigIdOverride : (props.semanticConfigId || null),
      selectedModelValue.value,
      selectedMemberDatasourceIds.value,
      'chat_data_data_analyze',
      options.skillName || null,
      undefined,
      (stage) => upsertTaskStage(stage),
      (payload) => {
        tokenUsage.value = payload.usage || null
      },
      (payload) => handleSemanticConfigSelected(payload),
      (payload) => handleSemanticConfigSelectRequired(payload),
      effectiveSemanticConfigSelection,
      enableDeepThinking.value
    )
  } catch (err) {
    const errorMessage = err instanceof Error ? err.message : '发送失败'
    setActiveAssistantError(errorMessage)
  } finally {
    isGenerating.value = false
  }
}

const handleSubmit = async () => {
  if (!input.value.trim() || isGenerating.value) return
  if (selectedAgent.value === 'chat_data_data_analyze' && !dataAnalyzeEnabled.value) {
    ElMessage.info('功能暂未开放')
    selectedAgent.value = 'chat_data'
    return
  }
  if (selectedAgent.value === 'chat_data') {
    emit('agentChange', 'chat_data')
    return
  }
  if (!selectedDatasource.value) {
    ElMessage.warning('请先选择数据源')
    return
  }
  if (props.executionTargets.length > 0 && selectedMemberDatasourceIds.value.length === 0) {
    ElMessage.warning('请选择执行对象')
    return
  }

  const queryText = input.value.trim()
  const skillName = selectedSkill.value?.name || null
  const requestText = buildSkillRequestText(queryText, skillName || '')
  const manualSemanticConfigId = props.semanticConfigSelection?.user_selected
    ? props.semanticConfigSelection.semantic_config_id
    : null
  const messageSkill = selectedSkill.value
    ? {
        name: selectedSkill.value.name,
        title: selectedSkill.value.title,
        description: selectedSkill.value.description,
      }
    : undefined
  await submitAnalysisQuery(requestText, {
    skillName,
    messageSkill,
    restoreInputText: queryText,
    semanticConfigIdOverride: manualSemanticConfigId,
  })
}

const handleSemanticConfigCandidateClick = async (msg: Message, candidate: SemanticConfigSelectRequiredInfo['candidates'][number]) => {
  if (msg.semanticConfigSelectRequired?.cross_config_required) return
  const configId = String(candidate.id || '').trim()
  const query = String(msg.semanticConfigSelectRequired?.query || '').trim()
  if (!configId || !query) return
  const selection: SemanticConfigSelectionInfo = {
    semantic_config_id: configId,
    semantic_config_name: candidate.name || configId,
    confidence: candidate.confidence || '',
    reason: candidate.reason || msg.semanticConfigSelectRequired?.reason || '用户从候选指标集中选择',
    matched_metrics: [],
    matched_dimensions: [],
    auto_selected: false,
    user_selected: true,
  }
  msg.semanticConfigSelection = selection
  msg.semanticConfigSelectRequired = undefined
  await submitAnalysisQuery(query, {
    semanticConfigIdOverride: configId,
    semanticConfigSelection: selection,
    assistantMessageId: msg.id,
    appendUserMessage: false,
  })
}

const handleStop = () => {
  stopGeneration()
  finalizeUnfinishedToolCalls('error', '已停止')
  finishStreamingMessages()
  activeAssistantMessageId.value = null
  isGenerating.value = false
}

const handleKeyDown = (event: KeyboardEvent) => {
  if (showSkillCommand.value) {
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      highlightedSkillIndex.value = Math.min(highlightedSkillIndex.value + 1, Math.max(filteredCommandSkills.value.length - 1, 0))
      return
    }
    if (event.key === 'ArrowUp') {
      event.preventDefault()
      highlightedSkillIndex.value = Math.max(highlightedSkillIndex.value - 1, 0)
      return
    }
    if ((event.key === 'Enter' || event.key === 'Tab') && filteredCommandSkills.value[highlightedSkillIndex.value]) {
      event.preventDefault()
      const skill = filteredCommandSkills.value[highlightedSkillIndex.value]
      if (skill) selectCommandSkill(skill)
      return
    }
    if (event.key === 'Escape') {
      event.preventDefault()
      isSkillCommandOpen.value = false
      return
    }
  }
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    handleSubmit()
  }
}
</script>

<template>
  <div class="relative h-full flex min-h-0 bg-[#F7F8FA]">
    <div class="flex-1 min-w-0 flex flex-col">
      <div
        ref="chatContainerRef"
        :class="[
          'flex-1 overflow-y-auto p-4',
          !hasLocalConversationStarted && messages.length === 0 && 'flex flex-col'
        ]"
        @scroll="handleScroll"
      >
        <div v-if="isLoadingHistory" class="h-full flex items-center justify-center text-sm text-slate-400">
          <i class="fa-solid fa-spinner fa-spin mr-2 text-xs"></i>
          <span>加载历史消息...</span>
        </div>
        <div v-else-if="!hasLocalConversationStarted && messages.length === 0" class="flex-1 flex items-start justify-center pt-[10%]">
          <div class="w-[774px] max-w-[90%] text-center">
            <h3 class="text-xl font-semibold text-gray-800 m-0 mb-2">您好，有什么可以帮您？</h3>
            <form class="relative" @submit.prevent="handleSubmit">
              <div
                :class="[
                  'rounded-2xl bg-white border border-black/[0.08] shadow-[0_2px_12px_rgba(0,0,0,0.06),0_0_0_1px_rgba(0,0,0,0.02)] transition-all duration-200',
                  inputFocused && 'border-blue-400/40 shadow-[0_2px_12px_rgba(0,0,0,0.06),0_0_0_3px_rgba(0,122,255,0.1)]'
                ]"
              >
                <div class="min-h-[54px] px-4 pt-4 pb-2">
                  <div
                    v-if="selectedSkillTitle"
                    class="mb-2 flex w-fit items-center gap-1.5 rounded-full bg-blue-50 px-2 py-1 text-xs text-blue-700"
                  >
                    <i class="fa-solid fa-wand-magic-sparkles text-[10px]"></i>
                    <span class="max-w-[140px] truncate">{{ selectedSkillTitle }}</span>
                    <button type="button" class="text-blue-400 hover:text-blue-700" @click="clearSelectedSkill">
                      <i class="fa-solid fa-xmark text-[10px]"></i>
                    </button>
                  </div>
                  <textarea
                    ref="textareaRef"
                    v-model="input"
                    placeholder="请输入分析任务，输入 / 可选择技能"
                    class="block w-full min-h-[26px] max-h-[200px] border-none bg-transparent text-[15px] leading-relaxed text-gray-700 resize-none outline-none overflow-y-hidden placeholder:text-gray-400"
                    @keydown="handleKeyDown"
                    @focus="inputFocused = true"
                    @blur="inputFocused = false"
                  ></textarea>
                </div>
                <div class="flex items-center justify-between gap-2 px-4 py-2 pb-3">
                  <div class="flex items-center gap-2">
                    <button
                      v-if="selectedAgent === 'chat_data_data_analyze'"
                      type="button"
                      class="flex h-6 items-center gap-1.5 whitespace-nowrap rounded-full px-2 text-xs text-gray-500 hover:bg-gray-100 hover:text-gray-700"
                      title="技能管理"
                      @click="isSkillManagerOpen = true"
                    >
                      <i class="fa-solid fa-wand-magic-sparkles text-[10px]"></i>
                      <span>技能</span>
                    </button>
                    <div ref="agentSelectorRef" class="relative">
                      <button
                        type="button"
                        class="flex items-center gap-1.5 whitespace-nowrap px-2 py-1 rounded-full border-none bg-transparent text-xs text-gray-500 outline-none cursor-pointer hover:bg-black/[0.05]"
                        @click="isAgentSelectorOpen = !isAgentSelectorOpen"
                      >
                        <i class="fa-solid fa-robot text-[10px] text-gray-400"></i>
                        <span>{{ selectedAgentLabel }}</span>
                        <i class="fa-solid fa-chevron-down text-[8px] text-gray-400"></i>
                      </button>
                      <div
                        v-if="isAgentSelectorOpen"
                        class="absolute left-0 bottom-8 z-50 min-w-[110px] overflow-hidden rounded-lg border border-gray-200 bg-white py-1 text-xs text-gray-600 shadow-lg"
                      >
                        <button
                          v-for="agent in agentOptions"
                          :key="agent.id"
                          type="button"
                          :aria-disabled="agent.disabled"
                          :title="agent.disabled ? '功能暂未开放' : undefined"
                          :class="[
                            'flex w-full items-center justify-between gap-2 border-none bg-transparent px-3 py-1.5 text-left text-xs',
                            selectedAgent === agent.id ? 'text-blue-600 bg-blue-50' : 'text-gray-600',
                            agent.disabled ? '!text-gray-400 cursor-not-allowed bg-gray-50' : 'cursor-pointer hover:bg-gray-50'
                          ]"
                          @click="selectAgent(agent)"
                        >
                          <span>{{ agent.label }}</span>
                          <i v-if="!agent.disabled && selectedAgent === agent.id" class="fa-solid fa-check text-[10px]"></i>
                        </button>
                      </div>
                    </div>
                    <div class="flex items-center gap-1.5 whitespace-nowrap px-2 py-1 rounded-full hover:bg-black/[0.05]">
                      <i class="fa-solid fa-microchip text-[10px] text-gray-400"></i>
                      <select v-model="selectedModel" class="border-none bg-transparent text-xs text-gray-500 outline-none cursor-pointer appearance-none pr-3">
                        <option v-for="m in modelOptions" :key="m.id" :value="m.id">{{ m.label }}</option>
                      </select>
                      <i class="fa-solid fa-chevron-down text-[8px] text-gray-400 -ml-2"></i>
                    </div>
                    <button
                      type="button"
                      :class="[
                        'flex items-center gap-1.5 whitespace-nowrap px-2 py-1 rounded-full border-none text-xs outline-none cursor-pointer transition-colors',
                        enableDeepThinking ? 'bg-blue-50 text-blue-600' : 'text-gray-500 hover:bg-black/[0.05]'
                      ]"
                      :title="enableDeepThinking ? '已开启深度思考' : '开启深度思考'"
                      :aria-pressed="enableDeepThinking"
                      @click="enableDeepThinking = !enableDeepThinking"
                    >
                      <i class="fa-solid fa-brain text-[10px]"></i>
                      <span>思考</span>
                    </button>
                  </div>
                  <button
                    type="submit"
                    :class="[
                      'w-8 h-8 flex items-center justify-center border-none rounded-full bg-gradient-to-br from-[#1677FF] to-[#1677FF] text-white shadow-[0_2px_8px_rgba(0,122,255,0.35)] cursor-pointer transition-all duration-200',
                      (isGenerating || !input.trim() || !selectedDatasource) ? 'bg-gray-200 shadow-none cursor-not-allowed text-gray-400' : 'hover:scale-105 active:scale-95'
                    ]"
                    :disabled="isGenerating || !input.trim() || !selectedDatasource"
                  >
                    <i class="fa-solid fa-arrow-up"></i>
                  </button>
                </div>
              </div>
              <div
                v-if="showSkillCommand"
                class="absolute left-0 right-0 top-full z-20 mt-2 overflow-hidden rounded-xl border border-slate-200 bg-white text-left shadow-[0_12px_32px_rgba(15,23,42,0.16)]"
              >
                <div v-if="isCommandSkillLoading" class="px-3 py-3 text-xs text-slate-400">正在加载技能</div>
                <div v-else-if="filteredCommandSkills.length === 0" class="px-3 py-3 text-xs text-slate-400">没有匹配的技能</div>
                <template v-else>
                  <button
                    v-for="(skill, index) in filteredCommandSkills"
                    :key="skill.name"
                    type="button"
                    :class="[
                      'flex w-full items-start gap-2 border-none px-3 py-2.5 text-left transition-colors',
                      index === highlightedSkillIndex ? 'bg-blue-50' : 'bg-white hover:bg-slate-50'
                    ]"
                    @mousedown.prevent="selectCommandSkill(skill)"
                  >
                    <i class="fa-solid fa-wand-magic-sparkles mt-0.5 text-xs text-blue-500"></i>
                    <span class="min-w-0 flex-1">
                      <span class="block truncate text-sm font-medium text-slate-800">{{ skill.title || skill.name }}</span>
                      <span class="mt-0.5 block truncate text-xs text-slate-400">{{ skill.description || skill.name }}</span>
                    </span>
                  </button>
                </template>
              </div>
            </form>
            <QuickQueries
              v-if="selectedDatasource"
              :key="recommendationContentType"
              :datasource-id="selectedDatasource"
              mode="conversation"
              :content-type="recommendationContentType"
              :max-items="8"
              @select-query="(query: string) => input = query"
              @select-skill="handleSkillSelect"
            />
          </div>
        </div>

        <div v-else class="max-w-[774px] mx-auto w-[90%] flex flex-col gap-5">
          <div v-for="msg in messages" :key="msg.id" class="flex" :class="msg.role === 'user' ? 'justify-end' : 'justify-start'">
            <div :class="msg.role === 'user' ? 'max-w-[80%] flex flex-col items-end gap-1.5' : 'w-full'">
              <template v-if="msg.role === 'user'">
                <div class="rounded-2xl rounded-br-md bg-[#1677FF]/10 px-4 py-2.5 text-[#1F3A56] shadow-[0_2px_8px_rgba(59,130,246,0.12)]">
                  <div class="whitespace-pre-wrap text-sm leading-relaxed">{{ msg.content }}</div>
                </div>
              </template>
              <div v-else-if="msg.role === 'tool'" class="rounded-xl bg-white border border-slate-100 px-4 py-3 shadow-sm">
                <ToolCallList
                  v-if="msg.toolCalls?.length"
                  :tool-calls="msg.toolCalls"
                  :datasource-id="selectedDatasource"
                  compact
                  :active-tool-call-id="activeToolCallId"
                  @select-tool="selectToolCall"
                />
              </div>
              <div v-else class="min-w-0 rounded-xl bg-white border border-slate-100 px-4 py-3 shadow-sm">
                <div
                  v-if="msg.semanticConfigSelectRequired"
                  class="mb-3 rounded-lg border border-red-100 bg-red-50/70 px-3 py-2 text-[13px] text-red-700"
                >
                  <div class="flex items-start gap-2">
                    <i class="fa-solid fa-circle-exclamation mt-0.5 text-[12px]"></i>
                    <div class="min-w-0 flex-1">
                      <div class="font-medium">{{ msg.semanticConfigSelectRequired.message }}</div>
                      <div v-if="msg.semanticConfigSelectRequired.candidates.length && !msg.semanticConfigSelectRequired.cross_config_required" class="mt-2 flex flex-wrap gap-2">
                        <button
                          v-for="candidate in msg.semanticConfigSelectRequired.candidates"
                          :key="candidate.id"
                          type="button"
                          class="rounded-md border border-red-200 bg-white px-2.5 py-1 text-[12px] font-medium text-red-700 transition-colors hover:border-red-300 hover:bg-red-100 disabled:cursor-not-allowed disabled:opacity-60"
                          :disabled="isGenerating"
                          :title="candidate.reason || '使用该指标集继续分析'"
                          @click="handleSemanticConfigCandidateClick(msg, candidate)"
                        >
                          {{ candidate.name || candidate.id }}
                        </button>
                      </div>
                    </div>
                  </div>
                </div>

                <div v-if="msg.segments?.length" class="min-w-0 space-y-3">
                  <template v-for="(segment, index) in msg.segments" :key="`${segment.type}-${index}`">
                    <div
                      v-if="segment.type === 'text' && segment.content"
                      :class="[
                        'analysis-markdown min-w-0 text-sm leading-relaxed text-slate-800 relative',
                        isExecutionDetailSegment(msg, index) && 'ml-9 execution-detail-line',
                        isFirstExecutionDetailSegment(msg, index) && '-mt-3'
                      ]"
                      v-html="renderMarkdown(segment.content)"
                    ></div>
                    <div
                      v-else-if="segment.type === 'toolCall' && getMessageToolCall(msg, segment.toolCallId)"
                      :class="[
                        isExecutionDetailSegment(msg, index) && 'relative ml-9 execution-detail-line',
                        isFirstExecutionDetailSegment(msg, index) && '-mt-3'
                      ]"
                    >
                      <ToolCallList
                        :tool-calls="[getMessageToolCall(msg, segment.toolCallId)!]"
                        :datasource-id="selectedDatasource"
                        compact
                        :active-tool-call-id="activeToolCallId"
                        @select-tool="selectToolCall"
                      />
                    </div>
                    <div
                      v-else-if="segment.type === 'taskStage' && getTaskStageByIndex(msg, segment.stageIndex)"
                      class="flex relative"
                    >
                      <div class="mr-3 flex flex-col items-center">
                        <div
                          :class="[
                            'flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[10px]',
                            getTaskStageDisplayStatus(msg, segment.stageIndex) === 'error'
                              ? 'bg-red-50 text-red-500'
                              : getTaskStageDisplayStatus(msg, segment.stageIndex) === 'running'
                                ? 'bg-blue-50 text-blue-500'
                                : 'bg-emerald-50 text-emerald-600'
                          ]"
                        >
                          <i v-if="getTaskStageDisplayStatus(msg, segment.stageIndex) === 'running'" class="fa-solid fa-spinner fa-spin"></i>
                          <i v-else-if="getTaskStageDisplayStatus(msg, segment.stageIndex) === 'error'" class="fa-solid fa-xmark"></i>
                          <i v-else class="fa-solid fa-check"></i>
                        </div>
                        <div
                          v-if="shouldShowTaskStageLine(msg, index)"
                          class="mt-2 min-h-5 flex-1"
                          style="width: 1px; background-image: repeating-linear-gradient(to bottom, #D8DAE5 0 3px, transparent 3px 6px);"
                        ></div>
                      </div>
                      <div
                        :class="[
                          'min-w-0 flex-1',
                          (getTaskStageByIndex(msg, segment.stageIndex)!.content || getTaskStageByIndex(msg, segment.stageIndex)!.steps?.length) ? 'pb-4' : 'pb-1'
                        ]"
                      >
                        <button
                          v-if="getTaskStageByIndex(msg, segment.stageIndex)!.source === 'reasoning'"
                          type="button"
                          class="flex w-full items-center gap-2 border-none bg-transparent p-0 text-left text-sm font-medium text-slate-800 cursor-pointer"
                          @click="toggleReasoningStage(msg, segment.stageIndex)"
                        >
                          <i
                            :class="[
                              'fa-solid fa-chevron-right text-[10px] text-slate-400 transition-transform',
                              !isReasoningStageCollapsed(msg, segment.stageIndex) && 'rotate-90'
                            ]"
                          ></i>
                          <i class="text-xs text-slate-500" :class="getTaskStageIcon(getTaskStageByIndex(msg, segment.stageIndex)!.stage)"></i>
                          <span>{{ getTaskStageByIndex(msg, segment.stageIndex)!.title || getTaskStageLabel(getTaskStageByIndex(msg, segment.stageIndex)!.stage) }}</span>
                          <span class="ml-auto text-[11px] font-normal text-slate-400">
                            {{ isReasoningStageCollapsed(msg, segment.stageIndex) ? '展开' : '收起' }}
                          </span>
                        </button>
                        <div v-else class="flex items-center gap-2 text-sm font-medium text-slate-800">
                          <i class="text-xs text-slate-500" :class="getTaskStageIcon(getTaskStageByIndex(msg, segment.stageIndex)!.stage)"></i>
                          <span>{{ getTaskStageByIndex(msg, segment.stageIndex)!.title || getTaskStageLabel(getTaskStageByIndex(msg, segment.stageIndex)!.stage) }}</span>
                          <span
                            v-if="getTaskStageByIndex(msg, segment.stageIndex)!.status === 'error'"
                            class="ml-auto inline-flex items-center gap-1 text-[11px] text-red-500"
                          >
                            失败
                          </span>
                        </div>
                        <div
                          v-if="getTaskStageByIndex(msg, segment.stageIndex)!.content && !isReasoningStageCollapsed(msg, segment.stageIndex)"
                          class="mt-1.5 whitespace-pre-wrap text-sm leading-relaxed text-slate-500"
                        >
                          {{ getTaskStageByIndex(msg, segment.stageIndex)!.content }}
                        </div>
                        <ol
                          v-if="getTaskStageByIndex(msg, segment.stageIndex)!.steps?.length && !isReasoningStageCollapsed(msg, segment.stageIndex)"
                          class="mt-1.5 list-decimal space-y-0.5 pl-4 text-sm leading-relaxed text-slate-500"
                        >
                          <li v-for="step in getTaskStageByIndex(msg, segment.stageIndex)!.steps" :key="step">{{ step }}</li>
                        </ol>
                      </div>
                    </div>
                  </template>
                </div>
                <div v-else-if="msg.content" class="analysis-markdown min-w-0 text-sm leading-relaxed text-slate-800" v-html="renderMarkdown(msg.content)"></div>
                <div v-else-if="msg.isStreaming" class="flex items-center gap-2 text-sm text-slate-500">
                  <i class="fa-solid fa-spinner fa-spin text-xs"></i>
                  <span>正在思考...</span>
                </div>
                <div v-if="getDashboardPreviewTools(msg).length && !msg.isStreaming" class="mt-3 space-y-3">
                  <div
                    v-for="tool in getDashboardPreviewTools(msg)"
                    :key="tool.tool_call_id"
                    class="rounded-xl border border-blue-100 bg-blue-50/60 px-3.5 py-3"
                  >
                    <div class="flex items-start justify-between gap-3">
                      <div class="min-w-0">
                        <div class="flex items-center gap-2">
                          <span class="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-blue-600 text-white">
                            <i class="fa-solid fa-chart-pie text-xs"></i>
                          </span>
                          <div class="min-w-0">
                            <div class="truncate text-sm font-semibold text-slate-800">{{ getDashboardPreviewTitle(getDashboardSpecFromTool(tool, msg)) }}</div>
                            <div class="mt-0.5 text-[11px] text-slate-500">
                              {{ getDashboardPreviewCards(getDashboardSpecFromTool(tool, msg)).length }} 个卡片，{{ getDashboardPreviewWritableCount(getDashboardSpecFromTool(tool, msg)) }} 个可写入
                            </div>
                          </div>
                        </div>
                        <div
                          v-if="getDashboardPreviewWarningCount(getDashboardSpecFromTool(tool, msg)) > 0"
                          class="mt-2 text-[11px] text-amber-600"
                        >
                          <i class="fa-solid fa-triangle-exclamation mr-1"></i>
                          {{ getDashboardPreviewWarningCount(getDashboardSpecFromTool(tool, msg)) }} 条校验提示，写入时会跳过不可生成 SQL 的卡片。
                        </div>
                      </div>
                      <button
                        v-if="!createdDashboardByToolId[tool.tool_call_id]"
                        type="button"
                        class="shrink-0 h-8 rounded-lg border border-blue-200 bg-blue-600 px-3 text-xs font-semibold text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-60"
                        :disabled="creatingDashboardToolCallId === tool.tool_call_id || !isDashboardPreviewWritable(tool, getDashboardSpecFromTool(tool, msg))"
                        @click="handleCreateDashboardFromPreview(msg, tool)"
                      >
                        <i v-if="creatingDashboardToolCallId === tool.tool_call_id" class="fa-solid fa-spinner fa-spin mr-1"></i>
                        <i v-else class="fa-solid fa-check mr-1"></i>
                        写入看板
                      </button>
                      <div v-else class="shrink-0 rounded-lg bg-white px-3 py-1.5 text-xs text-blue-700">
                        已写入
                      </div>
                    </div>
                    <div class="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
                      <div
                        v-for="(card, cardIndex) in getDashboardPreviewCards(getDashboardSpecFromTool(tool, msg))"
                        :key="`${tool.tool_call_id}-${cardIndex}`"
                        class="rounded-lg border border-white/80 bg-white px-3 py-2"
                      >
                        <div class="flex items-center justify-between gap-2">
                          <div class="min-w-0 truncate text-xs font-medium text-slate-700">{{ getCardTitle(card, cardIndex) }}</div>
                          <div class="shrink-0 rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500">{{ getCardChartType(card) }}</div>
                        </div>
                        <div v-if="getCardLayoutText(card)" class="mt-1 font-mono text-[10px] text-slate-400">
                          {{ getCardLayoutText(card) }}
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
                <div v-if="getChartPreviewTools(msg).length && !msg.isStreaming" class="mt-3 space-y-3">
                  <div
                    v-for="tool in getChartPreviewTools(msg)"
                    :key="tool.tool_call_id"
                    class="rounded-xl border border-slate-200 bg-slate-50/70 p-3"
                  >
                    <ChartSpecRenderer
                      v-if="getChartSpecFromTool(tool)"
                      :spec="getChartSpecFromTool(tool)!"
                    />
                  </div>
                </div>
                <div v-if="msg.error" class="mt-2 text-sm text-red-500">{{ msg.error }}</div>
              </div>
              <div v-if="msg.role === 'assistant' && msg.isStreaming && shouldShowStreamingStatus(msg)" class="mt-2 ml-3 flex items-center gap-2 text-xs text-slate-400">
                <i class="fa-solid fa-spinner fa-spin text-[11px]"></i>
                <span>{{ hasVisibleAssistantContent(msg.content) ? '正在生成' : '正在思考' }}</span>
                <span class="w-1.5 h-1.5 rounded-full bg-[#1677FF] animate-pulse"></span>
                <span class="w-1.5 h-1.5 rounded-full bg-[#1677FF] animate-pulse [animation-delay:120ms]"></span>
                <span class="w-1.5 h-1.5 rounded-full bg-[#1677FF] animate-pulse [animation-delay:240ms]"></span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div v-if="hasLocalConversationStarted || messages.length > 0" class="p-4 bg-gray-50 border-t border-slate-100">
        <div class="max-w-[774px] mx-auto w-[90%]">
          <form class="relative" @submit.prevent="handleSubmit">
            <div class="rounded-2xl bg-white border border-black/[0.08] shadow-[0_2px_12px_rgba(0,0,0,0.06)]">
              <div class="min-h-[54px] px-4 pt-4 pb-2">
                <div
                  v-if="selectedSkillTitle"
                  class="mb-2 flex w-fit items-center gap-1.5 rounded-full bg-blue-50 px-2 py-1 text-xs text-blue-700"
                >
                  <i class="fa-solid fa-wand-magic-sparkles text-[10px]"></i>
                  <span class="max-w-[140px] truncate">{{ selectedSkillTitle }}</span>
                  <button type="button" class="text-blue-400 hover:text-blue-700" @click="clearSelectedSkill">
                    <i class="fa-solid fa-xmark text-[10px]"></i>
                  </button>
                </div>
                <textarea
                  ref="textareaRef"
                  v-model="input"
                  placeholder="继续输入分析任务，输入 / 可选择技能"
                  class="block w-full min-h-[26px] max-h-[200px] border-none bg-transparent text-[15px] leading-relaxed text-gray-700 resize-none outline-none overflow-y-hidden placeholder:text-gray-400"
                  @keydown="handleKeyDown"
                  @focus="inputFocused = true"
                  @blur="inputFocused = false"
                ></textarea>
              </div>
              <div class="flex items-center justify-between gap-2 px-4 py-2 pb-3">
                <div class="flex items-center gap-2">
                  <button
                    v-if="selectedAgent === 'chat_data_data_analyze'"
                    type="button"
                    class="flex h-6 items-center gap-1.5 whitespace-nowrap rounded-full px-2 text-xs text-gray-500 hover:bg-gray-100 hover:text-gray-700"
                    title="技能管理"
                    @click="isSkillManagerOpen = true"
                  >
                    <i class="fa-solid fa-wand-magic-sparkles text-[10px]"></i>
                    <span>技能</span>
                  </button>
                  <div ref="agentSelectorRef" class="relative">
                    <button
                      type="button"
                      class="flex items-center gap-1.5 whitespace-nowrap px-2 py-1 rounded-full border-none bg-transparent text-xs text-gray-500 outline-none cursor-pointer hover:bg-black/[0.05]"
                      @click="isAgentSelectorOpen = !isAgentSelectorOpen"
                    >
                      <i class="fa-solid fa-robot text-[10px] text-gray-400"></i>
                      <span>{{ selectedAgentLabel }}</span>
                      <i class="fa-solid fa-chevron-down text-[8px] text-gray-400"></i>
                    </button>
                    <div
                      v-if="isAgentSelectorOpen"
                      class="absolute left-0 bottom-8 z-50 min-w-[110px] overflow-hidden rounded-lg border border-gray-200 bg-white py-1 text-xs text-gray-600 shadow-lg"
                    >
                      <button
                        v-for="agent in agentOptions"
                        :key="agent.id"
                        type="button"
                        :aria-disabled="agent.disabled"
                        :title="agent.disabled ? '功能暂未开放' : undefined"
                        :class="[
                          'flex w-full items-center justify-between gap-2 border-none bg-transparent px-3 py-1.5 text-left text-xs',
                          selectedAgent === agent.id ? 'text-blue-600 bg-blue-50' : 'text-gray-600',
                          agent.disabled ? '!text-gray-400 cursor-not-allowed bg-gray-50' : 'cursor-pointer hover:bg-gray-50'
                        ]"
                        @click="selectAgent(agent)"
                      >
                        <span>{{ agent.label }}</span>
                        <i v-if="!agent.disabled && selectedAgent === agent.id" class="fa-solid fa-check text-[10px]"></i>
                      </button>
                    </div>
                  </div>
                  <div class="flex items-center gap-1.5 whitespace-nowrap px-2 py-1 rounded-full hover:bg-black/[0.05]">
                    <i class="fa-solid fa-microchip text-[10px] text-gray-400"></i>
                    <select v-model="selectedModel" class="border-none bg-transparent text-xs text-gray-500 outline-none cursor-pointer appearance-none pr-3">
                      <option v-for="m in modelOptions" :key="m.id" :value="m.id">{{ m.label }}</option>
                    </select>
                    <i class="fa-solid fa-chevron-down text-[8px] text-gray-400 -ml-2"></i>
                  </div>
                  <button
                    type="button"
                    :class="[
                      'flex items-center gap-1.5 whitespace-nowrap px-2 py-1 rounded-full border-none text-xs outline-none cursor-pointer transition-colors',
                      enableDeepThinking ? 'bg-blue-50 text-blue-600' : 'text-gray-500 hover:bg-black/[0.05]'
                    ]"
                    :title="enableDeepThinking ? '已开启深度思考' : '开启深度思考'"
                    :aria-pressed="enableDeepThinking"
                    @click="enableDeepThinking = !enableDeepThinking"
                  >
                    <i class="fa-solid fa-brain text-[10px]"></i>
                    <span>思考</span>
                  </button>
                  <div v-if="tokenUsageDisplay" class="relative">
                    <button
                      type="button"
                      class="text-xs text-gray-400 whitespace-nowrap border-none bg-transparent px-1 py-0.5 rounded cursor-pointer hover:text-gray-600 hover:bg-gray-100"
                      @click="tokenUsagePanelOpen = !tokenUsagePanelOpen"
                    >
                      {{ tokenUsageDisplay }}
                    </button>
                    <div
                      v-if="tokenUsagePanelOpen"
                      class="absolute left-0 bottom-7 z-50 w-[460px] rounded-lg border border-gray-200 bg-white shadow-lg p-3 text-xs text-gray-600"
                    >
                      <div class="flex items-center justify-between mb-2">
                        <div class="font-semibold text-gray-800">上下文占用明细</div>
                        <button type="button" class="border-none bg-transparent text-gray-400 cursor-pointer hover:text-gray-600" @click="tokenUsagePanelOpen = false">
                          <i class="fa-solid fa-xmark"></i>
                        </button>
                      </div>
                      <div class="grid grid-cols-2 gap-2 mb-2">
                        <div v-for="row in tokenUsageDetailRows" :key="row.label" class="rounded bg-gray-50 px-2 py-1">
                          <div class="text-[11px] text-gray-400">{{ row.label }}</div>
                          <div class="font-medium text-gray-700">{{ formatTokenUsageDisplay({ used: row.value, total: 1 }).split(' / ')[0] }}</div>
                        </div>
                      </div>
                      <div v-if="tokenUsageMessageRows.length" class="space-y-1.5">
                        <div class="flex items-center justify-between">
                          <span class="text-[11px] text-gray-400">完整上下文占用</span>
                          <div class="inline-flex rounded bg-gray-100 p-0.5">
                            <button
                              type="button"
                              class="border-none rounded px-2 py-0.5 text-[11px] cursor-pointer"
                              :class="tokenUsageSort === 'round' ? 'bg-white text-gray-700 shadow-sm' : 'bg-transparent text-gray-400'"
                              @click="tokenUsageSort = 'round'"
                            >
                              时序
                            </button>
                            <button
                              type="button"
                              class="border-none rounded px-2 py-0.5 text-[11px] cursor-pointer"
                              :class="tokenUsageSort === 'desc' ? 'bg-white text-gray-700 shadow-sm' : 'bg-transparent text-gray-400'"
                              @click="tokenUsageSort = 'desc'"
                            >
                              Token↓
                            </button>
                            <button
                              type="button"
                              class="border-none rounded px-2 py-0.5 text-[11px] cursor-pointer"
                              :class="tokenUsageSort === 'asc' ? 'bg-white text-gray-700 shadow-sm' : 'bg-transparent text-gray-400'"
                              @click="tokenUsageSort = 'asc'"
                            >
                              Token↑
                            </button>
                          </div>
                        </div>
                        <div class="max-h-[300px] overflow-auto pr-1 space-y-1.5">
                          <div v-for="item in tokenUsageMessageRows" :key="item.key" class="rounded border border-gray-100 bg-white px-2 py-1">
                            <button type="button" class="w-full border-none bg-transparent p-0 text-left cursor-pointer" @click="toggleTokenMessage(item.key)">
                              <div class="flex items-center justify-between gap-2">
                                <span class="text-gray-700">{{ item.label }}</span>
                                <span class="font-mono text-gray-500">{{ formatTokenUsageDisplay({ used: item.tokens, total: 1 }).split(' / ')[0] }}</span>
                              </div>
                              <div class="mt-1 h-1.5 rounded-full bg-gray-100 overflow-hidden">
                                <div class="h-full rounded-full bg-blue-400" :style="{ width: `${Math.min(Math.abs(item.percentage), 100)}%` }"></div>
                              </div>
                              <div v-if="item.description && !expandedTokenMessageKeys.has(item.key)" class="mt-1 truncate text-[11px] text-gray-400">{{ item.description }}</div>
                            </button>
                            <div v-if="expandedTokenMessageKeys.has(item.key) && item.children?.length" class="mt-2 space-y-1.5">
                              <div v-for="child in item.children" :key="child.key" class="rounded bg-gray-50 p-2">
                                <div class="flex items-center justify-between gap-2 text-[11px] text-gray-500">
                                  <span>{{ child.label }}</span>
                                  <span class="font-mono">{{ formatTokenUsageDisplay({ used: child.tokens, total: 1 }).split(' / ')[0] }}</span>
                                </div>
                                <pre class="mt-1 max-h-32 overflow-auto whitespace-pre-wrap text-[11px] leading-relaxed text-gray-600">{{ child.content || child.description || '' }}</pre>
                              </div>
                            </div>
                            <pre v-else-if="expandedTokenMessageKeys.has(item.key)" class="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-gray-50 p-2 text-[11px] leading-relaxed text-gray-600">{{ item.content || item.description || '' }}</pre>
                          </div>
                        </div>
                      </div>
                      <div v-else class="text-gray-400">暂无可拆分明细。</div>
                    </div>
                  </div>
                </div>
                <button
                  v-if="isGenerating"
                  type="button"
                  class="relative w-8 h-8 flex items-center justify-center border-none rounded-full bg-[#1677FF] text-white shadow-[0_2px_8px_rgba(0,122,255,0.35)] cursor-pointer transition-all duration-200 animate-breathe hover:scale-105 active:scale-95"
                  @click="handleStop"
                >
                  <i class="fa-solid fa-stop relative z-10"></i>
                  <span class="absolute inset-0 rounded-full bg-[#1677FF] opacity-40 animate-ping"></span>
                </button>
                <button
                  v-else
                  type="submit"
                  :disabled="!input.trim() || !selectedDatasource"
                  class="w-8 h-8 flex items-center justify-center border-none rounded-full bg-[#1677FF] text-white shadow-[0_2px_8px_rgba(0,122,255,0.35)] disabled:bg-gray-200 disabled:text-gray-400 disabled:shadow-none"
                >
                  <i class="fa-solid fa-arrow-up"></i>
                </button>
              </div>
            </div>
            <div
              v-if="showSkillCommand"
              class="absolute left-0 right-0 bottom-full z-20 mb-2 overflow-hidden rounded-xl border border-slate-200 bg-white text-left shadow-[0_12px_32px_rgba(15,23,42,0.16)]"
            >
              <div v-if="isCommandSkillLoading" class="px-3 py-3 text-xs text-slate-400">正在加载技能</div>
              <div v-else-if="filteredCommandSkills.length === 0" class="px-3 py-3 text-xs text-slate-400">没有匹配的技能</div>
              <template v-else>
                <button
                  v-for="(skill, index) in filteredCommandSkills"
                  :key="skill.name"
                  type="button"
                  :class="[
                    'flex w-full items-start gap-2 border-none px-3 py-2.5 text-left transition-colors',
                    index === highlightedSkillIndex ? 'bg-blue-50' : 'bg-white hover:bg-slate-50'
                  ]"
                  @mousedown.prevent="selectCommandSkill(skill)"
                >
                  <i class="fa-solid fa-wand-magic-sparkles mt-0.5 text-xs text-blue-500"></i>
                  <span class="min-w-0 flex-1">
                    <span class="block truncate text-sm font-medium text-slate-800">{{ skill.title || skill.name }}</span>
                    <span class="mt-0.5 block truncate text-xs text-slate-400">{{ skill.description || skill.name }}</span>
                  </span>
                </button>
              </template>
            </div>
          </form>
        </div>
      </div>
    </div>

    <button
      v-if="!sidePanelOpen"
      type="button"
      class="absolute right-3 top-1/2 z-10 flex -translate-y-1/2 flex-col items-center gap-1 rounded-l-xl border border-r-0 border-slate-200 bg-white px-2.5 py-3 text-xs font-medium text-slate-500 shadow-[0_8px_24px_rgba(15,23,42,0.12)] transition-colors hover:bg-blue-50 hover:text-blue-700"
      @click="openConsolePanel"
    >
      <span class="[writing-mode:vertical-rl] tracking-wider">控制台</span>
    </button>

    <aside v-if="sidePanelOpen" class="w-[40%] min-w-[380px] max-w-[580px] border-l border-slate-200 bg-white flex flex-col">
      <div class="h-14 px-4 border-b border-slate-100 flex items-center justify-between">
        <div class="min-w-0">
          <div class="text-sm font-semibold text-slate-800">{{ sidePanelTab === 'console' ? '控制台' : '工具详情' }}</div>
          <div class="mt-0.5 truncate font-mono text-xs text-slate-400">
            {{ sidePanelTab === 'console' ? producedFileTree.root : (activeToolCall?.tool_name || '未选择工具') }}
          </div>
        </div>
        <button
          type="button"
          class="h-8 w-8 rounded-md border border-slate-200 bg-white text-slate-500 hover:bg-slate-50"
          @click="sidePanelOpen = false"
        >
          <i class="fa-solid fa-xmark"></i>
        </button>
      </div>
      <div class="flex-1 overflow-auto p-4">
        <template v-if="sidePanelTab === 'console'">
          <div class="mb-3 rounded-lg border border-slate-100 bg-slate-50 p-3">
            <div class="mb-2 flex items-center justify-between">
              <div class="text-xs font-medium text-slate-500">工作区文件</div>
              <div class="text-[10px] text-slate-400">{{ isWorkspaceFilesLoading ? '加载中' : `${producedFiles.length} 个文件` }}</div>
            </div>
            <div class="rounded-md bg-white px-2 py-2 text-xs">
              <div class="flex h-7 items-center gap-2 rounded px-1.5 font-mono text-slate-700">
                <i class="fa-regular fa-folder-open w-4 text-center text-amber-500"></i>
                <span class="font-medium">{{ producedFileTree.root }}</span>
              </div>
              <div class="mt-1 max-h-[300px] space-y-0.5 overflow-auto pr-1">
                <div
                  v-for="directory in producedFileTree.directories"
                  :key="directory.name"
                >
                  <button
                    type="button"
                    :class="[
                      'flex h-7 w-full items-center gap-1.5 rounded border-none bg-transparent px-1.5 text-left text-slate-700 hover:bg-slate-50',
                      selectedConsoleDirectoryPath === directory.path ? 'bg-blue-50 text-blue-700' : ''
                    ]"
                    @click="selectConsoleDirectoryAndToggle(directory.path)"
                  >
                    <i class="w-3 text-[10px] text-slate-400" :class="isConsoleDirectoryCollapsed(directory.name) ? 'fa-solid fa-chevron-right' : 'fa-solid fa-chevron-down'"></i>
                    <i class="fa-regular w-4 text-center text-amber-500" :class="isConsoleDirectoryCollapsed(directory.name) ? 'fa-folder' : 'fa-folder-open'"></i>
                    <span class="truncate font-mono font-semibold">{{ directory.name }}</span>
                    <span class="shrink-0 text-slate-400">{{ directory.label }}</span>
                    <span v-if="getFileCount(directory)" class="ml-auto shrink-0 rounded bg-slate-100 px-1.5 py-0.5 text-[10px] leading-none text-slate-500">{{ getFileCount(directory) }}</span>
                  </button>
                  <div v-if="directory.children.length && !isConsoleDirectoryCollapsed(directory.name)" class="space-y-0.5">
                    <div
                      v-for="item in flattenFileTreeNodes(directory.children)"
                      :key="item.node.path"
                      class="flex min-w-0 items-center text-slate-600"
                    >
                      <button
                        type="button"
                        v-if="item.node.file"
                        :class="[
                          'flex h-7 min-w-0 flex-1 items-center gap-1.5 rounded border-none bg-transparent px-1.5 text-left text-slate-600 hover:bg-blue-50 hover:text-blue-700',
                          selectedConsoleFilePath === item.node.file.relativePath ? 'bg-blue-50 text-blue-700' : ''
                        ]"
                        :style="{ paddingLeft: `${item.depth * 18 + 10}px` }"
                        @click="selectConsoleFile(item.node.file.relativePath)"
                      >
                        <span class="w-3"></span>
                        <i class="fa-regular fa-file-lines w-4 text-center text-slate-400"></i>
                        <span class="truncate font-mono text-[13px]">{{ item.node.name }}</span>
                      </button>
                      <div
                        v-else
                        role="button"
                        tabindex="0"
                        :class="[
                          'flex h-7 min-w-0 flex-1 items-center gap-1.5 rounded border-none bg-transparent px-1.5 text-left text-slate-600 hover:bg-slate-50',
                          selectedConsoleDirectoryPath === item.node.path ? 'bg-blue-50 text-blue-700' : ''
                        ]"
                        :style="{ paddingLeft: `${item.depth * 18 + 10}px` }"
                        @click="selectConsoleDirectoryAndToggle(item.node.path)"
                        @keydown.enter.prevent="selectConsoleDirectoryAndToggle(item.node.path)"
                        @keydown.space.prevent="selectConsoleDirectoryAndToggle(item.node.path)"
                      >
                        <i class="w-3 text-[10px] text-slate-400" :class="isFileTreeNodeCollapsed(item.node.path) ? 'fa-solid fa-chevron-right' : 'fa-solid fa-chevron-down'"></i>
                        <i class="fa-regular w-4 text-center text-amber-500" :class="isFileTreeNodeCollapsed(item.node.path) ? 'fa-folder' : 'fa-folder-open'"></i>
                        <span class="truncate font-mono text-[13px] font-semibold">{{ item.node.name }}</span>
                        <button
                          v-if="canImportWorkspaceSkillDir(item.node.path)"
                          type="button"
                          :disabled="importingWorkspaceSkillPath === item.node.path"
                          class="ml-auto shrink-0 rounded border-none bg-blue-50 px-2 py-0.5 text-[10px] leading-none text-blue-600 hover:bg-blue-100 disabled:bg-slate-100 disabled:text-slate-400"
                          @click.stop="importWorkspaceSkillDir(item.node.path)"
                        >
                          {{ importingWorkspaceSkillPath === item.node.path ? '导入中' : '导入技能' }}
                        </button>
                        <span v-else-if="getFileCount(item.node)" class="ml-auto shrink-0 rounded bg-slate-100 px-1.5 py-0.5 text-[10px] leading-none text-slate-500">{{ getFileCount(item.node) }}</span>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
              <div v-if="!producedFiles.length" class="mt-2 rounded bg-slate-50 px-2 py-2 text-xs text-slate-400">
                暂无文件
              </div>
            </div>
          </div>

          <div v-if="selectedConsoleFile || selectedConsoleDirectoryPath" class="rounded-lg border border-slate-100 bg-slate-50 p-3">
            <div class="mb-2 flex items-center justify-between gap-2">
              <div class="min-w-0 truncate font-mono text-xs font-medium text-slate-500">
                {{ selectedConsoleFile?.displayPath || formatConsoleDisplayPath(selectedConsoleDirectoryPath) }}
              </div>
              <div class="flex shrink-0 items-center gap-2">
                <div v-if="selectedConsoleFile && (selectedConsoleChartSpec || hasSelectedConsoleTable)" class="flex rounded-md border border-slate-200 bg-white p-0.5">
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
                <div v-if="selectedConsoleFile" class="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500">{{ selectedConsoleFile.source }}</div>
              </div>
            </div>
            <div v-if="!selectedConsoleFile" class="rounded-lg border border-blue-100 bg-blue-50/60 px-3 py-2 text-xs text-slate-500">
              <span v-if="canImportSelectedWorkspaceSkill">检测到技能目录，可导入到技能管理后继续编辑。</span>
              <span v-else>请选择文件查看内容，或点击包含 SKILL.md 的技能根目录进行导入。</span>
            </div>
            <ChartSpecRenderer
              v-else-if="selectedConsoleChartSpec && consoleFilePreviewMode === 'render'"
              :spec="selectedConsoleChartSpec"
            />
            <div v-else-if="hasSelectedConsoleTable && consoleFilePreviewMode === 'table'" class="max-h-[46vh] overflow-auto rounded-md border border-slate-100 bg-white">
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
            <pre v-else class="m-0 max-h-[46vh] overflow-auto whitespace-pre-wrap break-words text-xs leading-relaxed text-slate-700">{{ selectedConsoleFileText || '暂无可预览内容' }}</pre>
          </div>
          <div v-else class="rounded-lg border border-slate-100 bg-slate-50 p-3 text-xs text-slate-400">
            点击工作区文件查看内容，点击技能目录可导入技能。
          </div>
        </template>

        <template v-else>
          <div v-if="activeToolCall" class="space-y-3">
            <div class="rounded-lg border border-slate-100 bg-slate-50 p-3">
              <div class="mb-2 flex items-center justify-between gap-2">
                <div class="text-xs font-medium text-slate-500">参数</div>
                <div class="truncate font-mono text-[10px] text-slate-400">{{ activeToolCall.tool_name }}</div>
              </div>
              <pre class="m-0 whitespace-pre-wrap break-words text-xs leading-relaxed text-slate-700">{{ formatJson(activeToolCall.args) }}</pre>
            </div>
            <div class="rounded-lg border border-slate-100 bg-slate-50 p-3">
              <div class="mb-2 text-xs font-medium text-slate-500">结果</div>
              <pre class="m-0 whitespace-pre-wrap break-words text-xs leading-relaxed text-slate-700">{{ selectedToolResult }}</pre>
              <div v-if="hasActiveToolTablePreview" class="mt-3 overflow-hidden rounded-lg border border-slate-200 bg-white">
                <div class="flex items-center justify-between gap-2 border-b border-slate-100 px-3 py-2">
                  <div class="text-xs font-medium text-slate-600">结果预览</div>
                  <div class="text-[11px] text-slate-400">
                    展示前 {{ activeToolPreviewRows.length }} 行 / 共 {{ activeToolTotalRows }} 行
                  </div>
                </div>
                <div class="max-h-80 overflow-auto">
                  <table class="min-w-full border-separate border-spacing-0 text-left text-xs">
                    <thead class="sticky top-0 z-10 bg-slate-50">
                      <tr>
                        <th
                          v-for="column in activeToolTableColumns"
                          :key="column"
                          class="border-b border-slate-100 px-3 py-2 font-medium text-slate-500"
                        >
                          {{ column }}
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr v-for="(row, rowIndex) in activeToolPreviewRows" :key="rowIndex" class="hover:bg-slate-50">
                        <td
                          v-for="column in activeToolTableColumns"
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
              </div>
            </div>
          </div>
          <div v-else class="rounded-lg border border-slate-100 bg-slate-50 p-4 text-sm text-slate-400">
            从消息中选择一个工具调用查看参数和结果。
          </div>
        </template>
      </div>
    </aside>

    <el-dialog v-model="isSkillManagerOpen" width="min(1280px, 92vw)" title="技能管理" append-to-body>
      <div class="h-[640px] overflow-hidden">
        <SkillManager :initial-skill-id="importedSkillIdForManager" default-skill-scope="analyze" agent="analyze" />
      </div>
    </el-dialog>
  </div>
</template>

<style scoped>
.analysis-markdown :deep(p) {
  margin: 0 0 8px;
}

.analysis-markdown :deep(p:last-child) {
  margin-bottom: 0;
}

.analysis-markdown {
  min-width: 0;
  max-width: 100%;
  overflow-wrap: anywhere;
}

.analysis-markdown :deep(pre) {
  max-width: 100%;
  overflow-x: auto;
  margin: 8px 0;
  padding: 12px;
  border-radius: 8px;
  border: 1px solid #e2e8f0;
  background: #f8fafc;
  white-space: pre;
}

.analysis-markdown :deep(pre code) {
  display: block;
  min-width: max-content;
  padding: 0;
  background: transparent;
  white-space: pre;
  overflow-wrap: normal;
  word-break: normal;
  font-size: 12px;
  line-height: 1.6;
  color: #334155;
}

.analysis-markdown :deep(:not(pre) > code) {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.analysis-markdown :deep(table) {
  width: 100%;
  max-width: 100%;
  margin: 10px 0;
  border-collapse: collapse;
  table-layout: fixed;
}

.analysis-markdown :deep(th),
.analysis-markdown :deep(td) {
  border: 1px solid #d8dee8;
  padding: 8px 10px;
  text-align: left;
  vertical-align: top;
  overflow-wrap: anywhere;
  word-break: break-word;
}

.analysis-markdown :deep(th) {
  background: #f8fafc;
  color: #334155;
  font-weight: 700;
}

@keyframes breathe {
  0%, 100% {
    box-shadow: 0 2px 8px rgba(0, 122, 255, 0.35), 0 0 0 0 rgba(22, 119, 255, 0.28);
  }
  50% {
    box-shadow: 0 4px 14px rgba(0, 122, 255, 0.45), 0 0 0 6px rgba(22, 119, 255, 0.08);
  }
}

.animate-breathe {
  animation: breathe 1.4s ease-in-out infinite;
}

.execution-detail-line::before {
  content: '';
  position: absolute;
  left: -25px;
  top: -12px;
  bottom: -12px;
  width: 1px;
  background-image: repeating-linear-gradient(to bottom, #D8DAE5 0 3px, transparent 3px 6px);
}
</style>
