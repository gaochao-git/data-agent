<script setup lang="ts">
/**
 * ChatData 智能问数主页面
 * 参考 React 版本布局，支持四种模式：数据智能体、可视化查询、SQL查询、数据看板
 */
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Close } from '@element-plus/icons-vue'
import useUserStore from '@/store/modules/user'
import { getDatasources, getConversations, createConversation, generateConversationTitle, getSemanticConfigs, getDatasourceSchema } from '../api'
import { listDashboards } from '../api/dashboard'
import { initChatDataAuth } from '../utils/auth'
import type { DataSource, ViewMode, ConversationItem, ChatDataOptions, SemanticConfigItem, ExecutionTargetOption, TableInfo } from '../types'
import SqlConversation from '../components/SqlConversation.vue'
import SqlEditor from '../components/SqlEditor.vue'
import QueryBuilder from '../components/QueryBuilder/index.vue'
import ExecutionTargetPicker from '../components/ExecutionTargetPicker.vue'
import DashboardPanel from '../components/sidebars/DashboardPanel.vue'
import ConversationSidebar from '../components/sidebars/ConversationSidebar.vue'
import BuilderSidebar from '../components/sidebars/BuilderSidebar.vue'
import EditorSidebar from '../components/sidebars/EditorSidebar.vue'
import DashboardEditor from '../components/Dashboard/DashboardEditor.vue'
import SemanticConfigPanel from '../components/SemanticConfigPanel.vue'
import DataSourceManager from '../components/DataSourceManager.vue'
import IconModeConversation from '../components/icons/IconModeConversation.vue'
import IconModeBuilder from '../components/icons/IconModeBuilder.vue'
import IconModeEditor from '../components/icons/IconModeEditor.vue'

// 模式图标组件映射（经典样式使用）
const MODE_ICON_COMPONENTS: Record<string, typeof IconModeConversation> = {
  conversation: IconModeConversation,
  builder: IconModeBuilder,
  editor: IconModeEditor,
}

// 外部可传入的配置（JSON 对象，方便扩展）
const props = defineProps<{
  options?: ChatDataOptions
}>()
const route = useRoute()
const userStore = useUserStore()

const DEFAULT_MODE_LABELS: Record<ViewMode, string> = {
  conversation: '数据智能体',
  builder: '可视化查询',
  editor: 'SQL查询',
  dashboard: '数据看板',
}

const resolveModeLabels = (): Record<ViewMode, string> => {
  const labels = props.options?.modeLabels
  if (!labels) return DEFAULT_MODE_LABELS
  const required: ViewMode[] = ['conversation', 'builder', 'editor', 'dashboard']
  const allProvided = required.every((key) => typeof labels[key] === 'string' && labels[key].trim() !== '')
  if (!allProvided) {
    console.warn('[ChatData] options.modeLabels 需要完整传入四个模式名称，已回退默认名称')
    return DEFAULT_MODE_LABELS
  }
  return {
    conversation: labels.conversation.trim(),
    builder: labels.builder.trim(),
    editor: labels.editor.trim(),
    dashboard: labels.dashboard.trim(),
  }
}

const modeLabels = computed<Record<ViewMode, string>>(() => resolveModeLabels())
const preferredDashboardName = computed(() => (props.options?.defaultDashboardName || '').trim())

const parentComponentName = computed(() => {
  if (props.options?.parentComponentName) return props.options.parentComponentName
  const queryValue = route.query.parentComponentName
  if (Array.isArray(queryValue)) return queryValue[0] || ''
  return typeof queryValue === 'string' ? queryValue : ''
})

const currentUser = computed(() => ({
  id: String(userStore.id || '').trim(),
  name: String(userStore.name || '').trim(),
}))

// 数据源
const datasources = ref<DataSource[]>([])
const selectedDatasourceId = ref<string>('')
const loadingDatasources = ref(true)
const noMatchedDatasource = ref(false)

const noMatchedDatasourceTitle = computed(() => {
  if (parentComponentName.value) {
    return `当前模块（${parentComponentName.value}）未配置数据源`
  }
  return '当前配置未匹配到数据源'
})
const noMatchedDatasourceDesc = computed(() => {
  if (parentComponentName.value) {
    return '请在数据源管理中配置模块映射'
  }
  return '请在数据源管理中添加可用数据源'
})
// 数据源管理弹窗
const showDatasourceModal = ref(false)
const datasourceManagerRef = ref<InstanceType<typeof DataSourceManager> | null>(null)

// 打开新建数据源组弹窗
const openDatasourceGroupModal = () => {
  datasourceManagerRef.value?.openGroupModal()
}

// 数据源管理回调：更新本地数据源列表
const handleDatasourcesChange = (newDatasources: DataSource[]) => {
  datasources.value = applyDatasourceFilters(newDatasources)
  // 如果当前选中的数据源不在新列表中，重新选择
  if (datasources.value.length > 0 && !datasources.value.find(ds => ds.id === selectedDatasourceId.value)) {
    selectedDatasourceId.value = datasources.value[0].id
  } else if (datasources.value.length === 0) {
    selectedDatasourceId.value = ''
  }
  // 更新未匹配状态
  noMatchedDatasource.value = shouldShowNoMatchedDatasource(datasources.value)
}

const applyDatasourceFilters = (allDatasources: DataSource[]): DataSource[] => {
  const parentName = parentComponentName.value
  if (!parentName) return []

  return allDatasources.filter((ds) => {
    const parentMatched = ds.mapped_modules?.includes(parentName)
    return parentMatched
  })
}

const shouldShowNoMatchedDatasource = (filtered: DataSource[]): boolean => {
  return filtered.length === 0
}

// 模式列表
const allModes = computed(() => ([
  { key: 'conversation' as ViewMode, label: modeLabels.value.conversation },
  { key: 'builder' as ViewMode, label: modeLabels.value.builder },
  { key: 'editor' as ViewMode, label: modeLabels.value.editor },
  { key: 'dashboard' as ViewMode, label: modeLabels.value.dashboard },
]))

// 过滤隐藏模式
const visibleModes = computed(() => {
  const hiddenRaw = props.options?.hideTabs || []
  const keySet = new Set<ViewMode>()
  const labelToKey = new Map<string, ViewMode>(
    allModes.value.map((m) => [m.label, m.key])
  )
  for (const item of hiddenRaw) {
    const value = String(item || '').trim()
    if (!value) continue
    if (value === 'conversation' || value === 'builder' || value === 'editor' || value === 'dashboard') {
      keySet.add(value)
      continue
    }
    const mapped = labelToKey.get(value)
    if (mapped) keySet.add(mapped)
  }
  const filtered = allModes.value.filter(mode => !keySet.has(mode.key))
  return filtered.length > 0 ? filtered : [...allModes.value]
})

