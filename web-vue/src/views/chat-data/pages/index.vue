<script setup lang="ts">
/**
 * ChatData 智能问数主页面
 * 参考 React 版本布局，支持数据智能体、可视化查询、SQL查询、数据看板、指标集配置
 */
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
// 会话 markdown 渲染样式（.conversation-markdown）：主对话与子智能体 trail 共用，
// 全局注入但随本模块走，便于整个 chat-data 目录迁移到其它主项目。
import '../styles/markdown.css'
import useUserStore from '@/store/modules/user'
import { getDatasources, createConversation, generateConversationTitle, getConversationContext, getSemanticConfigs, getMySemanticConfigs, createSemanticConfig, getDatasourceSchema } from '../api'
import { listDashboards, type Dashboard } from '../api/dashboard'
import type { BuilderDragFieldData, DataSource, ViewMode, ChatDataOptions, SemanticConfigItem, ExecutionTargetOption, TableInfo, SemanticConfigSelectionInfo } from '../types'
import AnalysisConversation from '../components/AnalysisConversation.vue'
import SqlEditor from '../components/SqlEditor.vue'
import QueryBuilder from '../components/QueryBuilder/index.vue'
import type { BuilderConfig } from '../components/QuickQueries.vue'
import ExecutionTargetPicker from '../components/ExecutionTargetPicker.vue'
import DashboardPanel from '../components/sidebars/DashboardPanel.vue'
import ConversationSidebar from '../components/sidebars/ConversationSidebar.vue'
import BuilderSidebar from '../components/sidebars/BuilderSidebar.vue'
import EditorSidebar from '../components/sidebars/EditorSidebar.vue'
import DashboardEditor from '../components/Dashboard/DashboardEditor.vue'
import SemanticConfigPanel from '../components/SemanticConfigPanel.vue'
import ChatDataAdminManager from '../components/ChatDataAdminManager.vue'
import IconModeConversation from '../components/icons/IconModeConversation.vue'
import IconModeBuilder from '../components/icons/IconModeBuilder.vue'
import IconModeEditor from '../components/icons/IconModeEditor.vue'
import {
  formatSemanticConfigOptionLabel,
  readLocalStorage,
  rememberSidebarTab,
  resolveSidebarTab,
  writeLocalStorage,
} from '../utils'
import { buildExecutionTargets, isSameStringArray } from '../utils/executionTargets'
import type { ConversationViewMode } from '../utils/conversationHistory'
import { useConversationHistoryBrowser } from '../composables/useConversationHistoryBrowser'

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
  metricConfig: '语义模型配置',
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
    metricConfig: labels.metricConfig?.trim() || DEFAULT_MODE_LABELS.metricConfig,
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
const allDatasources = ref<DataSource[]>([])
const datasources = ref<DataSource[]>([])
const adminDatasourceCache = ref<Record<string, DataSource>>({})
const selectedDatasourceId = ref<string>('')
const loadingDatasources = ref(true)
const noMatchedDatasource = ref(false)
// storage key 统一 chatdata: 前缀;按用户隔离,避免共享浏览器多账号互相可见
const semanticConfigStorageKey = () => `chatdata:conversation-semantic-config:${currentUser.value.id || 'anonymous'}`
const conversationModelStorageKey = () => `chatdata:conversation-model:${currentUser.value.id || 'anonymous'}`

const noMatchedDatasourceTitle = computed(() => {
  if (parentComponentName.value) {
    return `当前模块（${parentComponentName.value}）无可用数据源`
  }
  return '当前配置未匹配到数据源'
})
const noMatchedDatasourceDesc = computed(() => {
  if (parentComponentName.value) {
    return '请确认已配置模块映射，且当前用户有可访问的机构权限'
  }
  return '请在数据源管理中添加可用数据源'
})

// 后台管理弹窗
const showAdminModal = ref(false)
const canManageAdmin = computed(() => {
  const permissions = userStore.permissions || []
  return permissions.includes('*:*:*') || permissions.includes('agents:chatData:admin')
})
const canManageDatasource = canManageAdmin

const openDatasourceManager = () => {
  if (!canManageAdmin.value) return
  showAdminModal.value = true
}

const normalizeTableInfoList = (tables: unknown): TableInfo[] => {
  if (!Array.isArray(tables)) return []
  return tables.map((item) => {
    const table = item as Partial<TableInfo>
    const columns = Array.isArray(table.columns) ? table.columns : []
    return {
      table_name: String(table.table_name || ''),
      description: String(table.description || ''),
      business_desc: table.business_desc,
      groups: table.groups,
      columns: columns.map((column) => ({
        ...column,
        name: String(column.name || ''),
        type: String(column.type || ''),
        description: String(column.description || ''),
      })),
    }
  }).filter(table => table.table_name)
}

const cacheDatasourceSchema = (newDatasources: DataSource[]) => {
  const nextSchemaCache = { ...datasourceSchemaCache.value }
  let schemaCacheChanged = false
  newDatasources.forEach(ds => {
    if (!ds.ds_tables) return
    nextSchemaCache[ds.id] = normalizeTableInfoList(ds.ds_tables)
    schemaCacheChanged = true
  })
  if (schemaCacheChanged) {
    datasourceSchemaCache.value = nextSchemaCache
  }
}

// 数据源管理回调：后台配置更新后重新加载前台授权数据源列表
const handleAdminDatasourcesUpdated = () => {
  void loadDatasources()
}

// 数据源管理回调：更新本地数据源列表
const handleDatasourcesChange = (newDatasources: DataSource[]) => {
  cacheDatasourceSchema(newDatasources)
  allDatasources.value = newDatasources
  datasources.value = applyDatasourceFilters(newDatasources)
  // 如果当前选中的数据源不在新列表中，重新选择
  if (datasources.value.length > 0 && !datasources.value.find(ds => ds.id === selectedDatasourceId.value)) {
    selectedDatasourceId.value = resolveDefaultDatasourceId(datasources.value)
  } else if (datasources.value.length === 0) {
    selectedDatasourceId.value = ''
  }
  // 更新未匹配状态
  noMatchedDatasource.value = shouldShowNoMatchedDatasource(datasources.value)
}

const applyDatasourceFilters = (allDatasources: DataSource[]): DataSource[] => {
  const parentName = parentComponentName.value
  if (!parentName) return []
  return allDatasources.filter(ds => ds.mapped_modules?.includes(parentName))
}

const shouldShowNoMatchedDatasource = (filtered: DataSource[]): boolean => {
  return filtered.length === 0
}

const resolveDefaultDatasourceId = (items: DataSource[]) => {
  if (items.length === 0) return ''
  const defaultDs = items.find(ds => ds.is_default)
  return defaultDs?.id || items[0]?.id || ''
}

