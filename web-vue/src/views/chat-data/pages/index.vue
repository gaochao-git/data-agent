<script setup lang="ts">
/**
 * ChatData 智能问数主页面
 * 参考 React 版本布局，支持数据智能体、可视化查询、SQL查询、数据看板、指标集配置
 */
import { ref, computed, watch, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
// 会话 markdown 渲染样式（.conversation-markdown）：主对话与子智能体 trail 共用，
// 全局注入但随本模块走，便于整个 chat-data 目录迁移到其它主项目。
import '../styles/markdown.css'
// z-index 层级变量（:root CSS 变量，本模块层级唯一事实来源，定义见文件头注释）
import '../styles/z-index.css'
import useUserStore from '@/store/modules/user'
import { getDatasources, createConversation, generateConversationTitle, getConversationContext, getSemanticConfigs, getMySemanticConfigs, createSemanticConfig, getDatasourceSchema } from '../api'
import { listDashboards, type Dashboard } from '../api/dashboard'
import type { BuilderDragFieldData, DataSource, ViewMode, ChatDataOptions, SemanticConfigItem, SemanticYamlFile, ExecutionTargetOption, TableInfo } from '../types'
import { normalizeTableInfoList, resolveDatasourceType } from '../utils/datasourceSchema'
import Conversation from '../components/conversation/Conversation.vue'
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
import OssieImportDialog from '../components/semantic/OssieImportDialog.vue'
import ChatDataAdminManager from '../components/ChatDataAdminManager.vue'
import AgentScheduleManager from '../components/AgentScheduleManager.vue'
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
import type { ConversationSortBy, ConversationViewMode } from '../utils/conversationHistory'
import { useConversationHistoryBrowser } from '../composables/useConversationHistoryBrowser'
import { useModelOptions } from '../composables/useModelOptions'
import { resolveVisibleModes } from './viewModes'

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
const selectedDatasourceId = ref<string>('')
const loadingDatasources = ref(true)
const noMatchedDatasource = ref(false)
// 无数据源整页空态开关：2026-09-20 起按用户要求改为「页面常显、仅禁查询」——
// 无数据源时仍展示侧栏/各模式页签（SQL 编辑框等场景需要），查询动作在各自入口被守卫拦截，
// 数据源组切换位置显示「请先配置数据源组」。恢复整页空态：把本开关改回 true 即可（模板四处
// isNoDatasourceEmptyPage 条件与空态块随之还原，无需其他改动）。
const SHOW_NO_DATASOURCE_EMPTY_PAGE = false
const isNoDatasourceEmptyPage = computed(() => noMatchedDatasource.value && SHOW_NO_DATASOURCE_EMPTY_PAGE)

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

// 定时任务管理弹窗（用户侧，侧边栏入口）
const showAgentScheduleModal = ref(false)
const handleOpenAgentSchedules = () => {
  showAgentScheduleModal.value = true
}
const handleOpenSkillManager = () => {
  conversationRef.value?.openSkillManager()
}
const handleOpenScheduledConversation = (conversationId: string) => {
  showAgentScheduleModal.value = false
  void handleHistoryConversationSelect(conversationId)
}
const openDatasourceManager = () => {
  if (!canManageAdmin.value) return
  showAdminModal.value = true
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

// 后台改了表分组/表业务说明/字段字典后，本地 schema 缓存必须失效并重拉，
// 否则 SQL 查询侧栏仍按旧分组展示，必须手点刷新才能看到。
const reloadDatasourceSchemaAfterAdminChange = async () => {
  const currentDatasourceId = String(selectedDatasourceId.value || '').trim()
  // 只保留当前数据源的旧缓存，避免重拉期间表列表闪空；其余数据源下次切换时按需重拉
  const currentCachedTables = currentDatasourceId ? datasourceSchemaCache.value[currentDatasourceId] : undefined
  datasourceSchemaCache.value = currentDatasourceId && currentCachedTables
    ? { [currentDatasourceId]: currentCachedTables }
    : {}
  if (!currentDatasourceId) return
  await loadDatasourceSchema(currentDatasourceId, true)
}

// 数据源管理回调：后台配置更新后重新加载前台授权数据源列表
const handleAdminDatasourcesUpdated = () => {
  void loadDatasources()
  void reloadDatasourceSchemaAfterAdminChange()
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

// 功能开关（终端用户语义模型入口）必须在 visibleModes 之前取到：
// resolveInitialMode() 在 setup 期就会求值 visibleModes，晚声明会踩 TDZ。
const { load: loadModelOptions, userSemanticModelEnabled: canCreateUserSemanticModel } = useModelOptions()

// 模式列表
const allModes = computed(() => ([
  { key: 'conversation' as ViewMode, label: modeLabels.value.conversation },
  { key: 'builder' as ViewMode, label: modeLabels.value.builder },
  { key: 'editor' as ViewMode, label: modeLabels.value.editor },
  { key: 'dashboard' as ViewMode, label: modeLabels.value.dashboard },
  { key: 'metricConfig' as ViewMode, label: modeLabels.value.metricConfig },
]))

// 过滤隐藏模式：宿主 hideTabs + 语义模型入口开关（关闭时整页签隐藏）
const visibleModes = computed(() =>
  resolveVisibleModes(allModes.value, props.options?.hideTabs, canCreateUserSemanticModel.value)
)

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

// 侧边栏宽度拖拽调整（默认 320：会话项需容纳标题 + 时间 + 运行状态 + 收藏。
// 竖排 Tab 轨道悬浮在侧栏右缘之外、侵入会话区 32px，因此不占用侧栏宽度，
// 侧栏宽度即内容区宽度，与没有轨道时一致）
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

// 竖排 Tab 轨道：贴侧栏右缘、悬浮侵入会话区 32px（会话区左侧留白本来空着，正好利用）；
// 折叠态下整条侧栏就是这个窄条，两种状态 Tab 垂直位置一致
const showVerticalTabRail = computed(() => viewMode.value === 'conversation' && sidebarTabs.value.length > 1)

// 收起按钮水平锚点：统一回到侧栏右缘的拖拽条线上（原版位置 right-[-10px]，按钮中心
// 压在分隔条上）；轨道虽悬浮在右缘外 32px，但按钮 z-30 压过轨道 z-20，视觉不被遮挡。
// 若之后想让按钮跟随轨道外缘，可改回 right-[-42px]（轨道 32px + 按钮半宽 10px）
const collapseButtonOffsetClass = computed(() => 'right-[-10px]')

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

// 收起/展开按钮位置已固定在竖排轨道顶部（见模板 top-1），
// 原先「跟随面板首个控件」的动态对齐逻辑随顶部分段 Tab 一并废弃（git 历史可找回）。

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

// 语义模型配置 tab 的数据源上下文（表结构缓存由 cacheDatasourceSchema/loadDatasourceSchema 维护）
const metricConfigDatasource = computed(() => {
  const datasourceId = selectedDatasourceId.value
  const datasource = allDatasources.value.find(ds => ds.id === datasourceId)
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

/**
 * 会话工作区 yaml「一键导入」：权限与配置入口同源（canManageDatasource 门禁），
 * 直接打开 Ossie 导入弹窗并预置文件（弹窗挂在本页，不经配置面板中转），
 * 省去「下载 → 配置页 → 导入按钮 → 选文件」的中间步骤。
 */
const conversationOssieImportVisible = ref(false)
const conversationOssieImportDatasourceId = ref('')
const conversationOssieImportDialogRef = ref<InstanceType<typeof OssieImportDialog> | null>(null)

const handleImportSemanticYamlFromConversation = (files: SemanticYamlFile[]) => {
  if (!canManageDatasource.value || !files?.length) return
  const datasourceId = currentConversationDatasourceId.value || selectedDatasourceId.value
  if (!datasourceId) {
    ElMessage.warning('未找到会话绑定的数据源组，无法导入')
    return
  }
  conversationOssieImportDatasourceId.value = datasourceId
  conversationOssieImportDialogRef.value?.openWithFiles(files)
}

// 会话
const currentConversationId = ref<string | null>(null)
const currentConversationTitle = ref<string>('新会话')
const currentConversationDatasourceId = ref<string | null>(null)
const conversationPageSize = 20
const conversationKey = ref(0)
// 模型选择跟随当前浏览器标签页（对话窗口）：刷新/路由切换/新建任务后都保持；
// 只有模型从注册表移除时才由 Conversation 的 modelOptions watcher 回填默认模型。
// 用 sessionStorage 而非 localStorage，避免新开标签页或下次打开系统时继承上一次的临时选择。
const SELECTED_MODEL_SESSION_KEY = 'chatdata.current_window_model_id'
const readWindowSelectedModel = (): string => {
  try {
    return sessionStorage.getItem(SELECTED_MODEL_SESSION_KEY) || ''
  } catch {
    return ''
  }
}
const rememberWindowSelectedModel = (value: string) => {
  try {
    if (value) sessionStorage.setItem(SELECTED_MODEL_SESSION_KEY, value)
    else sessionStorage.removeItem(SELECTED_MODEL_SESSION_KEY)
  } catch {
    // storage 被宿主禁用时只丢失记忆，不影响功能
  }
}
const selectedConversationModelId = ref(readWindowSelectedModel())
watch(selectedConversationModelId, (value) => {
  rememberWindowSelectedModel(value || '')
})

// SqlEditor 组件引用
const sqlEditorRef = ref<InstanceType<typeof SqlEditor> | null>(null)
const conversationRef = ref<InstanceType<typeof Conversation> | null>(null)

// 控制台面板可见性：由 Conversation 广播（面板内的 ✕ 也会触发），
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

const loadDatasourceSchema = async (datasourceId: string, force = false): Promise<boolean> => {
  const normalizedDatasourceId = String(datasourceId || '').trim()
  if (!normalizedDatasourceId) return false
  if (!force && datasourceSchemaCache.value[normalizedDatasourceId]) return true
  loadingDatasourceSchema.value = true
  try {
    const res = await getDatasourceSchema(normalizedDatasourceId)
    if (res.status === 'success') {
      datasourceSchemaCache.value = {
        ...datasourceSchemaCache.value,
        [normalizedDatasourceId]: normalizeTableInfoList(res.data?.tables),
      }
      return true
    }
    return false
  } catch (err) {
    console.error('加载数据源表结构失败:', err)
    return false
  } finally {
    loadingDatasourceSchema.value = false
  }
}

// 手动刷新 SQL 查询侧栏的表列表：强制绕过 schema 缓存重新拉取当前数据源的表结构
const handleRefreshEditorTables = async () => {
  const datasourceId = String(selectedDatasourceId.value || '').trim()
  if (!datasourceId) return
  const refreshed = await loadDatasourceSchema(datasourceId, true)
  if (refreshed) {
    ElMessage.success('表列表已更新')
  } else {
    ElMessage.error('刷新表列表失败，请稍后重试')
  }
}

// 确保会话 ID 存在（发送首条消息时兜底创建），创建即绑定当前数据源组；
// 服务端建会话时同步物化启用语义模型到工作区，发送后 @ 引用即可选。
// 选择器弹窗确认只记草稿不调本函数——与「新建任务」同口径，重活延迟到发送首条消息。
const ensureConversationId = async (): Promise<string | null> => {
  if (currentConversationId.value) {
    return currentConversationId.value
  }
  try {
    const res = await createConversation({ agent_id: 'chat_data', datasource_id: selectedDatasourceId.value || undefined })
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

// 会话列表视图模式：grouped 按数据源分组（默认），flat 平铺；排序：updated 最近更新（默认），created 创建时间
const CONVERSATION_VIEW_MODE_KEY = 'chatdata:view-mode'
const CONVERSATION_SORT_BY_KEY = 'chatdata:sort-by'

const conversationViewMode = ref<ConversationViewMode>(
  readLocalStorage(CONVERSATION_VIEW_MODE_KEY) === 'flat' ? 'flat' : 'grouped',
)
const conversationSortBy = ref<ConversationSortBy>(
  readLocalStorage(CONVERSATION_SORT_BY_KEY) === 'created' ? 'created' : 'updated',
)

const {
  conversations,
  groupedConversations,
  isLoadingConversations,
  isRefreshingConversations,
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
  sortBy: conversationSortBy,
})

const setConversationViewMode = (mode: ConversationViewMode) => {
  if (conversationViewMode.value === mode) return
  conversationViewMode.value = mode
  writeLocalStorage(CONVERSATION_VIEW_MODE_KEY, mode)
  void ensureConversationsLoaded()
}

const setConversationSortBy = (sortBy: ConversationSortBy) => {
  if (conversationSortBy.value === sortBy) return
  conversationSortBy.value = sortBy
  writeLocalStorage(CONVERSATION_SORT_BY_KEY, sortBy)
}

// 手动刷新：强制重载当前视图首屏（ensureConversationsLoaded 内部保留旧数据展示，刷新期间不闪空态）
const handleRefreshConversations = () => {
  void ensureConversationsLoaded(true)
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

// 新建会话（只清空状态；选数据源确认时创建会话，发送消息兜底创建）
const handleNewConversation = () => {
  currentConversationId.value = null
  currentConversationTitle.value = '新会话'
  currentConversationDatasourceId.value = null
  conversationKey.value++ // 强制重新渲染以清空消息
  // 模型注册表是模块级会话期缓存，后端改配置后本页不会自动感知，所以新建会话时
  // 强制重拉 /config（后台静默）；但保留当前窗口已选模型，避免每次新建任务都静默
  // 落回注册表默认模型（评测 OBS-01）。仅当该模型已从注册表移除时，由 Conversation
  // 内的 watch(modelOptions) 回填默认模型。
  loadModelOptions(true)
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
  // 无任何可用数据源：发送必被后端拒绝，这里提前拦下并说明原因
  if (noMatchedDatasource.value) {
    ElMessage.warning('请先配置数据源组')
    return false
  }
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

// 会话标题占位符：与后端 generate_title 的占位符集合保持一致。
// 命名只成功写库一次；失败时后端不落降级标题（保持占位符），
// 由下一轮 Run 终态再次触发，直到成功自停。
const CONVERSATION_TITLE_PLACEHOLDERS = new Set(['', '新会话', '未命名会话'])
const titleRetryInFlight = new Set<string>()

const retryConversationTitleIfNeeded = async (conversationId: string) => {
  if (titleRetryInFlight.has(conversationId)) return
  const currentTitle = currentConversationId.value === conversationId
    ? currentConversationTitle.value
    : conversations.value.find(item => item.id === conversationId)?.title || ''
  if (!CONVERSATION_TITLE_PLACEHOLDERS.has(currentTitle.trim())) return
  titleRetryInFlight.add(conversationId)
  try {
    const res = await generateConversationTitle(conversationId, selectedConversationModelId.value || undefined)
    if (res?.status === 'success' && res.data?.title) {
      if (currentConversationId.value === conversationId) {
        currentConversationTitle.value = res.data.title
      }
      updateHistoryConversationTitle(conversationId, res.data.title)
      invalidateInactiveView()
    }
  } catch (error) {
    // 失败保持占位符，等下一轮 Run 终态再试
    console.error('重试生成会话标题失败:', error)
  } finally {
    titleRetryInFlight.delete(conversationId)
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
    // 标题仍是占位符的会话借本轮终态重试命名（直到成功自停）
    void retryConversationTitleIfNeeded(conversationId)
  }
}

// 置顶会改变会话所属分组，成功后重载分组首屏，避免同一会话残留在旧目录。
const handleToggleConversationFavorite = async (conversationId: string, isFavorited: boolean) => {
  await refreshAfterFavorite(conversationId, isFavorited)
}

// 首条消息对应的 SSE 建立后立即生成标题并完成分组迁移，不等待助手输出结束。
const handleFirstMessageStarted = async (convId: string) => {
  const datasourcePromise = loadConversationDatasourceId(convId)
  const titlePromise = generateConversationTitle(convId, selectedConversationModelId.value || undefined)
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
      if (selectedSemanticConfigId.value && !semanticConfigs.value.find(c => c.id === selectedSemanticConfigId.value && c.enabled)) {
        selectedSemanticConfigId.value = null
      }
    }
  } catch (err) {
    console.error('加载指标集失败:', err)
    semanticConfigs.value = []
    selectedSemanticConfigId.value = null
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

// B 端策略：语义模型由管理员统一维护，默认不向终端用户开放「自定义语义模型」入口。
// 开关走后台变量 CHAT_DATA_USER_SEMANTIC_MODEL_ENABLED（「运行配置 → 功能开关」）：
// 默认关闭；需要存量迁移或临时自助建模时打开即可，创建逻辑与后端接口一直保留。
// 判定口径（严格 true / fail-closed）在 useModelOptions 内单点实现并可单测。

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

const enabledSemanticConfigs = computed(() => semanticConfigs.value.filter(cfg => cfg.enabled))
const enabledMetricSemanticConfigs = computed(() => metricSemanticConfigs.value.filter(cfg => cfg.enabled))

// 表结构 schema (用于 SQL 编辑器自动补全)

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

const canChangeDraftDatasource = computed(() => datasources.value.length > 1)

// 数据源组下拉被禁用时的原因提示：仅剩「只有一个数据源组」一条路径；
// 会话已绑定组不再锁死下拉——换组语义 = 基于新组新建任务（见 isGroupSwitchNewTask）
const datasourceSelectLockReason = computed(() => {
  if (!showContextSelectorDialog.value) return ''
  if (datasources.value.length <= 1) return '当前仅有一个数据源组，无可切换项'
  return ''
})

// 会话模式下把数据源组换成别的组 = 基于新组新建任务（与侧栏「新建任务」按钮同一条链路）：
// 当前会话保留在历史列表，确认后进入绑定新组的新任务。同组确认仍只调整执行对象，不新建。
const isGroupSwitchNewTask = computed(() => (
  isConversationContextMode.value
  && Boolean(currentConversationDatasourceId.value)
  && Boolean(draftSelectedDatasourceId.value)
  && draftSelectedDatasourceId.value !== currentConversationDatasourceId.value
))

const openContextSelector = () => {
  // 无数据源：不打开选择器（无可选项），提示原因；管理员可走旁边齿轮入口配置
  if (noMatchedDatasource.value) {
    ElMessage.warning(noMatchedDatasourceDesc.value)
    return
  }
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
  const datasourceChanged = Boolean(draftSelectedDatasourceId.value && draftSelectedDatasourceId.value !== selectedDatasourceId.value)
  if (datasourceChanged) {
    selectedDatasourceId.value = draftSelectedDatasourceId.value
  }
  if (isConversationContextMode.value) {
    if (isGroupSwitchNewTask.value) {
      // 换组 = 基于新组新建任务：重置会话态（已有会话保留在历史），新任务在
      // 首次使用会话工作区（发送首条消息 / 输入 @）时兜底创建；模型选择同
      // 「新建任务」口径——保留当前窗口已选模型，只刷新注册表
      currentConversationId.value = null
      currentConversationTitle.value = '新会话'
      currentConversationDatasourceId.value = null
      conversationKey.value++
      loadModelOptions(true)
    }
    conversationMemberDatasourceIds.value = nextIds
    // 确认 = 草稿记录，与「新建任务」「分析模式切组」同口径（0 请求瞬时完成）。
    // 会话落库与语义模型物化延迟到发送首条消息时兜底发生，三条路径体感一致。
    if (!currentConversationId.value) {
      currentConversationDatasourceId.value = selectedDatasourceId.value || null
    }
  } else {
    selectedMemberDatasourceIds.value = nextIds
  }
  showContextSelectorDialog.value = false
}

const currentContextSummary = computed(() => {
  // 无数据源：数据源组位置直接提示待配置（页面常显方案下这是主要提示位）
  if (noMatchedDatasource.value) return '请先配置数据源组'
  const datasourceName = currentDatasource.value?.group_name || '暂无数据源'
  return `${datasourceName} / ${selectedExecutionTargetSummary.value}`
})

const showSidebarContextBar = computed(() => {
  return noMatchedDatasource.value || datasources.value.length > 0 || executionTargets.value.length > 0
})

const showSidebarFooter = computed(() => {
  if (viewMode.value === 'dashboard') return false
  return showSidebarContextBar.value || canManageDatasource.value
})

watch(selectedDatasourceId, () => {
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

const handleBuilderSemanticConfigSelected = (configId: string) => {
  const normalizedId = String(configId || '').trim()
  if (!enabledSemanticConfigs.value.some(config => config.id === normalizedId)) return
  selectedSemanticConfigId.value = normalizedId
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
  // 功能开关随 /config 下发；页签可见性依赖它，故不等待会话视图挂载就主动拉一次
  // （进入页面直接停在 SQL/看板时，Conversation 可能一直不挂载，开关值会永远缺失）。
  void loadModelOptions()
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
    <!-- 未匹配数据源整页空态（默认关闭，见 SHOW_NO_DATASOURCE_EMPTY_PAGE 注释；页面改为常显+入口守卫） -->
    <div v-if="isNoDatasourceEmptyPage" class="flex-1 flex items-center justify-center">
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
    <div v-if="!isNoDatasourceEmptyPage" class="h-10 px-5 flex items-end bg-[#EEF1F5] border-b border-[#B8C1CE]">
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
            <!-- 面板 + 折叠箭头（与主流产品的侧栏开关同形）：面板近正方、细描边，
                 箭头为线性 chevron（非实心三角），展开时朝左、收起时朝右。 -->
            <svg class="h-[18px] w-[18px]" viewBox="0 0 16 16" aria-hidden="true">
              <rect x="1.75" y="1.75" width="12.5" height="12.5" rx="2.5" fill="none" stroke="currentColor" stroke-width="1.2" />
              <line x1="5.9" y1="1.75" x2="5.9" y2="14.25" stroke="currentColor" stroke-width="1.2" />
              <path
                v-if="conversationConsoleVisible"
                d="M9.9 5.6 7.5 8l2.4 2.4"
                fill="none"
                stroke="currentColor"
                stroke-width="1.2"
                stroke-linecap="round"
                stroke-linejoin="round"
              />
              <path
                v-else
                d="M7.5 5.6 9.9 8l-2.4 2.4"
                fill="none"
                stroke="currentColor"
                stroke-width="1.2"
                stroke-linecap="round"
                stroke-linejoin="round"
              />
            </svg>
          </button>
        </div>
      </div>
    </div>

    <!-- 语义模型配置：全宽内容区，不占用左侧栏空间 -->
    <div v-if="!isNoDatasourceEmptyPage && metricConfigMounted" v-show="viewMode === 'metricConfig'" class="flex flex-col overflow-hidden bg-[#F5F7FA]" style="height: calc(100vh - 130px)">
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
        <!-- 页签可见性已由功能开关统一控制（关闭时整页签隐藏），按钮恢复为常显，避免开关双重裁决 -->
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
          :allow-create="canCreateUserSemanticModel"
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
    <div v-if="!isNoDatasourceEmptyPage" v-show="viewMode !== 'metricConfig'" class="flex overflow-hidden" :class="{ 'select-none': isResizingSidebar }" style="height: calc(100vh - 130px)">
      <!-- 左侧：公用侧边栏 -->
      <!-- 折叠态：窄条（32px）。与展开态右缘悬浮轨道同构：按钮悬挂在条外缘顶部（top-1），
           Tab 从 pt-8 起——两种状态下 Tab 的垂直位置完全一致（横向跟随侧栏边界） -->
      <div v-if="!isSidebarVisible" class="relative flex flex-col items-stretch gap-1 bg-white border-r border-slate-200 shrink-0 w-8 pt-8 pb-3">
        <button
          type="button"
          class="absolute right-[-10px] top-1 z-30 w-5 h-5 flex items-center justify-center rounded-full border border-slate-200 bg-white text-gray-400 shadow-sm cursor-pointer transition-all duration-200 hover:text-gray-600 hover:border-slate-300"
          title="展开侧边栏"
          @click="isSidebarVisible = true"
        >
          <i class="fa-solid fa-chevron-right text-[10px]"></i>
        </button>
        <div
          v-for="tab in sidebarTabs"
          :key="tab.key"
          :class="[
            'writing-vertical w-full text-xs py-3 px-1 cursor-pointer transition-all duration-200 select-none',
            activeSidebarTab === tab.key ? 'text-blue-500 font-medium bg-blue-50' : 'text-gray-400 hover:text-gray-600 hover:bg-gray-50'
          ]"
          @click="activeSidebarTab = tab.key; isSidebarVisible = true"
        >{{ tab.label }}</div>
      </div>
      <!-- 展开态 -->
      <!-- 布局与折叠态对应：竖排 Tab 轨道贴侧栏右缘、悬浮侵入会话区 32px（不占侧栏宽度，
           会话区左侧留白正好利用）；收起/展开按钮悬挂在轨道外缘顶部（top-1），Tab 从 pt-8
           起排——两种状态下 Tab 的垂直位置完全一致，横向跟随侧栏边界移动。
           旧版顶部分段 Tab 按约定隐藏保留（见下方注释块），恢复方法见其注释。 -->
      <div v-show="isSidebarVisible" class="relative flex bg-slate-50 border-r border-slate-200 shrink-0" :style="{ width: `${sidebarWidth}px` }">
        <!-- 收起侧边栏（统一压在右缘拖拽条线上） -->
        <button
          type="button"
          class="absolute top-1 z-30 w-5 h-5 flex items-center justify-center rounded-full border border-slate-200 bg-white text-gray-400 shadow-sm cursor-pointer transition-all duration-200 hover:text-gray-600 hover:border-slate-300"
          :class="collapseButtonOffsetClass"
          data-collapse-btn="true"
          title="收起侧边栏"
          @click="isSidebarVisible = false"
        >
          <i class="fa-solid fa-chevron-left text-[10px]"></i>
        </button>

        <!-- 左列：面板内容 + 底部上下文栏 -->
        <div class="flex min-w-0 flex-1 flex-col">
          <!-- 侧边栏内容 -->
          <div class="flex-1 overflow-hidden flex flex-col bg-white min-h-0">

            <!-- 旧版顶部分段 Tab（2026-09-12 起被右缘竖排轨道取代，按「只隐藏不删除」约定保留；
                 恢复方法：删除「右缘竖排 Tab 轨道」块、把本内容列改回与底部栏平级的直接子节点、
                 展开态容器改回 flex-col，再取消本注释块）：仅智能问数模式有多个 tab（对话历史 / 语义模型）
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
            -->

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
              <div v-show="activeSidebarTab === 'conversation'" class="sidebar-unified-shell sidebar-unified-shell--conversation h-full">
                <ConversationSidebar
                  active-tab="conversation"
                  :grouped-conversations="groupedConversations"
                  :current-conversation-id="currentConversationId"
                  :is-loading="isLoadingConversations"
                  :is-refreshing="isRefreshingConversations"
                  :is-loading-more="isLoadingMoreConversations"
                  :has-more="hasMoreConversations"
                  :conversation-view-mode="conversationViewMode"
                  :conversation-sort-by="conversationSortBy"
                  :hide-management="!!parentComponentName"
                  @select-conversation="handleHistoryConversationSelect"
                  @new-conversation="handleNewConversation"
                  @load-more-conversations="loadMoreConversations"
                  @load-more-conversation-group="loadMoreConversationGroup"
                  @update-conversation-title="handleUpdateConversationTitle"
                  @toggle-conversation-favorite="handleToggleConversationFavorite"
                  @update-conversation-view-mode="setConversationViewMode"
                  @update-conversation-sort-by="setConversationSortBy"
                  @refresh-conversations="handleRefreshConversations"
                  @open-agent-schedules="handleOpenAgentSchedules"
                  @open-skill-manager="handleOpenSkillManager"
                />
              </div>
              <div v-show="activeSidebarTab === 'metrics'" class="sidebar-unified-shell sidebar-unified-shell--conversation h-full">
                <ConversationSidebar
                  active-tab="metrics"
                  :datasource-id="selectedDatasourceId"
                  :semantic-configs="enabledSemanticConfigs"
                  :loading-configs="loadingSemanticConfigs"
                  :bootstrap-loading="conversationSidebarBootstrapLoading"
                  :tables="currentDatasource?.ds_tables || []"
                  :hide-management="!!parentComponentName"
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
                :refreshing="loadingDatasourceSchema"
                @insert-table="handleInsertTable"
                @insert-column="handleInsertColumn"
                @refresh="handleRefreshEditorTables"
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

        <!-- 右缘竖排 Tab 轨道：悬浮在侧栏右缘之外、侵入会话区 32px，与折叠态窄条同构
             （按钮下方 pt-8 起排，垂直位置一致）；仅智能问数模式有多个 tab。
             容器完全透明、无边框无阴影——只是两个文字标签浮在会话区留白上，
             hover/选中时才出现背景，避免读成一条白条。
             容器 pointer-events-none + z-40：① Tab 块（pointer-events-auto）压过拖拽条 z-30，
             最左 8px 与拖拽条重叠处点得中；② 轨道空白区不再拦截会话区点击（此前整条 32px
             全高都在挡主区） -->
        <div
          v-if="showVerticalTabRail"
          class="pointer-events-none absolute right-[-32px] top-0 z-40 flex h-full w-8 flex-col items-stretch gap-1 pt-8 pb-3"
        >
          <div
            v-for="tab in sidebarTabs"
            :key="tab.key"
            :class="[
              'writing-vertical w-full pointer-events-auto text-xs py-3 px-1 cursor-pointer transition-all duration-200 select-none',
              activeSidebarTab === tab.key ? 'text-blue-500 font-medium bg-blue-50' : 'text-gray-400 hover:text-gray-600 hover:bg-gray-100'
            ]"
            :title="tab.label"
            @click="activeSidebarTab = tab.key"
          >{{ tab.label }}</div>
        </div>
      </div>

      <!-- 侧边栏拖动分隔条（z-30 压过悬浮 Tab 轨道，保证右缘 8px 仍可拖拽调宽） -->
      <div
        v-show="isSidebarVisible"
        class="w-2 cursor-col-resize flex items-center justify-center transition-colors duration-150 shrink-0 hover:bg-blue-50 group relative z-30"
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
                <Conversation
                  ref="conversationRef"
                  :key="conversationKey"
                  :datasource="currentDatasource"
                  :conversation-id="currentConversationId"
                  :selected-datasource-id="selectedDatasourceId"
                  :ensure-conversation-id="ensureConversationId"
                  :ensure-conversation-datasource="ensureConversationDatasource"
                  :execution-targets="executionTargets"
                  :execution-target-multiple="true"
                  :selected-member-datasource-ids="conversationMemberDatasourceIds"
                  v-model:selected-model-id="selectedConversationModelId"
                  :can-import-semantic-model="canManageDatasource"
                  @import-semantic-yaml="handleImportSemanticYamlFromConversation"
                  class="h-full"
                  @first-message-started="handleFirstMessageStarted"
                  @run-status-changed="handleConversationRunStatusChanged"
                  @console-visibility-changed="conversationConsoleVisible = $event"
                  @update:selected-member-datasource-ids="conversationMemberDatasourceIds = $event"
                  @dashboard-created="handleDashboardCreatedFromConversation"
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

    <!-- 会话工作区 yaml「一键导入」弹窗：由 ConversationWorkspacePanel 的导入按钮触发（管理员门禁） -->
    <OssieImportDialog
      ref="conversationOssieImportDialogRef"
      v-model:visible="conversationOssieImportVisible"
      :datasource-id="conversationOssieImportDatasourceId"
    />

    <el-dialog
      v-model="showContextSelectorDialog"
      width="520px"
      title="选择执行对象"
    >
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
          <div
            v-if="datasourceSelectLockReason"
            class="mt-1.5 flex items-start gap-1.5 text-xs text-slate-500"
          >
            <i class="fa-solid fa-circle-info mt-0.5 shrink-0 text-[11px]"></i>
            <span>{{ datasourceSelectLockReason }}</span>
          </div>
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
              class="h-9 px-4 rounded-lg border border-slate-200 bg-white text-[13px] text-slate-600 transition-all hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60"
              @click="showContextSelectorDialog = false"
            >
              取消
            </button>
            <button
              class="h-9 px-4 rounded-lg border border-[#1677FF] bg-[#1677FF] text-[13px] text-white transition-all hover:bg-[#3d8bfd] disabled:cursor-not-allowed disabled:opacity-60"
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
    />

    <AgentScheduleManager
      v-model="showAgentScheduleModal"
      :conversation-id="currentConversationId || ''"
      :datasource-id="selectedDatasourceId || ''"
      @open-conversation="handleOpenScheduledConversation"
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

/* 对话/语义模型两个面板：会话行是"标题 + 右缘行内操作"的横向布局，
   右内边距收窄到 8px，避免行内操作与侧栏右缘之间出现一条明显空白带
   （左侧仍由分组缩进线承接视觉留白，所以只收右侧）。 */
.sidebar-unified-shell--conversation {
  padding-right: 8px;
}

.chatdata-message-box-overlay {
  z-index: var(--z-messagebox-legacy-overlay) !important;
}

.chatdata-message-box {
  z-index: var(--z-messagebox-legacy-box) !important;
}

/*
 * 需较宽内容区的 MessageBox 变体（与 chatdata-message-box 叠加使用）：
 * 保存前校验的质量提示消息较长，默认 420px 下换行破碎难读。
 * 不直接改 chatdata-message-box 宽度——那是多处共用的 customClass，会连带加宽无关弹窗。
 */
.chatdata-message-box-wide {
  --el-messagebox-width: 640px;
}

/*
 * 弹窗内质量提示卡片固定占满内容区宽度（不随最长一行文字伸缩）：
 * EP 的 .el-message-box__container 是 flex 行，__message 作为 flex 项默认按内容收缩，
 * 导致 findings 卡片宽度跟内容走。仅在保存前校验弹窗的 -wide 作用域内修复，
 * 不动全局 MessageBox 布局。
 */
.chatdata-message-box-wide .el-message-box__message {
  flex: 1;
}

/*
 * Element Plus MessageBox 全局层级修复（本文件 style 非 scoped，规则随 chat-data 目录走）。
 * 根因：语义配置等整页容器 z-index 高达 2050（var(--z-page-overlay)，见 styles/z-index.css），
 * 而 MessageBox 默认 z-index 从 ~2001 起动态分配，整层弹窗（含遮罩）会被压在整页容器之下，
 * 表现为"点了删除不弹窗"。这里对 MessageBox 遮罩层（.el-overlay.is-message-box，EP 专属类，
 * 不影响 dialog/drawer）统一提到 20010，高于全部业务层级；各调用点无需再逐个传
 * customClass: 'chatdata-message-box'。
 */
.el-overlay.is-message-box {
  z-index: var(--z-messagebox-overlay) !important;
}

</style>