const resolveInitialMode = (): ViewMode => {
  const preferred = props.options?.defaultMode || 'conversation'
  const isVisible = visibleModes.value.some(mode => mode.key === preferred)
  return isVisible ? preferred : visibleModes.value[0]!.key
}

// 视图模式
const viewMode = ref<ViewMode>(resolveInitialMode())
const builderMounted = ref(viewMode.value === 'builder')
const editorMounted = ref(viewMode.value === 'editor')
const dashboardMounted = ref(viewMode.value === 'dashboard')

// 选中的数据集 ID
const selectedSemanticConfigId = ref<string | null>(null)
const semanticConfigs = ref<SemanticConfigItem[]>([])
const loadingSemanticConfigs = ref(false)
const datasourceSchemaCache = ref<Record<string, TableInfo[]>>({})
const loadingDatasourceSchema = ref(false)

// 侧边栏可见性
const isSidebarVisible = ref(true)
const sidebarBodyRef = ref<HTMLElement | null>(null)
const sidebarCollapseTop = ref(15)

// 侧边栏宽度拖拽调整
const sidebarWidth = ref(280)
const isResizingSidebar = ref(false)

const startSidebarResize = (e: MouseEvent) => {
  e.preventDefault()
  e.stopPropagation()
  isResizingSidebar.value = true
  const startX = e.clientX
  const startWidth = sidebarWidth.value

  const onMove = (ev: MouseEvent) => {
    ev.preventDefault()
    const delta = ev.clientX - startX
    let newWidth = startWidth + delta
    newWidth = Math.max(200, Math.min(500, newWidth))
    sidebarWidth.value = newWidth
  }

  const onUp = () => {
    isResizingSidebar.value = false
    document.removeEventListener('mousemove', onMove, true)
    document.removeEventListener('mouseup', onUp, true)
  }

  document.addEventListener('mousemove', onMove, true)
  document.addEventListener('mouseup', onUp, true)
}

// 侧边栏 Tab（公用）
const activeSidebarTab = ref<string>('metrics')

const sidebarTabs = computed(() => {
  const tabs: Array<{ key: string; label: string; icon: string }> = []
  if (viewMode.value === 'conversation') {
    tabs.push({ key: 'metrics', label: '数据集', icon: 'fa-bullseye' })
  } else if (viewMode.value === 'dashboard') {
    tabs.push({ key: 'dashboard', label: '数据看板', icon: 'fa-chart-pie' })
  } else if (viewMode.value === 'builder') {
    tabs.push({ key: 'tables', label: '数据集', icon: 'fa-database' })
  } else {
    tabs.push({ key: 'tables', label: '数据表', icon: 'fa-database' })
  }
  return tabs
})

// 计算侧边栏收起按钮垂直位置：跟随当前可见面板首个控件中心点
const updateSidebarCollapseTop = async () => {
  await nextTick()
  const root = sidebarBodyRef.value
  if (!root) return
  const rootRect = root.getBoundingClientRect()
  const btnHalfHeight = 10
  const shellPaddingTop = 6
  const baselineControlHeight = 28
  const visibleShell = Array.from(root.querySelectorAll<HTMLElement>('.sidebar-unified-shell')).find((el) => {
    if (!el.isConnected) return false
    if (el.offsetParent === null) return false
    const rect = el.getBoundingClientRect()
    return rect.width > 0 && rect.height > 0
  }) || null
  if (visibleShell) {
    const shellRect = visibleShell.getBoundingClientRect()
    const center = shellRect.top - rootRect.top + shellPaddingTop + baselineControlHeight / 2
    sidebarCollapseTop.value = Math.max(6, Math.round(center - btnHalfHeight))
    return
  }
  const candidates = Array.from(root.querySelectorAll<HTMLElement>('.sidebar-baseline-target'))
  const target = candidates.find((el) => {
    if (!el.isConnected) return false
    if (el.offsetParent === null) return false
    const rect = el.getBoundingClientRect()
    return rect.width > 0 && rect.height > 0
  }) || null
  if (!target) {
    sidebarCollapseTop.value = 15
    return
  }
  const targetRect = target.getBoundingClientRect()
  const center = targetRect.top - rootRect.top + targetRect.height / 2
  sidebarCollapseTop.value = Math.max(6, Math.round(center - btnHalfHeight))
}

// 看板编辑器状态
const activeDashboardId = ref<string | null>(null)
const loadingDefaultDashboard = ref(false)
const dashboards = ref<Array<{ id: string; name?: string }>>([])

async function ensureDefaultDashboard() {
  if (viewMode.value !== 'dashboard') return
  if (loadingDefaultDashboard.value) return
  loadingDefaultDashboard.value = true
  try {
    const dashboardList = await listDashboards()
    dashboards.value = dashboardList
    if (viewMode.value === 'dashboard' && !activeDashboardId.value && dashboardList.length > 0) {
      const matched = preferredDashboardName.value
        ? dashboardList.find((d) => (d.name || '').trim() === preferredDashboardName.value)
        : null
      activeDashboardId.value = (matched || dashboardList[0])!.id
    }
  } catch (err) {
    console.error('加载默认看板失败:', err)
  } finally {
    loadingDefaultDashboard.value = false
  }
}

// 切换模式时保持看板 tab，否则切到默认 tab
watch(viewMode, () => {
  if (viewMode.value === 'builder') {
    builderMounted.value = true
  } else if (viewMode.value === 'editor') {
    editorMounted.value = true
  } else if (viewMode.value === 'dashboard') {
    dashboardMounted.value = true
  }
  if (viewMode.value === 'conversation') {
    activeSidebarTab.value = 'metrics'
    return
  }
  if (viewMode.value === 'dashboard') {
    activeSidebarTab.value = 'dashboard'
    ensureDefaultDashboard()
    return
  }
  activeSidebarTab.value = 'tables'
}, { immediate: true })

// 打开看板编辑器
const handleOpenDashboard = (dashboardId: string) => {
  activeDashboardId.value = dashboardId
}

const handleDashboardsChange = (nextDashboards: Array<{ id: string; name?: string }>) => {
  dashboards.value = nextDashboards
  if (dashboards.value.length === 0) {
    activeDashboardId.value = null
    return
  }
  if (!activeDashboardId.value || !dashboards.value.some(item => item.id === activeDashboardId.value)) {
    activeDashboardId.value = dashboards.value[0]!.id
  }
}

// 关闭看板编辑器，返回会话页面
const handleCloseDashboard = () => {
  activeDashboardId.value = null
  viewMode.value = 'dashboard'
}

// 语义配置面板状态
const isSemanticConfigPanelOpen = ref(false)