// 模式列表
const allModes = computed(() => ([
  { key: 'conversation' as ViewMode, label: modeLabels.value.conversation },
  { key: 'builder' as ViewMode, label: modeLabels.value.builder },
  { key: 'editor' as ViewMode, label: modeLabels.value.editor },
  { key: 'dashboard' as ViewMode, label: modeLabels.value.dashboard },
  { key: 'metricConfig' as ViewMode, label: modeLabels.value.metricConfig },
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
    if (value === 'conversation' || value === 'builder' || value === 'editor' || value === 'dashboard' || value === 'metricConfig') {
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
const dashboardEditorMounted = ref(viewMode.value === 'dashboard')
const metricConfigMounted = ref(viewMode.value === 'metricConfig')

// 选中的指标集 ID
const selectedSemanticConfigId = ref<string | null>(null)
const metricSelectedSemanticConfigId = ref<string | null>(null)
const conversationSelectedSemanticConfigId = ref<string | null>(null)
const semanticConfigs = ref<SemanticConfigItem[]>([])
const loadingSemanticConfigs = ref(false)
const metricSemanticConfigs = ref<SemanticConfigItem[]>([])
const loadingMetricSemanticConfigs = ref(false)
const creatingMetricConfig = ref(false)
const metricConfigPanelKey = ref(0)
const datasourceSchemaCache = ref<Record<string, TableInfo[]>>({})
const loadingDatasourceSchema = ref(false)

// 侧边栏可见性
const isSidebarVisible = ref(true)
const sidebarBodyRef = ref<HTMLElement | null>(null)
const sidebarCollapseTop = ref(15)

// 侧边栏宽度拖拽调整（默认 320：会话项需容纳标题 + 时间 + 运行状态 + 收藏）
const sidebarWidth = ref(320)
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

// 侧边栏 Tab（公用）：tab 选择记忆见 utils/sidebarTab
const sidebarTabs = computed(() => {
  const tabs: Array<{ key: string; label: string; icon: string }> = []
  if (viewMode.value === 'conversation') {
    tabs.push({ key: 'conversation', label: '对话历史', icon: 'fa-comments' })
    tabs.push({ key: 'metrics', label: '语义模型', icon: 'fa-bullseye' })
  } else if (viewMode.value === 'dashboard') {
    tabs.push({ key: 'dashboard', label: '数据看板', icon: 'fa-chart-pie' })
  } else if (viewMode.value === 'builder') {
    tabs.push({ key: 'tables', label: '语义模型', icon: 'fa-database' })
  } else if (viewMode.value === 'editor') {
    tabs.push({ key: 'tables', label: '数据表', icon: 'fa-database' })
  }
  return tabs
})

const availableSidebarTabKeys = computed(() => sidebarTabs.value.map((tab) => tab.key))

const activeSidebarTab = ref<string>(
  resolveSidebarTab(viewMode.value, availableSidebarTabKeys.value),
)

// 单点写入：点击切换、模式切换回落都经此记忆，避免遗漏写入点；
// 无可用 tab 的模式（如语义模型配置，侧栏面板不消费 tab）不落记忆
watch(
  activeSidebarTab,
  (tab) => {
    if (availableSidebarTabKeys.value.length === 0) return
    rememberSidebarTab(viewMode.value, tab)
  },
  { flush: 'sync' },
)

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
const dashboards = ref<Dashboard[]>([])

async function ensureDefaultDashboard() {
  if (viewMode.value !== 'dashboard') return
  if (loadingDefaultDashboard.value) return
  loadingDefaultDashboard.value = true
  try {
    const [mineDashboards, publicDashboards] = await Promise.all([
      listDashboards('mine'),
      listDashboards('public'),
    ])
    const mergedDashboards = [
      ...mineDashboards,
      ...publicDashboards.filter(item => !mineDashboards.some(mine => mine.id === item.id)),
    ]
    dashboards.value = mergedDashboards
    const dashboardList = mergedDashboards
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
    dashboardEditorMounted.value = true
  } else if (viewMode.value === 'metricConfig') {
    metricConfigMounted.value = true
  }
  // 切模式时恢复该模式记住的 tab（无记忆或记忆失效则回落该模式默认 tab），
  // 各模式 tab 相互独立，避免在 A 模式的选择被 B 模式覆盖
  activeSidebarTab.value = resolveSidebarTab(viewMode.value, availableSidebarTabKeys.value)
  if (viewMode.value === 'dashboard') {
    ensureDefaultDashboard()
  }
}, { immediate: true })

// 打开看板编辑器
const handleOpenDashboard = (dashboardId: string) => {
  activeDashboardId.value = dashboardId
  dashboardEditorMounted.value = true
}

const handleDashboardCreatedFromConversation = (dashboard: Dashboard) => {
  if (!dashboard?.id) return
  dashboards.value = [
    dashboard,
    ...dashboards.value.filter(item => item.id !== dashboard.id),
  ]
  activeDashboardId.value = dashboard.id
  dashboardMounted.value = true
  dashboardEditorMounted.value = true
  viewMode.value = 'dashboard'
}

const handleDashboardsChange = (nextDashboards: Dashboard[]) => {
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
const semanticConfigDatasourceId = ref('')

const activeMetricConfigDatasourceId = computed(() => {
  return viewMode.value === 'metricConfig' ? selectedDatasourceId.value : semanticConfigDatasourceId.value
})

const metricConfigDatasource = computed(() => {
  const datasourceId = activeMetricConfigDatasourceId.value
  const datasource = adminDatasourceCache.value[datasourceId]
    || allDatasources.value.find(ds => ds.id === datasourceId)
    || datasources.value.find(ds => ds.id === datasourceId)
    || null
  if (!datasource) return null
  const cachedTables = datasourceSchemaCache.value[datasource.id]
  if (!cachedTables) return datasource
  return {
    ...datasource,
    ds_tables: cachedTables,
  }
})

const handleCloseMetricConfigTab = () => {
  viewMode.value = 'conversation'
}

// 打开语义配置面板
const handleOpenSemanticConfig = (datasourceId?: string, datasource?: DataSource) => {
  if (!canManageDatasource.value) return
  semanticConfigDatasourceId.value = datasourceId || selectedDatasourceId.value
  if (datasource?.id) {
    adminDatasourceCache.value = {
      ...adminDatasourceCache.value,
      [datasource.id]: datasource,
    }
    cacheDatasourceSchema([datasource])
  }
  showAdminModal.value = false
  isSemanticConfigPanelOpen.value = true
  if (semanticConfigDatasourceId.value) {
    void loadDatasourceSchema(semanticConfigDatasourceId.value)
  }
}

// 关闭语义配置面板
const handleCloseSemanticConfig = () => {
  isSemanticConfigPanelOpen.value = false
  semanticConfigDatasourceId.value = ''
}

// 会话
const currentConversationId = ref<string | null>(null)
const currentConversationTitle = ref<string>('新会话')
const currentConversationDatasourceId = ref<string | null>(null)
const conversationPageSize = 20
const conversationKey = ref(0)
const selectedConversationModelId = ref('')
const conversationModelStorageUserKey = computed(() => currentUser.value.id || currentUser.value.name || 'default')

// SqlEditor 组件引用
const sqlEditorRef = ref<InstanceType<typeof SqlEditor> | null>(null)
const conversationRef = ref<InstanceType<typeof AnalysisConversation> | null>(null)

// 控制台面板可见性：由 AnalysisConversation 广播（面板内的 ✕ 也会触发），
// 右上角开关按钮的图标与提示都读它，不自己另存状态。
const conversationConsoleVisible = ref(false)

const toggleConversationConsole = () => {
  conversationRef.value?.toggleConsolePanel()
}

// 加载数据源
const loadDatasources = async () => {
  loadingDatasources.value = true
  try {
    const res = await getDatasources({ include_tables: false })
    if (res.status === 'success') {
      const loadedDatasources = res.data.datasources || []
      allDatasources.value = loadedDatasources
      datasources.value = applyDatasourceFilters(allDatasources.value)
      noMatchedDatasource.value = shouldShowNoMatchedDatasource(datasources.value)
      if (datasources.value.length > 0 && !datasources.value.find(ds => ds.id === selectedDatasourceId.value)) {
        selectedDatasourceId.value = resolveDefaultDatasourceId(datasources.value)
      } else if (datasources.value.length === 0) {
        selectedDatasourceId.value = ''
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
        [normalizedDatasourceId]: normalizeTableInfoList(res.data?.tables),
      }
    }
  } catch (err) {
    console.error('加载数据源表结构失败:', err)
  } finally {
    loadingDatasourceSchema.value = false
  }
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
      currentConversationTitle.value = '新会话'
      currentConversationDatasourceId.value = selectedDatasourceId.value || null
      upsertHistoryConversation({
        id: res.data.id,
        title: '新会话',
        agent_id: 'chat_data',
        datasource_id: selectedDatasourceId.value || '',
        create_time: res.data.create_time || new Date().toISOString(),
        updated_at: res.data.create_time || new Date().toISOString(),
        is_favorited: false,
        run_status: null,
      })
      return res.data.id
    }
  } catch (err) {
    console.error('创建会话失败:', err)
  }
  return null
}

// 会话列表视图模式：grouped 按数据源分组（默认），flat 平铺
const CONVERSATION_VIEW_MODE_KEY = 'chatdata:view-mode'

const conversationViewMode = ref<ConversationViewMode>(
  readLocalStorage(CONVERSATION_VIEW_MODE_KEY) === 'flat' ? 'flat' : 'grouped',
)

const {
  conversations,
  groupedConversations,
  isLoadingConversations,
  isLoadingMoreConversations,
  hasMoreConversations,
  ensureConversationsLoaded,
  loadMoreConversations,
  loadMoreConversationGroup,
  updateConversationTitle: updateHistoryConversationTitle,
  updateConversationRunStatus: updateHistoryConversationRunStatus,
  upsertConversation: upsertHistoryConversation,
  refreshAfterFavorite,
  invalidateInactiveView,
} = useConversationHistoryBrowser({
  agentId: 'chat_data',
  pageSize: conversationPageSize,
  viewMode: conversationViewMode,
  datasources: allDatasources,
})

const toggleConversationViewMode = () => {
  conversationViewMode.value = conversationViewMode.value === 'grouped' ? 'flat' : 'grouped'
  writeLocalStorage(CONVERSATION_VIEW_MODE_KEY, conversationViewMode.value)
  void ensureConversationsLoaded()
}

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
  currentConversationDatasourceId.value = null
  conversationKey.value++ // 强制重新渲染以清空消息
}

const loadConversationDatasourceId = async (conversationId: string): Promise<string | null | undefined> => {
  try {
    const response = await getConversationContext(conversationId)
    if (response.status !== 'success') {
      throw new Error(response.msg || '读取历史会话上下文失败')
    }
    return String(response.data?.datasource_id || '').trim() || null
  } catch (error) {
    console.error('读取历史会话上下文失败:', error)
    return undefined
  }
}

// 选择会话
const selectConversation = async (id: string): Promise<boolean> => {
  const conv = conversations.value.find(c => c.id === id)
  if (!conv) return false
  const conversationDatasourceId = await loadConversationDatasourceId(id)
  if (conversationDatasourceId === undefined) {
    ElMessage.error('读取历史会话上下文失败')
    return false
  }
  if (conversationDatasourceId) {
    if (!datasources.value.some(datasource => datasource.id === conversationDatasourceId)) {
      ElMessage.warning('该历史会话关联的数据源组已不可用，仅可查看历史内容')
    } else {
      selectedDatasourceId.value = conversationDatasourceId
    }
  }
  currentConversationId.value = id
  currentConversationTitle.value = conv?.title || '会话'
  currentConversationDatasourceId.value = conversationDatasourceId || null
  conversationKey.value++ // 强制重新渲染以加载历史
  return true
}

const ensureConversationDatasource = async (): Promise<boolean> => {
  const boundDatasourceId = currentConversationDatasourceId.value
  if (boundDatasourceId) {
    if (!datasources.value.some(datasource => datasource.id === boundDatasourceId)) {
      ElMessage.warning('该历史会话关联的数据源组已不可用，无法继续提问')
      return false
    }
    selectedDatasourceId.value = boundDatasourceId
    return true
  }
  if (!currentConversationId.value) return true
  const currentDatasourceName = currentDatasource.value?.group_name || '当前数据源组'
  try {
    await ElMessageBox.confirm(
      `该历史会话未记录数据源组。继续提问将绑定到当前数据源组“${currentDatasourceName}”。`,
      '确认数据源组',
      { confirmButtonText: '确认继续', cancelButtonText: '取消', type: 'warning' },
    )
    return true
  } catch {
    return false
  }
}

const handleHistoryConversationSelect = async (id: string) => {
  await selectConversation(id)
}

const handleUpdateConversationTitle = (conversationId: string, title: string) => {
  updateHistoryConversationTitle(conversationId, title)
  if (currentConversationId.value === conversationId) {
    currentConversationTitle.value = title
  }
}

const handleConversationRunStatusChanged = (
  conversationId: string,
  status: 'pending' | 'active' | 'cancelling' | 'waiting_user' | null,
) => {
  updateHistoryConversationRunStatus(conversationId, status)
  if (status === null) {
    // 终态先更新当前缓存，随后以服务端列表覆盖，避免停止/完成后历史项仍显示旧的 cancelling 状态。
    void ensureConversationsLoaded(true)
  }
}

// 置顶会改变会话所属分组，成功后重载分组首屏，避免同一会话残留在旧目录。
const handleToggleConversationFavorite = async (conversationId: string, isFavorited: boolean) => {
  await refreshAfterFavorite(conversationId, isFavorited)
}

// 首条消息对应的 SSE 建立后立即生成标题并完成分组迁移，不等待助手输出结束。
const handleFirstMessageStarted = async (convId: string) => {
  const datasourcePromise = loadConversationDatasourceId(convId)
  const titlePromise = generateConversationTitle(convId)
    .catch(error => {
      console.error('生成标题失败:', error)
      return null
    })
  const [boundDatasourceId, res] = await Promise.all([datasourcePromise, titlePromise])

  if (currentConversationId.value === convId && boundDatasourceId) {
    currentConversationDatasourceId.value = boundDatasourceId
  }
  let generatedTitle = currentConversationTitle.value
  if (res?.status === 'success' && res.data?.title) {
    generatedTitle = res.data.title
    if (currentConversationId.value === convId) currentConversationTitle.value = generatedTitle
    updateHistoryConversationTitle(convId, generatedTitle)
  }

  upsertHistoryConversation({
    id: convId,
    title: generatedTitle,
    ...(boundDatasourceId ? { datasource_id: boundDatasourceId } : {}),
    updated_at: new Date().toISOString(),
  })
  // 刷新会话列表
  invalidateInactiveView()
  void ensureConversationsLoaded(true)
}

// 数据源变更
const handleDatasourceChange = (dsId: string) => {
  selectedDatasourceId.value = dsId
}

const readConversationSemanticConfigSelection = (datasourceId: string) => {
  if (typeof window === 'undefined' || !datasourceId) return null
  try {
    const raw = window.localStorage.getItem(semanticConfigStorageKey())
    if (!raw) return null
    const data = JSON.parse(raw) as Record<string, string>
    return String(data?.[datasourceId] || '').trim() || null
  } catch (err) {
    return null
  }
}

const writeConversationSemanticConfigSelection = (datasourceId: string, configId: string | null) => {
  if (typeof window === 'undefined' || !datasourceId) return
  try {
    const raw = window.localStorage.getItem(semanticConfigStorageKey())
    const data = raw ? JSON.parse(raw) as Record<string, string> : {}
    if (configId) {
      data[datasourceId] = configId
    } else {
      delete data[datasourceId]
    }
    window.localStorage.setItem(semanticConfigStorageKey(), JSON.stringify(data))
  } catch (err) {
    // 本地存储不可用时保持当前页面内状态即可
  }
}

const getConversationModelStorageUserKey = () => {
  return conversationModelStorageUserKey.value
}

const readConversationModelSelection = () => {
  if (typeof window === 'undefined') return ''
  try {
    const raw = window.localStorage.getItem(conversationModelStorageKey())
    if (!raw) return ''
    const data = JSON.parse(raw) as Record<string, string>
    return String(data?.[getConversationModelStorageUserKey()] || '').trim()
  } catch (err) {
    return ''
  }
}

const writeConversationModelSelection = (modelId: string) => {
  if (typeof window === 'undefined') return
  try {
    const raw = window.localStorage.getItem(conversationModelStorageKey())
    const data = raw ? JSON.parse(raw) as Record<string, string> : {}
    const userKey = getConversationModelStorageUserKey()
    const normalizedModelId = String(modelId || '').trim()
    if (normalizedModelId) {
      data[userKey] = normalizedModelId
    } else {
      delete data[userKey]
    }
    window.localStorage.setItem(conversationModelStorageKey(), JSON.stringify(data))
  } catch (err) {
    // 本地存储不可用时保持当前页面内状态即可
  }
}

const loadSemanticConfigs = async () => {
  if (!selectedDatasourceId.value) {
    semanticConfigs.value = []
    selectedSemanticConfigId.value = null
    conversationSelectedSemanticConfigId.value = null
    return
  }
  loadingSemanticConfigs.value = true
  try {
    const res = await getSemanticConfigs(selectedDatasourceId.value)
    if (res.status === 'success') {
      semanticConfigs.value = res.data?.configs || []
      if (selectedSemanticConfigId.value && !semanticConfigs.value.find(c => c.id === selectedSemanticConfigId.value && c.enabled)) {
        selectedSemanticConfigId.value = null
      }
      const savedConversationConfigId = readConversationSemanticConfigSelection(selectedDatasourceId.value)
      if (!conversationSelectedSemanticConfigId.value && savedConversationConfigId) {
        conversationSelectedSemanticConfigId.value = savedConversationConfigId
      }
      if (conversationSelectedSemanticConfigId.value && !semanticConfigs.value.find(c => c.id === conversationSelectedSemanticConfigId.value && c.enabled)) {
        conversationSelectedSemanticConfigId.value = null
        writeConversationSemanticConfigSelection(selectedDatasourceId.value, null)
      }
    }
  } catch (err) {
    console.error('加载指标集失败:', err)
    semanticConfigs.value = []
    selectedSemanticConfigId.value = null
    conversationSelectedSemanticConfigId.value = null
  } finally {
    loadingSemanticConfigs.value = false
  }
}

const loadMetricSemanticConfigs = async () => {
  if (!selectedDatasourceId.value) {
    metricSemanticConfigs.value = []
    metricSelectedSemanticConfigId.value = null
    return
  }
  loadingMetricSemanticConfigs.value = true
  try {
    const res = await getMySemanticConfigs(selectedDatasourceId.value)
    if (res.status === 'success') {
      metricSemanticConfigs.value = res.data?.configs || []
      if (metricSelectedSemanticConfigId.value && !metricSemanticConfigs.value.find(c => c.id === metricSelectedSemanticConfigId.value)) {
        metricSelectedSemanticConfigId.value = null
      }
    }
  } catch (err) {
    console.error('加载个人指标集失败:', err)
    metricSemanticConfigs.value = []
    metricSelectedSemanticConfigId.value = null
  } finally {
    loadingMetricSemanticConfigs.value = false
  }
}

const handleCreateMetricConfig = async () => {
  if (!selectedDatasourceId.value || creatingMetricConfig.value) return
  let datasetName = ''
  try {
    const { value } = await ElMessageBox.prompt('请输入语义模型名称', '新建语义模型', {
      confirmButtonText: '创建',
      cancelButtonText: '取消',
      inputValue: '',
      inputPlaceholder: '请输入语义模型名称',
      inputPattern: /\S+/,
      inputErrorMessage: '名称不能为空',
      customClass: 'chatdata-message-box',
      modalClass: 'chatdata-message-box-overlay',
    })
    datasetName = String(value || '').trim()
  } catch (err) {
    return
  }
  if (!datasetName) return
  creatingMetricConfig.value = true
  try {
    const res = await createSemanticConfig(selectedDatasourceId.value, {
      name: datasetName,
      enabled: true,
      metrics: [],
      dimensions: [],
      relationships: [],
    })
    if (res.status === 'success' && res.data) {
      metricSemanticConfigs.value = [...metricSemanticConfigs.value, res.data]
      semanticConfigs.value = [...semanticConfigs.value, res.data]
      metricSelectedSemanticConfigId.value = res.data.id
      metricConfigPanelKey.value += 1
      ElMessage.success('创建成功')
    } else {
      ElMessage.error(res.msg || '创建失败')
    }
  } catch (err) {
    ElMessage.error('创建失败')
  } finally {
    creatingMetricConfig.value = false
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

const semanticConfigDatasource = computed(() => {
  const datasource = adminDatasourceCache.value[semanticConfigDatasourceId.value]
    || allDatasources.value.find(ds => ds.id === semanticConfigDatasourceId.value)
    || datasources.value.find(ds => ds.id === semanticConfigDatasourceId.value)
    || null
  if (!datasource) return null
  const cachedTables = datasourceSchemaCache.value[datasource.id]
  if (!cachedTables) return datasource
  return {
    ...datasource,
    ds_tables: cachedTables,
  }
})

const resolveDatasourceType = (datasource: DataSource | null | undefined) => {
  const sourceType = String(datasource?.source_type || '').trim().toLowerCase()
  if (sourceType) return sourceType
  const memberTypes = Array.from(new Set((datasource?.member_sources || [])
    .map(member => String(member.source_type || '').trim().toLowerCase())
    .filter(Boolean)))
  return memberTypes.length === 1 ? memberTypes[0] : ''
}

const enabledSemanticConfigs = computed(() => semanticConfigs.value.filter(cfg => cfg.enabled))
const enabledMetricSemanticConfigs = computed(() => metricSemanticConfigs.value.filter(cfg => cfg.enabled))

const currentSemanticConfig = computed(() => {
  if (!selectedSemanticConfigId.value) return null
  return enabledSemanticConfigs.value.find(cfg => cfg.id === selectedSemanticConfigId.value) || null
})

const currentMetricSemanticConfig = computed(() => {
  if (!metricSelectedSemanticConfigId.value) return null
  return metricSemanticConfigs.value.find(cfg => cfg.id === metricSelectedSemanticConfigId.value) || null
})

const getSemanticConfigIndex = (config: SemanticConfigItem) => {
  return semanticConfigs.value.findIndex(item => item.id === config.id)
}

const getMetricSemanticConfigIndex = (config: SemanticConfigItem) => {
  return metricSemanticConfigs.value.findIndex(item => item.id === config.id)
}

const formatSemanticConfigLabel = (config: SemanticConfigItem) => {
  return formatSemanticConfigOptionLabel(config, getSemanticConfigIndex(config))
}

const formatMetricSemanticConfigLabel = (config: SemanticConfigItem) => {
  return formatSemanticConfigOptionLabel(config, getMetricSemanticConfigIndex(config))
}

const conversationSemanticConfigId = computed(() => {
  const selected = semanticConfigs.value.find(cfg => cfg.id === conversationSelectedSemanticConfigId.value)
  return selected?.enabled ? selected.id : null
})

const conversationSemanticConfigSelection = computed<SemanticConfigSelectionInfo | null>(() => {
  const selected = semanticConfigs.value.find(cfg => cfg.id === conversationSelectedSemanticConfigId.value && cfg.enabled)
  if (!selected) return null
  return {
    semantic_config_id: selected.id,
    semantic_config_name: selected.name || selected.id,
    confidence: '',
    reason: '用户手动选择指标集',
    matched_metrics: [],
    matched_dimensions: [],
    auto_selected: false,
    user_selected: true,
  }
})

const ensureStructuredViewSemanticConfig = () => {
  if (viewMode.value === 'metricConfig') {
    if (metricSelectedSemanticConfigId.value && metricSemanticConfigs.value.find(cfg => cfg.id === metricSelectedSemanticConfigId.value)) return
    metricSelectedSemanticConfigId.value = enabledMetricSemanticConfigs.value[0]?.id || metricSemanticConfigs.value[0]?.id || null
    return
  }
  if (viewMode.value !== 'builder') return
  if (selectedSemanticConfigId.value && enabledSemanticConfigs.value.find(cfg => cfg.id === selectedSemanticConfigId.value)) return
  selectedSemanticConfigId.value = enabledSemanticConfigs.value[0]?.id || null
}

const conversationSidebarBootstrapLoading = computed(() => {
  if (loadingDatasources.value) return true
  if (!selectedDatasourceId.value && datasources.value.length > 0) return true
  if (!!selectedDatasourceId.value && loadingSemanticConfigs.value) return true
  return false
})

const executionTargets = computed<ExecutionTargetOption[]>(() => (
  buildExecutionTargets({ datasource: currentDatasource.value, fallbackId: currentDatasource.value?.id })
))
const selectedMemberDatasourceIds = ref<string[]>([])
const conversationMemberDatasourceIds = ref<string[]>([])

const resolveSelectedExecutionTargetIds = (
  targets: ExecutionTargetOption[],
  currentIds: string[],
  templateId?: string,
  multiple = true,
  defaultAll = false
): string[] => {
  const availableIds = targets.map(item => item.member_datasource_id)
  if (availableIds.length === 0) {
    return []
  }

  const retainedIds = currentIds.filter(id => availableIds.includes(id))
  if (retainedIds.length > 0) {
    return multiple ? retainedIds : [retainedIds[0]!]
  }

  if (multiple && defaultAll) {
    return [...availableIds]
  }

  if (templateId && availableIds.includes(templateId)) {
    return [templateId]
  }

  return [availableIds[0]!]
}

const syncSelectedExecutionTargets = () => {
  const nextIds = resolveSelectedExecutionTargetIds(
    executionTargets.value,
    selectedMemberDatasourceIds.value,
    currentDatasource.value?.template_datasource_id,
    true,
    false
  )
  if (isSameStringArray(nextIds, selectedMemberDatasourceIds.value)) return
  selectedMemberDatasourceIds.value = nextIds
}

const syncConversationExecutionTargets = () => {
  const nextIds = resolveSelectedExecutionTargetIds(
    executionTargets.value,
    conversationMemberDatasourceIds.value,
    currentDatasource.value?.template_datasource_id,
    true,
    true
  )
  if (isSameStringArray(nextIds, conversationMemberDatasourceIds.value)) return
  conversationMemberDatasourceIds.value = nextIds
}

watch(
  () => [
    selectedDatasourceId.value,
    currentDatasource.value?.template_datasource_id,
    executionTargets.value.map(item => item.member_datasource_id).join(','),
  ],
  () => {
    syncSelectedExecutionTargets()
    syncConversationExecutionTargets()
  },
  { immediate: true }
)

const isConversationContextMode = computed(() => viewMode.value === 'conversation')
const activeMemberDatasourceIds = computed(() => (
  isConversationContextMode.value ? conversationMemberDatasourceIds.value : selectedMemberDatasourceIds.value
))

const selectedExecutionTargetSummary = computed(() => {
  const selectedTargets = executionTargets.value.filter(item => activeMemberDatasourceIds.value.includes(item.member_datasource_id))
  if (selectedTargets.length === 0) return '未选择执行对象'
  if (selectedTargets.length === 1) return selectedTargets[0]?.label || '已选择 1 个执行对象'
  return `${selectedTargets[0]?.label || '已选择'} 等 ${selectedTargets.length} 个`
})

const showContextSelectorDialog = ref(false)
const draftSelectedDatasourceId = ref<string>('')
const draftSelectedMemberDatasourceIds = ref<string[]>([])
const draftDefaultAllExecutionTargets = ref(false)

const draftDatasource = computed(() => {
  const datasourceId = draftSelectedDatasourceId.value || selectedDatasourceId.value
  return datasources.value.find(ds => ds.id === datasourceId) || null
})

const draftExecutionTargets = computed<ExecutionTargetOption[]>(() => (
  buildExecutionTargets({ datasource: draftDatasource.value, fallbackId: draftDatasource.value?.id })
))

const canChangeDraftDatasource = computed(() => {
  if (datasources.value.length <= 1) return false
  return !isConversationContextMode.value || !currentConversationDatasourceId.value
})

const openContextSelector = () => {
  draftSelectedDatasourceId.value = selectedDatasourceId.value || resolveDefaultDatasourceId(datasources.value)
  draftSelectedMemberDatasourceIds.value = [...activeMemberDatasourceIds.value]
  draftDefaultAllExecutionTargets.value = isConversationContextMode.value
  showContextSelectorDialog.value = true
}

watch(
  () => [
    draftSelectedDatasourceId.value,
    draftDatasource.value?.template_datasource_id,
    draftExecutionTargets.value.map(item => item.member_datasource_id).join(','),
  ],
  () => {
    const nextIds = resolveSelectedExecutionTargetIds(
      draftExecutionTargets.value,
      draftSelectedMemberDatasourceIds.value,
      draftDatasource.value?.template_datasource_id,
      true,
      draftDefaultAllExecutionTargets.value
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
  const nextIds = resolveSelectedExecutionTargetIds(
    draftExecutionTargets.value,
    draftSelectedMemberDatasourceIds.value,
    draftDatasource.value?.template_datasource_id,
    true,
    draftDefaultAllExecutionTargets.value
  )
  if (draftSelectedDatasourceId.value && draftSelectedDatasourceId.value !== selectedDatasourceId.value) {
    selectedDatasourceId.value = draftSelectedDatasourceId.value
  }
  if (isConversationContextMode.value) {
    conversationMemberDatasourceIds.value = nextIds
  } else {
    selectedMemberDatasourceIds.value = nextIds
  }
  showContextSelectorDialog.value = false
}

const currentContextSummary = computed(() => {
  const datasourceName = currentDatasource.value?.group_name || '暂无数据源'
  return `${datasourceName} / ${selectedExecutionTargetSummary.value}`
})

const showSidebarContextBar = computed(() => {
  return datasources.value.length > 0 || executionTargets.value.length > 0
})

const showSidebarFooter = computed(() => {
  if (viewMode.value === 'dashboard') return false
  return showSidebarContextBar.value || canManageDatasource.value
})

watch(selectedDatasourceId, () => {
  conversationSelectedSemanticConfigId.value = null
  metricSelectedSemanticConfigId.value = null
  metricSemanticConfigs.value = []
  loadSemanticConfigs()
  if (viewMode.value === 'metricConfig') {
    loadMetricSemanticConfigs()
  }
}, { immediate: true })

watch(viewMode, (mode) => {
  if (mode === 'metricConfig') {
    loadMetricSemanticConfigs()
  }
})

watch(
  conversationModelStorageUserKey,
  () => {
    const savedModelId = readConversationModelSelection()
    if (savedModelId) {
      selectedConversationModelId.value = savedModelId
    } else if (selectedConversationModelId.value) {
      writeConversationModelSelection(selectedConversationModelId.value)
    }
  },
  { immediate: true }
)

watch(selectedConversationModelId, (modelId) => {
  writeConversationModelSelection(modelId)
})

watch(
  [viewMode, () => semanticConfigs.value.map(cfg => `${cfg.id}:${Number(cfg.enabled)}`).join('|'), () => metricSemanticConfigs.value.map(cfg => cfg.id).join('|')],
  ensureStructuredViewSemanticConfig,
  { immediate: true }
)

watch(
  [selectedDatasourceId, viewMode, activeSidebarTab],
  ([datasourceId, mode]) => {
    if (!datasourceId) return
    // conversation 模式下「语义模型」面板与「对话」面板共存（v-show），切换 tab 不能省掉 schema 加载
    if (mode === 'builder' || mode === 'editor' || mode === 'metricConfig' || mode === 'conversation') {
      void loadDatasourceSchema(datasourceId)
    }
  },
  { immediate: true }
)

watch(
  [semanticConfigDatasourceId, isSemanticConfigPanelOpen],
  ([datasourceId, semanticPanelOpen]) => {
    if (!semanticPanelOpen || !datasourceId) return
    void loadDatasourceSchema(datasourceId)
  },
  { immediate: true }
)

watch(
  [viewMode, activeSidebarTab, isSidebarVisible, selectedDatasourceId, selectedSemanticConfigId, conversationSelectedSemanticConfigId],
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
const handleBuilderDragStart = (type: 'field', data: BuilderDragFieldData, event: DragEvent) => {
  if (queryBuilderRef.value) {
    queryBuilderRef.value.handleSidebarDragStart(type, data, event)
  }
}

const handleBuilderDragEnd = () => {
  if (queryBuilderRef.value) {
    queryBuilderRef.value.handleSidebarDragEnd()
  }
}

const handleBuilderAddDimension = (data: BuilderDragFieldData) => {
  queryBuilderRef.value?.addFieldToDimension(data)
}

const handleBuilderAddMetric = (data: BuilderDragFieldData) => {
  queryBuilderRef.value?.addFieldToAggregation(data)
}

// EditorSidebar 插入事件处理
const handleInsertTable = (tableName: string) => {
  if (sqlEditorRef.value) {
    sqlEditorRef.value.insertTableName(tableName)
  }
}

const handleInsertColumn = (tableName: string, columnName: string) => {
  if (sqlEditorRef.value) {
    sqlEditorRef.value.insertColumnName(tableName, columnName)
  }
}

const handleFillSemanticQuestion = (payload: { fieldName: string; fieldType: 'dimension' | 'metric' }) => {
  if (viewMode.value !== 'conversation') return
  conversationRef.value?.fillSemanticFieldQuestion(payload)
}

const handleConversationDatasetSelected = (configId: string | null) => {
  conversationSelectedSemanticConfigId.value = configId
  writeConversationSemanticConfigSelection(selectedDatasourceId.value, configId)
}

const handleBuilderSemanticConfigSelected = (configId: string) => {
  const normalizedId = String(configId || '').trim()
  if (!enabledSemanticConfigs.value.some(config => config.id === normalizedId)) return
  selectedSemanticConfigId.value = normalizedId
}

const handleConversationSemanticConfigSelected = (payload: SemanticConfigSelectionInfo) => {
  const selectedId = String(payload.semantic_config_id || '').trim()
  if (!selectedId || !payload.user_selected) return
  if (!enabledSemanticConfigs.value.some(config => config.id === selectedId)) return
  conversationSelectedSemanticConfigId.value = selectedId
  writeConversationSemanticConfigSelection(selectedDatasourceId.value, selectedId)
}

// storage key 统一前缀改造后的一次性迁移：清掉历史版本的孤儿键，
// 避免旧键里上一个账号的数据永久残留在浏览器
const LEGACY_STORAGE_KEYS = [
  'chat-data:conversation-semantic-config',
  'chat-data:conversation-model',
  'chatdata-conversation-view-mode',
  'chat_data_sql_ai_conversation_history',
]
const cleanupLegacyStorageKeys = () => {
  try {
    LEGACY_STORAGE_KEYS.forEach(key => localStorage.removeItem(key))
    sessionStorage.removeItem('chatdata-active-run-conversation')
  } catch {
    // storage 不可用（如宿主禁用）时清理失败无影响
  }
}

onMounted(async () => {
  cleanupLegacyStorageKeys()
  // 确保主系统用户信息已加载，后端统一从主系统认证中解析当前用户。
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
  await loadDatasources()
  void ensureConversationsLoaded()
  // 不立即创建会话，等发送第一条消息时才创建
  window.addEventListener('resize', updateSidebarCollapseTop)
  updateSidebarCollapseTop()
})

onUnmounted(() => {
  window.removeEventListener('resize', updateSidebarCollapseTop)
})
</script>

<template>
  <div
    class="chatdata-root flex flex-col w-full"
    style="height: calc(100vh - 82px); background: linear-gradient(to bottom, #F9FAFB 0%, #F3F4F6 100%)"
  >
    <!-- 根节点必须是单个真实 DOM 元素（不能在 template 直下放注释或组件，否则编译成 Fragment 根，宿主项目 transition(mode="out-in") + keep-alive 路由出口会导致页面切换卡死），注释与 el-config-provider 均须置于根 div 内 -->
    <!-- 高度 = 视口 - 主项目头部(导航栏50+标签栏32=82px)，按实际调整 -->
    <el-config-provider :locale="zhCn">
    <!-- 未匹配数据源提示 -->
    <div v-if="noMatchedDatasource" class="flex-1 flex items-center justify-center">
      <div class="text-center text-gray-400">
        <i class="fa-solid fa-database text-4xl mb-4 block"></i>
        <p class="text-sm">{{ noMatchedDatasourceTitle }}</p>
        <p class="text-xs mt-1">{{ noMatchedDatasourceDesc }}</p>
        <button
          v-if="canManageDatasource"
          class="mt-4 inline-flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-4 h-9 text-sm text-slate-700 shadow-sm transition-all hover:bg-slate-50 hover:text-slate-900"
          @click="openDatasourceManager"
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

        <div class="relative shrink-0 flex items-center gap-2">
          <button
            v-if="viewMode === 'conversation'"
            class="w-8 h-8 flex shrink-0 items-center justify-center rounded-lg border-0 bg-transparent text-slate-500 cursor-pointer transition-colors hover:bg-slate-200/70 hover:text-slate-700"
            :title="conversationConsoleVisible ? '收起工作区' : '展开工作区'"
            :aria-label="conversationConsoleVisible ? '收起工作区' : '展开工作区'"
            :aria-expanded="conversationConsoleVisible"
            data-conversation-console-toggle="true"
            @click="toggleConversationConsole"
          >
            <!-- 面板 + 折叠箭头（与主流产品的侧栏开关同形）：面板在右侧，
                 展开时箭头朝左、收起时朝右，点击即在开/关之间切换。 -->
            <svg class="h-[18px] w-[18px]" viewBox="0 0 16 16" aria-hidden="true">
              <rect x="1.55" y="2.65" width="12.9" height="10.7" rx="2.2" fill="none" stroke="currentColor" stroke-width="1.3" />
              <line x1="5.75" y1="2.65" x2="5.75" y2="13.35" stroke="currentColor" stroke-width="1.3" />
              <path v-if="conversationConsoleVisible" d="M12.35 8 8.55 5.1v5.8L12.35 8Z" fill="currentColor" />
              <path v-else d="M8.55 8 12.35 5.1v5.8L8.55 8Z" fill="currentColor" />
            </svg>
          </button>
        </div>
      </div>
    </div>

    <!-- 语义模型配置：全宽内容区，不占用左侧栏空间 -->
    <div v-if="!noMatchedDatasource && metricConfigMounted" v-show="viewMode === 'metricConfig'" class="flex flex-col overflow-hidden bg-[#F5F7FA]" style="height: calc(100vh - 130px)">
      <div class="h-12 shrink-0 flex flex-nowrap items-center gap-3 border-b border-slate-200 bg-white px-4">
        <span class="shrink-0 whitespace-nowrap text-[13px] font-medium text-slate-700">数据源组</span>
        <select
          v-model="selectedDatasourceId"
          class="h-8 w-[320px] max-w-[28vw] shrink-0 rounded-lg border border-slate-200 bg-white px-3 text-[13px] text-slate-800 outline-none"
        >
          <option v-if="datasources.length === 0" value="">暂无数据源</option>
          <option v-for="ds in datasources" :key="ds.id" :value="ds.id">
            {{ ds.group_name }}
          </option>
        </select>
        <span class="ml-4 shrink-0 whitespace-nowrap text-[13px] font-medium text-slate-700">语义模型</span>
        <el-select
          v-model="metricSelectedSemanticConfigId"
          class="min-w-0 flex-1"
          filterable
          :loading="loadingMetricSemanticConfigs"
          :disabled="loadingMetricSemanticConfigs || metricSemanticConfigs.length === 0"
          :title="currentMetricSemanticConfig ? formatMetricSemanticConfigLabel(currentMetricSemanticConfig) : ''"
          placeholder="请选择语义模型"
          loading-text="加载中"
          no-data-text="暂无语义模型"
          fit-input-width
          popper-class="chatdata-semantic-config-select-popper"
        >
          <el-option
            v-for="(config, configIndex) in metricSemanticConfigs"
            :key="config.id"
            :value="config.id"
            :label="formatSemanticConfigOptionLabel(config, configIndex)"
            :title="formatSemanticConfigOptionLabel(config, configIndex)"
          />
        </el-select>
        <button
          class="h-8 w-8 shrink-0 flex items-center justify-center rounded-lg border-none bg-blue-500 text-white text-[13px] transition-all hover:bg-blue-600 disabled:opacity-60 disabled:cursor-not-allowed"
          title="新建语义模型"
          :disabled="!selectedDatasourceId || creatingMetricConfig"
          @click="handleCreateMetricConfig"
        >
          <i :class="['fa-solid', creatingMetricConfig ? 'fa-spinner fa-spin' : 'fa-plus']"></i>
        </button>
      </div>
      <div class="flex-1 min-h-0">
        <SemanticConfigPanel
          v-if="selectedDatasourceId && metricConfigMounted"
          :key="`${selectedDatasourceId}-${metricConfigPanelKey}`"
          :datasource-id="selectedDatasourceId"
          :datasource-name="metricConfigDatasource?.group_name || ''"
          :datasource-type="resolveDatasourceType(metricConfigDatasource)"
          :tables="metricConfigDatasource?.ds_tables || []"
          :selected-config-id="metricSelectedSemanticConfigId"
          hide-dataset-selector
          embedded
          @config-change="() => { loadMetricSemanticConfigs(); loadSemanticConfigs() }"
          @update:selected-config-id="metricSelectedSemanticConfigId = $event"
          @close="handleCloseMetricConfigTab"
        />
        <div v-else class="h-full flex items-center justify-center text-sm text-slate-400">
          请选择数据源组
        </div>
      </div>
    </div>

    <!-- 下方内容区（82px 头部 + 48px 工具栏 = 130px） -->
    <div v-if="!noMatchedDatasource" v-show="viewMode !== 'metricConfig'" class="flex overflow-hidden" :class="{ 'select-none': isResizingSidebar }" style="height: calc(100vh - 130px)">
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
        <div ref="sidebarBodyRef" class="flex-1 overflow-hidden flex flex-col bg-white min-h-0">
          <!-- 收起侧边栏 -->
          <button
            class="absolute right-[-10px] z-30 w-5 h-5 flex items-center justify-center rounded-full border border-slate-200 bg-white text-gray-400 shadow-sm cursor-pointer transition-all duration-200 shrink-0 hover:text-gray-600 hover:border-slate-300"
            :style="{ top: `${sidebarCollapseTop}px` }"
            data-collapse-btn="true"
            title="收起侧边栏"
            @click="isSidebarVisible = false"
          >
            <i class="fa-solid fa-chevron-left text-[10px]"></i>
          </button>

          <!-- 左栏一级 Tab：仅智能问数模式有多个（对话历史 / 语义模型） -->
          <div
            v-if="viewMode === 'conversation' && sidebarTabs.length > 1"
            class="shrink-0 border-b border-slate-200 bg-white px-3 py-2"
          >
            <div class="flex items-center gap-1 rounded-lg bg-slate-100 p-1">
              <button
                v-for="tab in sidebarTabs"
                :key="tab.key"
                type="button"
                class="flex flex-1 items-center justify-center gap-1.5 rounded-md py-1.5 text-[13px] transition-colors"
                :class="activeSidebarTab === tab.key
                  ? 'bg-white font-medium text-[#165DFF] shadow-sm'
                  : 'text-slate-500 hover:text-slate-700'"
                @click="activeSidebarTab = tab.key"
              >
                <i :class="['fa-solid', tab.icon]" class="text-[11px]"></i>
                <span>{{ tab.label }}</span>
              </button>
            </div>
          </div>

          <!-- 看板（懒挂载，避免首屏无关请求） -->
          <div v-if="dashboardMounted" v-show="activeSidebarTab === 'dashboard'" class="sidebar-unified-shell h-full">
            <DashboardPanel
              :selected-dashboard-id="activeDashboardId"
              :dashboards="dashboards"
              :loading="loadingDefaultDashboard"
              :current-user-id="currentUser.id"
              @open-dashboard="handleOpenDashboard"
              @dashboards-change="handleDashboardsChange"
            />
          </div>

          <!-- 智能问数：对话 / 语义模型 -->
          <template v-if="viewMode === 'conversation'">
            <div v-show="activeSidebarTab === 'conversation'" class="sidebar-unified-shell h-full">
              <ConversationSidebar
                active-tab="conversation"
                :grouped-conversations="groupedConversations"
                :current-conversation-id="currentConversationId"
                :is-loading="isLoadingConversations"
                :is-loading-more="isLoadingMoreConversations"
                :has-more="hasMoreConversations"
                :conversation-view-mode="conversationViewMode"
                :hide-management="!!parentComponentName"
                @select-conversation="handleHistoryConversationSelect"
                @new-conversation="handleNewConversation"
                @load-more-conversations="loadMoreConversations"
                @load-more-conversation-group="loadMoreConversationGroup"
                @update-conversation-title="handleUpdateConversationTitle"
                @toggle-conversation-favorite="handleToggleConversationFavorite"
                @toggle-conversation-view-mode="toggleConversationViewMode"
              />
            </div>
            <div v-show="activeSidebarTab === 'metrics'" class="sidebar-unified-shell h-full">
              <ConversationSidebar
                active-tab="metrics"
                :datasource-id="selectedDatasourceId"
                :semantic-configs="enabledSemanticConfigs"
                :loading-configs="loadingSemanticConfigs"
                :bootstrap-loading="conversationSidebarBootstrapLoading"
                :selected-dataset-id="conversationSemanticConfigId"
                :tables="currentDatasource?.ds_tables || []"
                :hide-management="!!parentComponentName"
                @select-dataset="handleConversationDatasetSelected"
                @refresh-semantic-configs="loadSemanticConfigs"
                @fill-question="handleFillSemanticQuestion"
              />
            </div>
          </template>

          <!-- 辅助查询：数据表 -->
          <div v-show="viewMode === 'builder' && activeSidebarTab === 'tables'" class="sidebar-unified-shell h-full">
            <BuilderSidebar
              :datasource="currentDatasource"
              :semantic-config="currentSemanticConfig"
              :semantic-configs="enabledSemanticConfigs"
              :selected-dataset-id="selectedSemanticConfigId"
              :tables="currentDatasource?.ds_tables || []"
              :loading="loadingSemanticConfigs"
              @drag-start="handleBuilderDragStart"
              @drag-end="handleBuilderDragEnd"
              @add-dimension="handleBuilderAddDimension"
              @add-metric="handleBuilderAddMetric"
              @dataset-change="handleBuilderSemanticConfigSelected"
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

        <!-- 底部：统一上下文选择 / 数据源组管理
             两者并存时并成一体控件组（外框统一圆角/描边/阴影，中间 1px 分隔），
             避免两个各自带边框的独立按钮并排产生割裂感 -->
        <div v-if="showSidebarFooter" class="px-3 py-2.5 border-t border-slate-200 bg-slate-50 shrink-0">
          <div
            class="flex items-stretch overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm"
            :class="showSidebarContextBar ? 'min-w-0 flex-1' : 'ml-auto shrink-0'"
          >
            <button
              v-if="showSidebarContextBar"
              class="flex h-9 min-w-0 flex-1 items-center gap-2 px-3 text-left transition-colors hover:bg-slate-50"
              :title="currentContextSummary"
              @click="openContextSelector"
            >
              <span class="min-w-0 flex-1 truncate text-[13px] font-medium text-slate-800">{{ currentContextSummary }}</span>
              <i class="fa-solid fa-chevron-down text-[10px] text-slate-400 shrink-0"></i>
            </button>
            <!-- 显式分隔线（不用 divide-x：此项目 Tailwind 下未生成边框宽度） -->
            <span
              v-if="showSidebarContextBar && canManageDatasource"
              class="w-px shrink-0 bg-slate-200"
            ></span>
            <button
              v-if="canManageDatasource"
              class="flex h-9 w-9 shrink-0 items-center justify-center text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-600"
              title="后台管理"
              @click="openDatasourceManager"
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
        <div class="relative flex-1 min-h-0 overflow-hidden">
          <!-- 看板编辑器（优先显示） -->
          <div v-show="viewMode === 'dashboard' && !!activeDashboardId" class="absolute inset-0 overflow-hidden">
            <DashboardEditor
              v-if="dashboardEditorMounted && activeDashboardId"
              :key="activeDashboardId"
              :dashboard-id="activeDashboardId"
              :datasource-id="selectedDatasourceId"
              :datasources="datasources"
              :current-user-id="currentUser.id"
              class="h-full"
              @close="handleCloseDashboard"
            />
          </div>

          <!-- 问数模式（v-show 保持存活，切换 tab 不销毁） -->
          <div v-show="viewMode === 'conversation'" class="absolute inset-0 overflow-hidden">
            <div class="h-full flex min-h-0">
              <div class="flex-1 min-w-0 min-h-0">
                <AnalysisConversation
                  ref="conversationRef"
                  :key="conversationKey"
                  :datasource="currentDatasource"
                  :conversation-id="currentConversationId"
                  :selected-datasource-id="selectedDatasourceId"
                  :semantic-config-id="conversationSemanticConfigId"
                  :semantic-config-selection="conversationSemanticConfigSelection"
                  :ensure-conversation-id="ensureConversationId"
                  :ensure-conversation-datasource="ensureConversationDatasource"
                  :execution-targets="executionTargets"
                  :execution-target-multiple="true"
                  :selected-member-datasource-ids="conversationMemberDatasourceIds"
                  v-model:selected-model-id="selectedConversationModelId"
                  class="h-full"
                  @first-message-started="handleFirstMessageStarted"
                  @run-status-changed="handleConversationRunStatusChanged"
                  @console-visibility-changed="conversationConsoleVisible = $event"
                  @update:selected-member-datasource-ids="conversationMemberDatasourceIds = $event"
                  @dashboard-created="handleDashboardCreatedFromConversation"
                  @semantic-config-selected="handleConversationSemanticConfigSelected"
                />
              </div>
            </div>
          </div>

          <!-- 辅助查询模式 -->
          <div v-if="builderMounted" v-show="viewMode === 'builder'" class="absolute inset-0 overflow-hidden">
            <QueryBuilder
              ref="queryBuilderRef"
              :datasource-id="selectedDatasourceId"
              :datasource="currentDatasource"
              :datasources="datasources"
              :semantic-config-id="selectedSemanticConfigId"
              :semantic-config="currentSemanticConfig"
              :semantic-configs="enabledSemanticConfigs"
              :initial-builder-config="options?.initialBuilderConfig as BuilderConfig | undefined"
              :execution-targets="executionTargets"
              :execution-target-multiple="true"
              :selected-member-datasource-ids="selectedMemberDatasourceIds"
              :on-sql-change="options?.onSqlChange"
              :hide-chart-panel="!!options?.hideBuilderChartPanel"
              hide-execution-target-selector
              hide-sidebar
              class="h-full"
              @datasource-change="handleDatasourceChange"
              @semantic-config-change="handleBuilderSemanticConfigSelected"
              @update:selected-member-datasource-ids="selectedMemberDatasourceIds = $event"
            />
          </div>

          <!-- 自由查询模式 -->
          <div v-if="editorMounted" v-show="viewMode === 'editor'" class="absolute inset-0 overflow-hidden">
            <SqlEditor
              ref="sqlEditorRef"
              :datasource-id="selectedDatasourceId"
              :datasource="currentDatasource"
              :table-schema="tableSchema"
              :initial-sql="options?.initialSql"
              :execution-targets="executionTargets"
              :execution-target-multiple="true"
              :selected-member-datasource-ids="selectedMemberDatasourceIds"
              :on-sql-change="options?.onSqlChange"
              hide-execution-target-selector
              hide-sidebar
              class="h-full"
              @update:selected-member-datasource-ids="selectedMemberDatasourceIds = $event"
            />
          </div>

          <!-- 数据看板模式（未选中具体看板时） -->
          <div v-show="viewMode === 'dashboard' && !activeDashboardId" class="absolute inset-0 flex items-center justify-center text-sm text-slate-400">
            请选择左侧数据看板
          </div>
        </div>
      </div>
    </div>

    <!-- 语义配置页面（整页） -->
    <div v-if="isSemanticConfigPanelOpen" class="absolute inset-0 z-[2050] bg-[#F5F7FA]">
      <SemanticConfigPanel
        :datasource-id="semanticConfigDatasourceId"
        :datasource-name="semanticConfigDatasource?.group_name || ''"
        :datasource-type="resolveDatasourceType(semanticConfigDatasource)"
        :tables="semanticConfigDatasource?.ds_tables || []"
        admin-mode
        @close="handleCloseSemanticConfig"
      />
    </div>

    <el-dialog v-model="showContextSelectorDialog" width="520px" title="选择执行对象">
      <div class="space-y-4">
        <div>
          <div class="mb-2 text-[13px] font-medium text-slate-700">当前数据源组</div>
          <el-select
            v-model="draftSelectedDatasourceId"
            class="w-full"
            :disabled="!canChangeDraftDatasource"
            placeholder="请选择数据源组"
            filterable
          >
            <el-option
              v-for="ds in datasources"
              :key="ds.id"
              :value="ds.id"
              :label="ds.group_name || ds.id"
            />
          </el-select>
        </div>

        <div>
          <div class="mb-2 text-[13px] font-medium text-slate-700">执行对象</div>
          <ExecutionTargetPicker
            v-if="draftExecutionTargets.length > 0"
            v-model="draftSelectedMemberDatasourceIds"
            :targets="draftExecutionTargets"
            :multiple="true"
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

    <ChatDataAdminManager
      v-if="canManageAdmin"
      v-model="showAdminModal"
      @datasources-updated="handleAdminDatasourcesUpdated"
      @open-semantic-config="handleOpenSemanticConfig"
    />
    </el-config-provider>
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
  width: auto;
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
  min-width: 104px;
  padding: 0 16px;
  font-size: 13px;
  font-weight: 600;
  line-height: 1;
  white-space: nowrap;
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

.chatdata-message-box-overlay {
  z-index: 20000 !important;
}

.chatdata-message-box {
  z-index: 20001 !important;
}

</style>
