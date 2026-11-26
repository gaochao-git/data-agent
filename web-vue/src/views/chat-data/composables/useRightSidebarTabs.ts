/**
 * 右侧栏 tab 状态唯一所有者：tab 列表、激活项、复用/关闭、每 tab 滚动位置。
 *
 * 设计约定（对齐 ZCode/DSH 的 sidebar tab 模型）：
 * - 首页常驻不可关，进入文件管理/子智能体管理后仍可一键回到首页；
 * - tab 有稳定 id：`home`、`workspace`、`file:<path>`、`tool:<tool_call_id>`、
 *   `agent:<tool_call_id>`、`subagent-manager`、`subagent-task:<task_id>`；
 * - 同 id 再次打开 = 激活已有 tab，并刷新元数据（例如子会话切到最新一轮）；
 * - 滚动位置按 tab id 记忆，由调用方在切换前/后读写（宿主组件持有真实滚动容器）。
 */
import { computed, ref, watch } from 'vue'

export type RightSidebarTabKind =
  | 'home'
  | 'workspace'
  | 'file'
  | 'tool'
  | 'agentProcess'
  | 'subagentManager'
  | 'subagentTask'

export interface RightSidebarTab {
  id: string
  kind: RightSidebarTabKind
  title: string
  path?: string
  toolCallId?: string
  taskId?: string
}

export const HOME_TAB_ID = 'home'
export const WORKSPACE_TAB_ID = 'workspace'
export const SUBAGENT_MANAGER_TAB_ID = 'subagent-manager'

const HOME_TAB: RightSidebarTab = { id: HOME_TAB_ID, kind: 'home', title: '工作台' }
const WORKSPACE_TAB: RightSidebarTab = { id: WORKSPACE_TAB_ID, kind: 'workspace', title: '文件管理' }
const SUBAGENT_MANAGER_TAB: RightSidebarTab = {
  id: SUBAGENT_MANAGER_TAB_ID,
  kind: 'subagentManager',
  title: '子智能体管理',
}

export const useRightSidebarTabs = () => {
  const tabs = ref<RightSidebarTab[]>([{ ...HOME_TAB }])
  const activeTabId = ref(HOME_TAB_ID)
  const scrollTops = new Map<string, number>()

  const activeTab = computed(() => tabs.value.find(tab => tab.id === activeTabId.value) ?? tabs.value[0]!)
  const isHomeTabActive = computed(() => activeTab.value.kind === 'home')
  const isWorkspaceTabActive = computed(() => activeTab.value.kind === 'workspace' || activeTab.value.kind === 'file')
  const isSubagentManagerTabActive = computed(() => activeTab.value.kind === 'subagentManager')
  const isToolTabActive = computed(() => (
    activeTab.value.kind === 'tool'
    || activeTab.value.kind === 'agentProcess'
    || activeTab.value.kind === 'subagentTask'
  ))

  // 工具/子智能体内容用「最后一次激活的工具 tab」渲染：切到文件 tab 时保持挂载
  const lastToolTab = ref<RightSidebarTab | null>(null)
  watch(isToolTabActive, (active) => {
    if (active) lastToolTab.value = activeTab.value
  }, { immediate: true })
  const displayToolTab = computed(() => (isToolTabActive.value ? activeTab.value : lastToolTab.value))

  /** 激活一个已存在的 tab（不处理滚动，宿主负责）。 */
  const selectTab = (tabId: string) => { activeTabId.value = tabId }

  const ensureTab = (tab: RightSidebarTab): RightSidebarTab => {
    const existing = tabs.value.find(item => item.id === tab.id)
    if (existing) {
      Object.assign(existing, tab)
      return existing
    }
    tabs.value.push({ ...tab })
    return tabs.value[tabs.value.length - 1]!
  }

  /** 打开/激活：同 id 复用页签，同时把详情定位刷新到本次打开的轮次。 */
  const activateTab = (tab: RightSidebarTab) => {
    ensureTab(tab)
    activeTabId.value = tab.id
  }

  const activateHome = () => selectTab(HOME_TAB_ID)

  const activateWorkspace = () => {
    ensureTab(WORKSPACE_TAB)
    selectTab(WORKSPACE_TAB_ID)
  }

  const activateSubagentManager = () => {
    ensureTab(SUBAGENT_MANAGER_TAB)
    selectTab(SUBAGENT_MANAGER_TAB_ID)
  }

  /** 关闭 tab：首页常驻；关闭当前项后返回相邻 tab（由调用方决定是否恢复滚动）。 */
  const closeTab = (tabId: string): string | null => {
    const index = tabs.value.findIndex(tab => tab.id === tabId)
    if (index < 0) return null
    if (tabs.value[index]!.kind === 'home') return null
    tabs.value.splice(index, 1)
    scrollTops.delete(tabId)
    if (activeTabId.value !== tabId) return null
    const fallback = tabs.value[Math.max(0, index - 1)] ?? tabs.value[0]!
    activeTabId.value = fallback.id
    return fallback.id
  }

  const rememberScroll = (tabId: string, top: number) => { scrollTops.set(tabId, top) }
  const scrollTopFor = (tabId: string) => scrollTops.get(tabId)
  const clearScrollMemory = () => { scrollTops.clear() }

  /** 会话切换：tab/记忆全部回到首页。 */
  const resetTabs = () => {
    tabs.value = [{ ...HOME_TAB }]
    activeTabId.value = HOME_TAB_ID
    lastToolTab.value = null
    scrollTops.clear()
  }

  return {
    tabs,
    activeTabId,
    activeTab,
    isHomeTabActive,
    isWorkspaceTabActive,
    isSubagentManagerTabActive,
    isToolTabActive,
    lastToolTab,
    displayToolTab,
    selectTab,
    activateTab,
    activateHome,
    activateWorkspace,
    activateSubagentManager,
    closeTab,
    rememberScroll,
    scrollTopFor,
    clearScrollMemory,
    resetTabs,
  }
}