// 打开语义配置面板
const handleOpenSemanticConfig = (datasourceId?: string) => {
  if (datasourceId) {
    selectedDatasourceId.value = datasourceId
  }
  showDatasourceModal.value = false
  isSemanticConfigPanelOpen.value = true
}

// 关闭语义配置面板
const handleCloseSemanticConfig = () => {
  isSemanticConfigPanelOpen.value = false
}

// 会话
const conversations = ref<ConversationItem[]>([])
const currentConversationId = ref<string | null>(null)
const currentConversationTitle = ref<string>('新会话')
const isLoadingConversations = ref(false)
const conversationKey = ref(0) // 用于强制重新渲染 SqlConversation
const hasAnalysisPanel = ref(false)
const rightConversationPanelMode = ref<'conversation' | 'analysis'>('conversation')
const showConversationListPanel = ref(true)
const showHistoryPopover = ref(false)
const hasEnteredConversationOnce = ref(false)
const conversationsLoaded = ref(false)
let historyPopoverHideTimer: ReturnType<typeof setTimeout> | null = null

const openHistoryPopover = () => {
  if (historyPopoverHideTimer) {
    clearTimeout(historyPopoverHideTimer)
    historyPopoverHideTimer = null
  }
  showHistoryPopover.value = true
}

const closeHistoryPopoverWithDelay = () => {
  if (historyPopoverHideTimer) clearTimeout(historyPopoverHideTimer)
  historyPopoverHideTimer = setTimeout(() => {
    showHistoryPopover.value = false
    historyPopoverHideTimer = null
  }, 180)
}

const canShowNewConversationButton = computed(() => {
  return hasEnteredConversationOnce.value
})

onUnmounted(() => {
})

const switchConversationRightPanel = () => {
  if (!hasAnalysisPanel.value) return
  rightConversationPanelMode.value = rightConversationPanelMode.value === 'conversation' ? 'analysis' : 'conversation'
  if (rightConversationPanelMode.value === 'conversation') {
    showConversationListPanel.value = true
  } else {
    showConversationListPanel.value = false
  }
}

const switchToConversationPanel = () => {
  rightConversationPanelMode.value = 'conversation'
  showConversationListPanel.value = true
}

const switchToAnalysisPanel = () => {
  if (!hasAnalysisPanel.value) return
  rightConversationPanelMode.value = 'analysis'
  showConversationListPanel.value = false
}

const handleAnalysisPanelChange = (open: boolean) => {
  hasAnalysisPanel.value = open
  if (open) {
    rightConversationPanelMode.value = 'analysis'
    showConversationListPanel.value = false
    return
  }
  if (rightConversationPanelMode.value === 'analysis') {
    rightConversationPanelMode.value = 'conversation'
    showConversationListPanel.value = true
  }
}

// SqlEditor 组件引用
const sqlEditorRef = ref<InstanceType<typeof SqlEditor> | null>(null)

// 加载数据源
const loadDatasources = async () => {
  loadingDatasources.value = true
  try {
    const res = await getDatasources({ include_tables: false })
    if (res.status === 'success') {
      const allDatasources = res.data.datasources || []
      datasources.value = applyDatasourceFilters(allDatasources)
      noMatchedDatasource.value = shouldShowNoMatchedDatasource(datasources.value)
      if (datasources.value.length > 0 && !selectedDatasourceId.value) {
        const defaultDs = datasources.value.find(ds => ds.is_default)
        selectedDatasourceId.value = defaultDs?.id || datasources.value[0].id || ''
      }
    }
  } catch (err) {
    console.error('加载数据源失败:', err)
  } finally {
    loadingDatasources.value = false
  }
}

const loadDatasourceSchema = async (datasourceId: string, force = false) => {
  const normalizedDatasourceId = String(datasourceId || '').trim()
  if (!normalizedDatasourceId) return
  if (!force && datasourceSchemaCache.value[normalizedDatasourceId]) return
  loadingDatasourceSchema.value = true
  try {
    const res = await getDatasourceSchema(normalizedDatasourceId)
    if (res.status === 'success') {
      datasourceSchemaCache.value = {
        ...datasourceSchemaCache.value,
        [normalizedDatasourceId]: res.data?.tables || [],
      }
    }
  } catch (err) {
    console.error('加载数据源表结构失败:', err)
  } finally {
    loadingDatasourceSchema.value = false
  }
}

// 加载会话列表
const loadConversations = async () => {
  isLoadingConversations.value = true
  try {
    const res = await getConversations('chat_data')
    if (res.status === 'success') {
      conversations.value = (res.data || []).map((conv: any) => ({
        id: conv.id || conv.conversation_id,
        title: conv.title || conv.first_message_preview || '未命名会话',
        created_at: conv.update_time || conv.create_time || '',
      }))
    }
    conversationsLoaded.value = true
  } catch (err) {
    console.error('加载会话失败:', err)
  } finally {
    isLoadingConversations.value = false
  }
}

const ensureConversationsLoaded = async (force = false) => {
  if (isLoadingConversations.value) return
  if (!force && conversationsLoaded.value) return
  await loadConversations()
}

// 确保会话 ID 存在（懒创建模式，只在发送消息时才创建）
const ensureConversationId = async (): Promise<string | null> => {
  if (currentConversationId.value) {
    return currentConversationId.value
  }
  try {
    const res = await createConversation({ agent_id: 'chat_data' })
    if (res.status === 'success' && res.data?.id) {
      currentConversationId.value = res.data.id
      return res.data.id
    }
  } catch (err) {
    console.error('创建会话失败:', err)
  }
  return null
}

// 会话分组
const groupedConversations = computed(() => {
  if (conversations.value.length === 0) return []

  const now = new Date()
  const groups: { label: string; items: ConversationItem[] }[] = []
  const groupMap: Record<string, ConversationItem[]> = {}

  const getGroupLabel = (dateStr: string): string => {
    if (!dateStr) return '更早'
    const date = new Date(dateStr.replace(' ', 'T'))
    if (isNaN(date.getTime())) return '更早'

    const diffMs = now.getTime() - date.getTime()
    const diffDays = diffMs / (1000 * 60 * 60 * 24)

    if (diffDays < 7) return '7天内'
    if (diffDays < 30) return '30天内'
    if (diffDays < 183) return '半年内'
    return '更早'
  }

  for (const conv of conversations.value) {
    const label = getGroupLabel(conv.created_at)
    if (!groupMap[label]) groupMap[label] = []
    groupMap[label].push(conv)
  }

  const order = ['7天内', '30天内', '半年内', '更早']
  for (const label of order) {
    const items = groupMap[label]
    if (items && items.length > 0) {
      groups.push({ label, items })
    }
  }

  return groups
})

// 切换模式
const changeMode = (mode: ViewMode) => {
  if (!visibleModes.value.some(item => item.key === mode)) return
  viewMode.value = mode
}

