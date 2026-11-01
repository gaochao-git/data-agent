/**
 * 右侧栏 tab 状态唯一所有者：tab 列表、激活项、复用/关闭、每 tab 滚动位置。
 *
 * 设计约定（对齐 ZCode/DSH 的 sidebar tab 模型）：
 * - tab 有稳定 id：`workspace`、`file:<path>`、`tool:<tool_call_id>`、`agent:<tool_call_id>`；
 * - 同 id 再次打开 = 激活已有 tab，不替换其他内容；
 * - `workspace` 常驻不可关；
 * - 滚动位置按 tab id 记忆，由调用方在切换前/后读写（宿主组件持有真实滚动容器）。
 */
import { computed, ref, watch } from 'vue'

export type RightSidebarTabKind = 'workspace' | 'file' | 'tool' | 'agentProcess'

export interface RightSidebarTab {
  id: string
  kind: RightSidebarTabKind
  title: string
  path?: string
  toolCallId?: string
}

export const WORKSPACE_TAB_ID = 'workspace'
const WORKSPACE_TAB: RightSidebarTab = { id: WORKSPACE_TAB_ID, kind: 'workspace', title: '文件管理' }

export const useRightSidebarTabs = () => {
  const tabs = ref<RightSidebarTab[]>([{ ...WORKSPACE_TAB }])
  const activeTabId = ref(WORKSPACE_TAB_ID)
  const scrollTops = new Map<string, number>()

  const activeTab = computed(() => tabs.value.find(tab => tab.id === activeTabId.value) ?? tabs.value[0]!)
  const isWorkspaceTabActive = computed(() => activeTab.value.kind === 'workspace' || activeTab.value.kind === 'file')
  const isToolTabActive = computed(() => activeTab.value.kind === 'tool' || activeTab.value.kind === 'agentProcess')

  // 工具/子智能体内容用「最后一次激活的工具 tab」渲染：切到文件 tab 时保持挂载
  const lastToolTab = ref<RightSidebarTab | null>(null)
  watch(isToolTabActive, (active) => {
    if (active) lastToolTab.value = activeTab.value
  }, { immediate: true })
  const displayToolTab = computed(() => (isToolTabActive.value ? activeTab.value : lastToolTab.value))

  /** 激活一个已存在的 tab（不处理滚动，宿主负责）。 */
  const selectTab = (tabId: string) => { activeTabId.value = tabId }

  /** 打开/激活：同 id 只激活，不替换其他 tab。 */
  const activateTab = (tab: RightSidebarTab) => {
    if (!tabs.value.some(item => item.id === tab.id)) tabs.value.push(tab)
    activeTabId.value = tab.id
  }

  const activateWorkspace = () => selectTab(WORKSPACE_TAB_ID)

  /** 关闭 tab：工作区常驻；关闭当前项后返回相邻 tab（由调用方决定是否恢复滚动）。 */
  const closeTab = (tabId: string): string | null => {
    const index = tabs.value.findIndex(tab => tab.id === tabId)
    if (index < 0) return null
    if (tabs.value[index]!.kind === 'workspace') return null
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

  /** 会话切换：tab/记忆全部回到初始态。 */
  const resetTabs = () => {
    tabs.value = [{ ...WORKSPACE_TAB }]
    activeTabId.value = WORKSPACE_TAB_ID
    lastToolTab.value = null
    scrollTops.clear()
  }

  return {
    tabs,
    activeTabId,
    activeTab,
    isWorkspaceTabActive,
    isToolTabActive,
    lastToolTab,
    displayToolTab,
    selectTab,
    activateTab,
    activateWorkspace,
    closeTab,
    rememberScroll,
    scrollTopFor,
    clearScrollMemory,
    resetTabs,
  }
}