watch(visibleModes, () => {
  if (!visibleModes.value.some(mode => mode.key === viewMode.value)) {
    viewMode.value = visibleModes.value[0]!.key
  }
})

// 新建会话（只清空状态，不立即创建，等发送第一条消息时才创建）
const handleNewConversation = () => {
  currentConversationId.value = null
  currentConversationTitle.value = '新会话'
  conversationKey.value++ // 强制重新渲染以清空消息
}

// 选择会话
const selectConversation = (id: string) => {
  currentConversationId.value = id
  hasEnteredConversationOnce.value = true
  const conv = conversations.value.find(c => c.id === id)
  currentConversationTitle.value = conv?.title || '会话'
  conversationKey.value++ // 强制重新渲染以加载历史
}

// 首条消息完成后的回调
const handleFirstMessageComplete = async (convId: string) => {
  hasEnteredConversationOnce.value = true
  try {
    // 生成会话标题
    const res = await generateConversationTitle(convId)
    if (res.status === 'success' && res.data?.title) {
      const newTitle = res.data.title
      // 更新当前会话标题
      currentConversationTitle.value = newTitle
      // 更新会话列表中的标题
      const convIndex = conversations.value.findIndex(c => c.id === convId)
      if (convIndex >= 0) {
        conversations.value[convIndex].title = newTitle
      }
    }
  } catch (error) {
    console.error('生成标题失败:', error)
  }
  // 刷新会话列表
  ensureConversationsLoaded(true)
}

// 数据源变更
const handleDatasourceChange = (dsId: string) => {
  selectedDatasourceId.value = dsId
}

const loadSemanticConfigs = async () => {
  if (!selectedDatasourceId.value) {
    semanticConfigs.value = []
    selectedSemanticConfigId.value = null
    return
  }
  loadingSemanticConfigs.value = true
  try {
    const res = await getSemanticConfigs(selectedDatasourceId.value)
    if (res.status === 'success') {
      semanticConfigs.value = res.data?.configs || []
      if (!semanticConfigs.value.find(c => c.id === selectedSemanticConfigId.value)) {
        const enabledConfig = semanticConfigs.value.find(c => c.enabled)
        selectedSemanticConfigId.value = enabledConfig?.id || semanticConfigs.value[0]?.id || null
      }
    }
  } catch (err) {
    console.error('加载数据集失败:', err)
    semanticConfigs.value = []
    selectedSemanticConfigId.value = null
  } finally {
    loadingSemanticConfigs.value = false
  }
}

// 当前选中的数据源
const currentDatasource = computed(() => {
  const datasource = datasources.value.find(ds => ds.id === selectedDatasourceId.value) || null
  if (!datasource) return null
  const cachedTables = datasourceSchemaCache.value[datasource.id]
  if (!cachedTables) return datasource
  return {
    ...datasource,
    ds_tables: cachedTables,
  }
})

const currentSemanticConfig = computed(() => {
  if (!selectedSemanticConfigId.value) return null
  return semanticConfigs.value.find(cfg => cfg.id === selectedSemanticConfigId.value) || null
})

const conversationSidebarBootstrapLoading = computed(() => {
  if (loadingDatasources.value) return true
  if (!selectedDatasourceId.value && datasources.value.length > 0) return true
  if (!!selectedDatasourceId.value && loadingSemanticConfigs.value) return true
  return false
})

const buildExecutionTargets = (datasource: DataSource | null): ExecutionTargetOption[] => {
  const members = datasource?.member_sources || []
  if (members.length > 0) {
    return members
      .filter(member => member.member_datasource_id)
      .map(member => ({
        member_datasource_id: String(member.member_datasource_id),
        datasource_name: member.datasource_name || '',
        source_type: member.source_type || datasource?.source_type || '',
        host: member.host,
        port: member.port,
        database: member.database,
        label: member.datasource_name || `${member.host}:${member.port}/${member.database}`,
      }))
  }
  if (!datasource?.id) return []
  return [{
    member_datasource_id: datasource.template_datasource_id || datasource.id,
    datasource_name: datasource.ds_name || datasource.id,
    source_type: datasource.source_type || '',
    host: datasource.source_config?.host as string | undefined,
    port: datasource.source_config?.port as number | undefined,
    database: datasource.source_config?.database as string | undefined,
    label: datasource.ds_name || datasource.id,
  }]
}

const executionTargets = computed<ExecutionTargetOption[]>(() => buildExecutionTargets(currentDatasource.value))
const executionTargetMultiple = ref(false)

const selectedMemberDatasourceIds = ref<string[]>([])

const resolveSelectedExecutionTargetIds = (
  targets: ExecutionTargetOption[],
  currentIds: string[],
  templateId?: string,
  multiple = true
): string[] => {
  const availableIds = targets.map(item => item.member_datasource_id)
  if (availableIds.length === 0) {
    return []
  }

  const retainedIds = currentIds.filter(id => availableIds.includes(id))
  if (retainedIds.length > 0) {
    return multiple ? retainedIds : [retainedIds[0]!]
  }

  if (templateId && availableIds.includes(templateId)) {
    return [templateId]
  }

  return [availableIds[0]!]
}

const isSameStringArray = (left: string[], right: string[]) => {
  if (left.length !== right.length) return false
  return left.every((item, index) => item === right[index])
}

const syncSelectedExecutionTargets = () => {
  const nextIds = resolveSelectedExecutionTargetIds(
    executionTargets.value,
    selectedMemberDatasourceIds.value,
    currentDatasource.value?.template_datasource_id,
    executionTargetMultiple.value
  )
  if (isSameStringArray(nextIds, selectedMemberDatasourceIds.value)) return
  selectedMemberDatasourceIds.value = nextIds
}

watch(
  () => [
    selectedDatasourceId.value,
    currentDatasource.value?.template_datasource_id,
    executionTargets.value.map(item => item.member_datasource_id).join(','),
  ],
  () => {
    syncSelectedExecutionTargets()
  },
  { immediate: true }
)

const selectedExecutionTargetSummary = computed(() => {
  const selectedTargets = executionTargets.value.filter(item => selectedMemberDatasourceIds.value.includes(item.member_datasource_id))
  if (selectedTargets.length === 0) return '未选择执行对象'
  if (selectedTargets.length === 1) return selectedTargets[0]?.label || '已选择 1 个执行对象'
  return `${selectedTargets[0]?.label || '已选择'} 等 ${selectedTargets.length} 个`
})

const showContextSelectorDialog = ref(false)
const draftDatasourceId = ref('')
const draftExecutionTargetMultiple = ref(false)
const draftSelectedMemberDatasourceIds = ref<string[]>([])

const draftDatasource = computed(() => {
  return datasources.value.find(ds => ds.id === draftDatasourceId.value) || null
})

const draftExecutionTargets = computed<ExecutionTargetOption[]>(() => buildExecutionTargets(draftDatasource.value))

const openContextSelector = () => {
  draftDatasourceId.value = selectedDatasourceId.value
  draftExecutionTargetMultiple.value = executionTargetMultiple.value
  draftSelectedMemberDatasourceIds.value = [...selectedMemberDatasourceIds.value]
  showContextSelectorDialog.value = true
}

watch(
  () => [draftDatasourceId.value, draftExecutionTargets.value.map(item => item.member_datasource_id).join(',')],
  () => {
    const nextIds = resolveSelectedExecutionTargetIds(
      draftExecutionTargets.value,
      draftSelectedMemberDatasourceIds.value,
      draftDatasource.value?.template_datasource_id,
      draftExecutionTargetMultiple.value
    )
    if (isSameStringArray(nextIds, draftSelectedMemberDatasourceIds.value)) return
    draftSelectedMemberDatasourceIds.value = nextIds
  }
)

const handleConfirmContextSelector = () => {
  if (draftExecutionTargets.value.length > 0 && draftSelectedMemberDatasourceIds.value.length === 0) {
    ElMessage.warning('请选择执行对象')
    return
  }
  executionTargetMultiple.value = draftExecutionTargetMultiple.value
  selectedDatasourceId.value = draftDatasourceId.value
  const nextDatasource = datasources.value.find(ds => ds.id === draftDatasourceId.value) || null
  const nextTargets = buildExecutionTargets(nextDatasource)
  selectedMemberDatasourceIds.value = resolveSelectedExecutionTargetIds(
    nextTargets,
    draftSelectedMemberDatasourceIds.value,
    nextDatasource?.template_datasource_id,
    draftExecutionTargetMultiple.value
  )
  showContextSelectorDialog.value = false
}

const currentContextSummary = computed(() => {
  const datasourceName = currentDatasource.value?.ds_name || '暂无数据源'
  return `${datasourceName} / ${selectedExecutionTargetSummary.value}`
})

const showSidebarContextBar = computed(() => {
  return datasources.value.length > 0 || executionTargets.value.length > 0
})

watch(selectedDatasourceId, () => {
  loadSemanticConfigs()
}, { immediate: true })

watch(
  [viewMode, activeSidebarTab],
  ([mode, tab]) => {
    if (mode === 'conversation' && tab === 'conversation') {
      void ensureConversationsLoaded()
    }
  },
  { immediate: true }
)

watch(
  [selectedDatasourceId, viewMode, isSemanticConfigPanelOpen],
  ([datasourceId, mode, semanticPanelOpen]) => {
    if (!datasourceId) return
    if (semanticPanelOpen || mode === 'builder' || mode === 'editor') {
      void loadDatasourceSchema(datasourceId)
    }
  },
  { immediate: true }
)

watch(draftExecutionTargetMultiple, () => {
  const nextIds = resolveSelectedExecutionTargetIds(
    draftExecutionTargets.value,
    draftSelectedMemberDatasourceIds.value,
    draftDatasource.value?.template_datasource_id,
    draftExecutionTargetMultiple.value
  )
  if (isSameStringArray(nextIds, draftSelectedMemberDatasourceIds.value)) return
  draftSelectedMemberDatasourceIds.value = nextIds
})

watch(
  [viewMode, activeSidebarTab, isSidebarVisible, selectedDatasourceId, selectedSemanticConfigId],
  () => {
    updateSidebarCollapseTop()
  },
  { immediate: true }
)

// 表结构 schema (用于 SQL 编辑器自动补全)
const tableSchema = computed(() => {
  if (!currentDatasource.value?.ds_tables) return {}
  const schema: Record<string, string[]> = {}
  currentDatasource.value.ds_tables.forEach(table => {
    schema[table.table_name] = table.columns?.map(col => col.name) || []
  })
  return schema
})

// QueryBuilder 组件引用
const queryBuilderRef = ref<InstanceType<typeof QueryBuilder> | null>(null)

// BuilderSidebar 拖拽事件处理
const handleBuilderDragStart = (type: 'table' | 'field', data: any, event: DragEvent) => {
  if (queryBuilderRef.value) {
    queryBuilderRef.value.handleSidebarDragStart(type, data, event)
  }
}

const handleBuilderDragEnd = () => {
  if (queryBuilderRef.value) {
    queryBuilderRef.value.handleSidebarDragEnd()
  }
}

// EditorSidebar 插入事件处理
const handleInsertTable = (tableName: string) => {
  if (sqlEditorRef.value) {
    sqlEditorRef.value.insertTableName(tableName)
    sqlEditorRef.value.focusEditor?.()
  }
}

const handleInsertColumn = (tableName: string, columnName: string) => {
  if (sqlEditorRef.value) {
    sqlEditorRef.value.insertColumnName(tableName, columnName)
    sqlEditorRef.value.focusEditor?.()
  }
}

onMounted(async () => {
  // 自动登录（获取 inner_token cookie），确保会话接口有认证
  let userId = String(currentUser.value.id || '').trim()
  let userName = String(currentUser.value.name || '').trim()
  if ((!userId || !userName) && typeof userStore.getInfo === 'function') {
    await userStore.getInfo()
    userId = String(currentUser.value.id || '').trim()
    userName = String(currentUser.value.name || '').trim()
  }
  if (!userId || !userName) {
    ElMessage.error('未获取到当前登录用户')
    return
  }
  await initChatDataAuth({ userId, userName })
  loadDatasources()
  // 不立即创建会话，等发送第一条消息时才创建
  window.addEventListener('resize', updateSidebarCollapseTop)
  updateSidebarCollapseTop()
})

onUnmounted(() => {
  window.removeEventListener('resize', updateSidebarCollapseTop)
  if (historyPopoverHideTimer) {
    clearTimeout(historyPopoverHideTimer)
    historyPopoverHideTimer = null
  }
})
</script>

<template>
  <!-- 高度 = 视口 - 主项目头部(导航栏50+标签栏32=82px)，按实际调整 -->
  <div
    class="chatdata-root flex flex-col w-full"
    style="height: calc(100vh - 82px); background: linear-gradient(to bottom, #F9FAFB 0%, #F3F4F6 100%)"
  >
    <!-- 未匹配数据源提示 -->
    <div v-if="noMatchedDatasource" class="flex-1 flex items-center justify-center">
      <div class="text-center text-gray-400">
        <i class="fa-solid fa-database text-4xl mb-4 block"></i>
        <p class="text-sm">{{ noMatchedDatasourceTitle }}</p>
        <p class="text-xs mt-1">{{ noMatchedDatasourceDesc }}</p>
        <button
          v-hasPermi="['chatdata:datasource:manage']"
          class="mt-4 inline-flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-4 h-9 text-sm text-slate-700 shadow-sm transition-all hover:bg-slate-50 hover:text-slate-900"
          @click="showDatasourceModal = true"
        >
          <i class="fa-solid fa-cog text-xs"></i>
          配置数据源
        </button>
      </div>
    </div>

    <!-- 顶部工具栏 -->
    <div v-if="!noMatchedDatasource" class="h-10 px-5 flex items-end bg-[#EEF1F5] border-b border-[#B8C1CE]">
      <div class="flex items-center justify-between w-full min-w-0">
        <div class="mode-trapezoid shrink-0">
          <button
            v-for="mode in visibleModes"
            :key="mode.key"
            :class="[
              'mode-trapezoid-item',
              mode === visibleModes[0] ? 'is-first' : '',
              mode === visibleModes[visibleModes.length - 1] ? 'is-last' : '',
              viewMode === mode.key ? 'is-active' : ''
            ]"
            @click="changeMode(mode.key as ViewMode)"
          >
            <span>{{ mode.label }}</span>
          </button>
        </div>

        <div
          v-if="viewMode === 'conversation'"
          class="relative shrink-0 flex items-center gap-2"
        >
          <button
            class="w-8 h-8 flex items-center justify-center border border-slate-200 rounded-xl bg-white text-slate-500 cursor-pointer shadow-sm hover:text-slate-700 hover:bg-slate-50"
            title="历史会话"
            @mouseenter="openHistoryPopover"
            @mouseleave="closeHistoryPopoverWithDelay"
            @click.stop="showHistoryPopover = !showHistoryPopover"
          >
            <i class="fa-solid fa-clock-rotate-left text-[12px]"></i>
          </button>
          <button
            v-if="canShowNewConversationButton"
            class="w-8 h-8 flex items-center justify-center border border-slate-200 rounded-xl bg-white text-slate-700 text-sm cursor-pointer shadow-sm hover:bg-slate-50"
            title="新对话"
            @click="handleNewConversation"
          >
            <i class="fa-solid fa-circle-plus text-[13px]"></i>
          </button>

          <div
            v-if="showHistoryPopover"
            class="absolute right-0 top-full mt-2 z-[140] w-[360px] max-h-[70vh] bg-white border border-slate-200 rounded-2xl shadow-[0_10px_30px_rgba(0,0,0,0.12)] overflow-hidden"
            @mouseenter="openHistoryPopover"
            @mouseleave="closeHistoryPopoverWithDelay"
          >
            <div class="px-4 py-3 border-b border-slate-100 text-base font-semibold text-slate-800">历史对话</div>
            <div class="h-[calc(70vh-56px)] min-h-[260px]">
              <ConversationSidebar
                active-tab="conversation"
                :grouped-conversations="groupedConversations"
                :current-conversation-id="currentConversationId"
                :is-loading="isLoadingConversations"
                :show-new-conversation-button="false"
                :hide-management="!!parentComponentName"
                @select-conversation="(id: string) => { selectConversation(id); showConversationListPanel = false; showHistoryPopover = false }"
                @new-conversation="handleNewConversation"
              />
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 下方内容区（82px 头部 + 48px 工具栏 = 130px） -->
    <div v-if="!noMatchedDatasource" class="flex overflow-hidden" :class="{ 'select-none': isResizingSidebar }" style="height: calc(100vh - 130px)">
      <!-- 左侧：公用侧边栏 -->
      <!-- 折叠态：窄条 -->
      <div v-if="!isSidebarVisible" class="flex flex-col items-center gap-1 pt-3 pb-3 bg-white border-r border-slate-200 shrink-0 w-12">
        <div
          v-for="tab in sidebarTabs"
          :key="tab.key"
          :class="[
            'writing-vertical text-xs py-3 px-1 rounded-md cursor-pointer transition-all duration-200 select-none',
            activeSidebarTab === tab.key ? 'text-blue-500 font-medium bg-blue-50' : 'text-gray-400 hover:text-gray-600 hover:bg-gray-50'
          ]"
          @click="activeSidebarTab = tab.key; isSidebarVisible = true"
        >{{ tab.label }}</div>
      </div>
      <!-- 展开态 -->
      <div v-show="isSidebarVisible" class="relative flex flex-col bg-slate-50 border-r border-slate-200 shrink-0" :style="{ width: `${sidebarWidth}px` }">
        <!-- 侧边栏内容 -->
        <div ref="sidebarBodyRef" class="flex-1 overflow-hidden flex flex-col bg-white min-h-0 pr-5">
          <!-- 收起侧边栏 -->
          <button
            class="absolute right-2 z-30 w-5 h-5 flex items-center justify-center border-none bg-transparent text-gray-400 cursor-pointer transition-all duration-200 shrink-0 hover:text-gray-600"
            :style="{ top: `${sidebarCollapseTop}px` }"
            data-collapse-btn="true"
            title="收起侧边栏"
            @click="isSidebarVisible = false"
          >
            <i class="fa-solid fa-chevron-left text-[10px]"></i>
          </button>

          <!-- 看板（懒挂载，避免首屏无关请求） -->
          <div v-if="dashboardMounted" v-show="activeSidebarTab === 'dashboard'" class="sidebar-unified-shell h-full">
            <DashboardPanel
              :selected-dashboard-id="activeDashboardId"
              :dashboards="dashboards"
              :loading="loadingDefaultDashboard"
              @open-dashboard="handleOpenDashboard"
              @dashboards-change="handleDashboardsChange"
            />
          </div>

          <!-- 智能问数：指标 / 会话 -->
          <template v-if="viewMode === 'conversation'">
            <div v-show="activeSidebarTab === 'metrics'" class="sidebar-unified-shell h-full">
              <ConversationSidebar
                active-tab="metrics"
                :datasource-id="selectedDatasourceId"
                :semantic-configs="semanticConfigs"
                :loading-configs="loadingSemanticConfigs"
                :bootstrap-loading="conversationSidebarBootstrapLoading"
                :selected-dataset-id="selectedSemanticConfigId"
                :tables="currentDatasource?.ds_tables || []"
                :hide-management="!!parentComponentName"
                @select-dataset="(id: string | null) => selectedSemanticConfigId = id"
                @refresh-semantic-configs="loadSemanticConfigs"
              />
            </div>
            <ConversationSidebar
              v-show="activeSidebarTab === 'conversation'"
              active-tab="conversation"
              :grouped-conversations="groupedConversations"
              :current-conversation-id="currentConversationId"
              :is-loading="isLoadingConversations"
              :hide-management="!!parentComponentName"
              @select-conversation="selectConversation"
              @new-conversation="handleNewConversation"
            />
          </template>

          <!-- 辅助查询：数据表 -->
          <div v-show="viewMode === 'builder' && activeSidebarTab === 'tables'" class="sidebar-unified-shell h-full">
            <BuilderSidebar
              :datasource="currentDatasource"
              :semantic-config="currentSemanticConfig"
              :semantic-configs="semanticConfigs"
              :selected-dataset-id="selectedSemanticConfigId"
              :loading="loadingSemanticConfigs"
              @drag-start="handleBuilderDragStart"
              @drag-end="handleBuilderDragEnd"
              @dataset-change="(id: string) => selectedSemanticConfigId = id"
              @refresh-semantic-configs="loadSemanticConfigs"
            />
          </div>

          <!-- SQL查询：数据表 -->
          <div v-show="viewMode === 'editor' && activeSidebarTab === 'tables'" class="sidebar-unified-shell h-full">
            <EditorSidebar
              :datasource="currentDatasource"
              :loading="loadingDatasources"
              @insert-table="handleInsertTable"
              @insert-column="handleInsertColumn"
            />
          </div>
        </div>

        <!-- 底部：统一上下文选择 / 数据源组管理 -->
        <div class="px-3 py-2.5 border-t border-slate-200 bg-slate-50 shrink-0">
          <div class="flex items-center gap-2 min-w-0">
            <button
              v-if="showSidebarContextBar"
              class="flex min-w-0 flex-1 items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 h-9 text-left shadow-sm transition-all hover:bg-slate-50"
              :title="currentContextSummary"
              @click="openContextSelector"
            >
              <span class="min-w-0 flex-1 truncate text-[13px] font-medium text-slate-800">{{ currentContextSummary }}</span>
              <i class="fa-solid fa-chevron-down text-[10px] text-slate-400 shrink-0"></i>
            </button>
            <button
              v-hasPermi="['chatdata:datasource:manage']"
              class="shrink-0 flex h-9 w-9 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-400 shadow-sm hover:bg-slate-100 hover:text-slate-600 transition-all"
              :class="showSidebarContextBar ? '' : 'ml-auto'"
              title="数据源组管理"
              @click="showDatasourceModal = true"
            >
              <i class="fa-solid fa-cog text-xs"></i>
            </button>
          </div>
        </div>
      </div>

      <!-- 侧边栏拖动分隔条 -->
      <div
        v-show="isSidebarVisible"
        class="w-2 cursor-col-resize flex items-center justify-center transition-colors duration-150 shrink-0 hover:bg-blue-50 group relative"
        :class="isResizingSidebar ? 'bg-blue-50' : ''"
        @mousedown="startSidebarResize"
      >
        <div
          class="h-8 w-[3px] rounded-sm bg-black/[0.15] transition-all duration-150 group-hover:h-12 group-hover:bg-[#1677FF]"
          :class="{ 'h-12 !bg-[#1677FF]': isResizingSidebar }"
        ></div>
      </div>

      <!-- 右侧：主内容区 -->
      <div class="flex-1 flex flex-col min-w-0 overflow-hidden bg-white">
          <!-- 看板编辑器（优先显示） -->
          <DashboardEditor
            v-if="viewMode === 'dashboard' && activeDashboardId"
            :key="activeDashboardId"
            :dashboard-id="activeDashboardId"
            :datasource-id="selectedDatasourceId"
            :datasources="datasources"
            @close="handleCloseDashboard"
          />

          <template v-else>
            <!-- 问数模式（v-show 保持存活，切换 tab 不销毁） -->
            <div v-show="viewMode === 'conversation'" class="flex-1 min-h-0 relative">
              <div class="h-full flex min-h-0">
                <div class="flex-1 min-w-0 min-h-0">
                  <SqlConversation
                    :key="conversationKey"
                    :datasources="datasources"
                    :datasource="currentDatasource"
                    :conversation-id="currentConversationId"
                    :conversation-title="currentConversationTitle"
                  :selected-datasource-id="selectedDatasourceId"
                  :semantic-config-id="selectedSemanticConfigId"
                  :show-analysis-panel="rightConversationPanelMode === 'analysis'"
                  :ensure-conversation-id="ensureConversationId"
                  :execution-targets="executionTargets"
                  :execution-target-multiple="executionTargetMultiple"
                  :selected-member-datasource-ids="selectedMemberDatasourceIds"
                  hide-header
                  hide-datasource-selector
                  hide-execution-target-selector
                  hide-sidebar
                  class="h-full"
                  @first-message-complete="handleFirstMessageComplete"
                  @datasource-change="handleDatasourceChange"
                  @update:selected-member-datasource-ids="selectedMemberDatasourceIds = $event"
                  @analysis-panel-change="handleAnalysisPanelChange"
                  @switch-to-conversation="switchToConversationPanel"
                  @switch-to-analysis="switchToAnalysisPanel"
                  />
                </div>
              </div>

            </div>

            <!-- 辅助查询模式 -->
            <div v-if="builderMounted" v-show="viewMode === 'builder'" class="flex-1 min-h-0">
              <QueryBuilder
                ref="queryBuilderRef"
                :datasource-id="selectedDatasourceId"
                :datasource="currentDatasource"
                :datasources="datasources"
                :semantic-config-id="selectedSemanticConfigId"
                :semantic-config="currentSemanticConfig"
                :initial-builder-config="options?.initialBuilderConfig"
                :execution-targets="executionTargets"
                :execution-target-multiple="executionTargetMultiple"
                :selected-member-datasource-ids="selectedMemberDatasourceIds"
                :on-sql-change="options?.onSqlChange"
                :hide-chart-panel="!!options?.hideBuilderChartPanel"
                hide-execution-target-selector
                hide-sidebar
                class="h-full"
                @datasource-change="handleDatasourceChange"
                @update:selected-member-datasource-ids="selectedMemberDatasourceIds = $event"
              />
            </div>

            <!-- 自由查询模式 -->
            <div v-if="editorMounted" v-show="viewMode === 'editor'" class="flex-1 min-h-0">
              <SqlEditor
                ref="sqlEditorRef"
                :datasource-id="selectedDatasourceId"
                :datasource="currentDatasource"
                :table-schema="tableSchema"
                :initial-sql="options?.initialSql"
                :execution-targets="executionTargets"
                :execution-target-multiple="executionTargetMultiple"
                :selected-member-datasource-ids="selectedMemberDatasourceIds"
                :on-sql-change="options?.onSqlChange"
                hide-execution-target-selector
                hide-sidebar
                class="h-full"
                @update:selected-member-datasource-ids="selectedMemberDatasourceIds = $event"
              />
            </div>

            <!-- 数据看板模式（未选中具体看板时） -->
            <div v-show="viewMode === 'dashboard'" class="flex-1 min-h-0 flex items-center justify-center text-sm text-slate-400">
              请选择左侧数据看板
            </div>
          </template>
      </div>
    </div>

    <!-- 语义配置页面（整页） -->
    <div v-if="isSemanticConfigPanelOpen" class="absolute inset-0 z-[2050] bg-[#F5F7FA]">
      <SemanticConfigPanel
        :datasource-id="selectedDatasourceId"
        :datasource-name="currentDatasource?.ds_name || ''"
        :tables="currentDatasource?.ds_tables || []"
        @close="handleCloseSemanticConfig"
      />
    </div>

    <el-dialog v-model="showContextSelectorDialog" width="520px" title="切换数据源组与执行对象">
      <div class="space-y-4">
        <div class="flex items-end gap-3">
          <div class="min-w-0 flex-1">
            <div class="mb-2 text-[13px] font-medium text-slate-700">数据源组</div>
            <select
              v-model="draftDatasourceId"
              class="w-full h-10 rounded-lg border border-slate-200 bg-white px-3 text-[13px] text-slate-800 outline-none"
            >
              <option v-if="datasources.length === 0" value="">暂无数据源</option>
              <option v-for="ds in datasources" :key="ds.id" :value="ds.id">
                {{ ds.ds_name }}
              </option>
            </select>
          </div>

          <div class="shrink-0">
            <div class="mb-2 text-[13px] font-medium text-slate-700">选择模式</div>
            <div class="inline-flex rounded-lg border border-slate-200 bg-slate-50 p-1">
              <button
                :class="[
                  'h-8 px-3 rounded-md text-[12px] transition-all',
                  !draftExecutionTargetMultiple ? 'bg-white text-slate-800 shadow-sm' : 'text-slate-500 hover:text-slate-700'
                ]"
                @click="draftExecutionTargetMultiple = false"
              >
                单选
              </button>
              <button
                :class="[
                  'h-8 px-3 rounded-md text-[12px] transition-all',
                  draftExecutionTargetMultiple ? 'bg-white text-slate-800 shadow-sm' : 'text-slate-500 hover:text-slate-700'
                ]"
                @click="draftExecutionTargetMultiple = true"
              >
                多选
              </button>
            </div>
          </div>
        </div>

        <div>
          <div class="mb-2 text-[13px] font-medium text-slate-700">执行对象</div>
          <ExecutionTargetPicker
            v-if="draftExecutionTargets.length > 0"
            v-model="draftSelectedMemberDatasourceIds"
            :targets="draftExecutionTargets"
            :multiple="draftExecutionTargetMultiple"
            inline
          />
          <div
            v-else
            class="flex h-10 items-center rounded-lg border border-slate-200 bg-slate-50 px-3 text-[13px] text-slate-800"
          >
            {{ draftExecutionTargets[0]?.label || '暂无执行对象' }}
          </div>
        </div>
      </div>

      <template #footer>
        <div class="flex items-center justify-end gap-2">
            <button
              class="h-9 px-4 rounded-lg border border-slate-200 bg-white text-[13px] text-slate-600 transition-all hover:bg-slate-50"
              @click="showContextSelectorDialog = false"
            >
              取消
            </button>
            <button
              class="h-9 px-4 rounded-lg border border-[#1677FF] bg-[#1677FF] text-[13px] text-white transition-all hover:bg-[#3d8bfd]"
              @click="handleConfirmContextSelector"
            >
              确定
            </button>
        </div>
      </template>
    </el-dialog>

    <!-- 数据源组管理弹窗 -->
    <el-dialog v-model="showDatasourceModal" width="600px" :show-close="false">
      <template #header>
        <div class="flex items-center justify-between">
          <h3 class="text-sm font-medium text-slate-700 m-0">
            <i class="fa-solid fa-database mr-2 text-blue-500"></i>
            数据源组管理
          </h3>
          <div class="flex items-center gap-2">
            <el-button type="primary" size="small" round @click="openDatasourceGroupModal">
              <i class="fa-solid fa-plus" style="margin-right: 4px; font-size: 10px;"></i>
              新建
            </el-button>
            <el-button
              :icon="Close"
              circle
              size="small"
              @click="showDatasourceModal = false"
            />
          </div>
        </div>
      </template>
      <div class="overflow-y-auto overflow-x-hidden" style="height: 500px">
        <DataSourceManager
          ref="datasourceManagerRef"
          @datasources-change="handleDatasourcesChange"
          @open-semantic-config="handleOpenSemanticConfig"
        />
      </div>
    </el-dialog>
  </div>
</template>

<style>
/* 竖排文字 */
.writing-vertical {
  writing-mode: vertical-rl;
  text-orientation: mixed;
}
/* 拖拽时禁止文本选择 - 全局样式无法用 Tailwind 替代 */
body.is-resizing {
  cursor: col-resize !important;
  user-select: none !important;
}
/* 按钮默认样式重置（替代 Tailwind preflight）
   :where() 让优先级为 0，Tailwind 工具类可以正常覆盖 */
:where(.chatdata-root) button {
  background: transparent;
  border: none;
  padding: 0;
  font: inherit;
  color: inherit;
  cursor: pointer;
}

.mode-trapezoid {
  display: flex;
  align-items: flex-end;
  gap: 0;
  width: 420px;
  max-width: 100%;
  height: 36px;
  padding: 0;
  background: transparent;
  border: none;
  position: relative;
  isolation: isolate;
}

.mode-trapezoid-item {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  height: 32px;
  padding: 0 18px;
  font-size: 13px;
  font-weight: 600;
  color: #334155;
  transition: color 160ms ease;
  margin-right: -6px;
  z-index: 1;
}

.mode-trapezoid-item::before {
  content: '';
  position: absolute;
  inset: 0;
  background: #e8f4ff;
  border: 1px solid #b6bfcc;
  border-bottom: none;
  border-radius: 0;
  z-index: -1;
}

.mode-trapezoid-item.is-first::before {
  border-top-left-radius: 6px;
}

.mode-trapezoid-item.is-last::before {
  border-top-right-radius: 6px;
}

.mode-trapezoid-item::after {
  content: none;
}

.mode-trapezoid-item:hover {
  color: #1f2937;
  z-index: 2;
}

.mode-trapezoid-item:hover::before {
  background: #edf7ff;
}

.mode-trapezoid-item.is-active {
  color: #1f3c56;
  font-weight: 600;
  height: 38px;
  padding-bottom: 1px;
  z-index: 4;
  margin-bottom: -2px;
}

.mode-trapezoid-item.is-active::before {
  background: #ffffff;
  border-color: #b6bfcc;
  border-bottom: none;
  border-radius: 7px 7px 0 0;
}

.sidebar-unified-shell {
  height: 100%;
  padding: 6px 16px 8px;
  background: #fff;
}

</style>
