/**
 * Dashboard 逻辑 Composable
 */
import { ref, computed, watch, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import {
  getDashboard,
  updateDashboard,
  updateLayout,
  addPanel,
  updatePanel,
  deletePanel,
  executePanel,
  executePanelsBatch,
  type Dashboard,
  type Panel,
  type PanelData,
} from '../../../api/dashboard'
import {
  DASHBOARD_BACKGROUNDS,
  THEMES,
  LIGHT_BACKGROUNDS,
  type ThemeType,
  type PanelDataCache,
} from '../constants'

export function useDashboard(
  dashboardId: string,
  getPanelMemberDatasourceId?: (panelId: string) => string | undefined
) {
  // 状态
  const dashboard = ref<Dashboard | null>(null)
  const loading = ref(true)
  const saving = ref(false)
  const panelDataCache = ref<PanelDataCache>({})
  const selectedBgId = ref('light01')
  const autoRefreshEnabled = ref(false)
  const autoRefreshTimers = new Map<string, ReturnType<typeof setInterval>>()
  let batchLoadController: AbortController | null = null

  // 派生状态
  const currentTheme = computed<ThemeType>(() =>
    selectedBgId.value.startsWith('light') ? 'light' : 'dark'
  )
  const theme = computed(() => THEMES[currentTheme.value])

  const currentBackground = computed(() => {
    const bg = DASHBOARD_BACKGROUNDS.find(b => b.id === selectedBgId.value)
    if (bg?.src) {
      return `url(${bg.src}) center/cover no-repeat`
    }
    return LIGHT_BACKGROUNDS[selectedBgId.value] || THEMES.light.pageBg
  })

  const panels = computed(() => dashboard.value?.panels || [])

  const resetPanelDataCache = (panelIds: string[]) => {
    const nextCache: PanelDataCache = {}
    panelIds.forEach((panelId) => {
      nextCache[panelId] = panelDataCache.value[panelId] || { data: null, loading: false, error: null }
    })
    panelDataCache.value = nextCache
  }

  const primePanelLoadingState = (panelIds: string[]) => {
    resetPanelDataCache(panelIds)
    panelIds.forEach((panelId) => {
      panelDataCache.value[panelId] = {
        data: panelDataCache.value[panelId]?.data ?? null,
        loading: true,
        error: null,
      }
    })
  }

  const loadDashboardPanelsInBackground = (panelIds: string[]) => {
    if (panelIds.length === 0) {
      resetPanelDataCache([])
      return
    }
    primePanelLoadingState(panelIds)
    void loadAllPanelData(panelIds, buildPanelMemberDatasourceIds(panelIds)).catch((err) => {
      if (err instanceof DOMException && err.name === 'AbortError') {
        return
      }
      console.error('后台加载看板面板失败:', err)
    })
  }

  // 加载看板数据
  const loadDashboard = async () => {
    if (!dashboardId) return

    try {
      loading.value = true
      const data = await getDashboard(dashboardId)
      dashboard.value = data

      // 设置背景
      if (data.background_id) {
        selectedBgId.value = data.background_id
      }

      const panelIds = (data.panels || []).map(p => p.id)
      loadDashboardPanelsInBackground(panelIds)
    } catch (err) {
      console.error('加载看板失败:', err)
      ElMessage.error('加载看板失败')
    } finally {
      loading.value = false
    }
  }

  // 批量加载面板数据（流式）
  const buildPanelMemberDatasourceIds = (panelIds: string[]) => {
    const panelMemberDatasourceIds: Record<string, string> = {}
    panelIds.forEach((panelId) => {
      const memberDatasourceId = getPanelMemberDatasourceId?.(panelId)
      if (memberDatasourceId) {
        panelMemberDatasourceIds[panelId] = memberDatasourceId
      }
    })
    return panelMemberDatasourceIds
  }

  const loadAllPanelData = async (
    panelIds: string[],
    panelMemberDatasourceIds?: Record<string, string>
  ) => {
    if (!dashboardId || panelIds.length === 0) {
      return { total: 0, succeeded: 0, failed: 0 }
    }

    resetPanelDataCache(panelIds)
    panelIds.forEach(panelId => {
      panelDataCache.value[panelId] = {
        data: panelDataCache.value[panelId]?.data ?? null,
        loading: true,
        error: null,
      }
    })

    batchLoadController?.abort()
    const controller = new AbortController()
    batchLoadController = controller

    try {
      const summary = await executePanelsBatch(dashboardId, {
        panel_ids: panelIds,
        panel_member_datasource_ids: panelMemberDatasourceIds || {},
      }, (panelId, data) => {
        panelDataCache.value[panelId] = {
          data: 'error' in data ? null : (data as PanelData),
          loading: false,
          error: 'error' in data ? (data as any).error : null,
        }
      }, controller.signal)
      return {
        total: summary.total,
        succeeded: summary.succeeded,
        failed: summary.failed,
      }
    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError') {
        return { total: 0, succeeded: 0, failed: 0 }
      }
      console.error('批量加载面板失败:', err)
      // 回退到单个加载
      const results = await Promise.all(
        panelIds.map(id => loadPanelData(id, panelMemberDatasourceIds?.[id]))
      )
      const failed = results.filter(item => item === false).length
      return {
        total: panelIds.length,
        succeeded: panelIds.length - failed,
        failed,
      }
    } finally {
      if (batchLoadController === controller) {
        batchLoadController = null
      }
    }
  }

  // 加载单个面板数据
  const loadPanelData = async (panelId: string, memberDatasourceId?: string) => {
    if (!dashboardId) return false

    panelDataCache.value[panelId] = {
      ...panelDataCache.value[panelId],
      loading: true,
      error: null,
    }

    try {
      const data = await executePanel(dashboardId, panelId, memberDatasourceId)
      panelDataCache.value[panelId] = {
        data,
        loading: false,
        error: null,
      }
      return true
    } catch (err: any) {
      panelDataCache.value[panelId] = {
        data: null,
        loading: false,
        error: err.message || '加载失败',
      }
      return false
    }
  }

  // 保存布局
  const saveLayout = async (layouts: Array<{ panel_id: string; x: number; y: number; w: number; h: number }>) => {
    if (!dashboardId) return false

    try {
      saving.value = true
      await updateLayout(dashboardId, layouts)
      return true
    } catch (err) {
      console.error('保存布局失败:', err)
      ElMessage.error('保存布局失败')
      return false
    } finally {
      saving.value = false
    }
  }

  // 更新看板设置
  const updateDashboardSettings = async (params: {
    name?: string
    theme?: 'light' | 'dark'
    background_id?: string
  }) => {
    if (!dashboardId) return

    try {
      const updated = await updateDashboard(dashboardId, params)
      if (dashboard.value) {
        dashboard.value = { ...dashboard.value, ...updated }
      }
      ElMessage.success('保存成功')
    } catch (err) {
      console.error('更新看板失败:', err)
      ElMessage.error('保存失败')
    }
  }

  // 添加面板
  const handleAddPanel = async (params: {
    name: string
    sql: string
    chart_type?: Panel['chart_type']
    datasource_id?: string
  }) => {
    if (!dashboardId) return

    try {
      const newPanel = await addPanel(dashboardId, {
        ...params,
        layout_x: 0,
        layout_y: Infinity, // 放到最底部
        layout_w: 4,
        layout_h: 4,
      })

      if (dashboard.value) {
        dashboard.value.panels = [...(dashboard.value.panels || []), newPanel]
      }

      // 加载新面板数据
      await loadPanelData(newPanel.id, getPanelMemberDatasourceId?.(newPanel.id))

      ElMessage.success('面板已添加')
      return newPanel
    } catch (err) {
      console.error('添加面板失败:', err)
      ElMessage.error('添加面板失败')
    }
  }

  // 更新面板
  const handleUpdatePanel = async (panelId: string, params: {
    name?: string
    sql?: string
    chart_type?: Panel['chart_type']
    chart_config?: Record<string, unknown>
  }) => {
    if (!dashboardId) return

    try {
      const updated = await updatePanel(dashboardId, panelId, params)

      if (dashboard.value) {
        dashboard.value.panels = dashboard.value.panels?.map(p =>
          p.id === panelId ? updated : p
        )
      }

      // 如果更新了 SQL，重新加载数据
      if (params.sql) {
        await loadPanelData(panelId, getPanelMemberDatasourceId?.(panelId))
      }

      return updated
    } catch (err) {
      console.error('更新面板失败:', err)
      ElMessage.error('更新面板失败')
    }
  }

  // 删除面板
  const handleDeletePanel = async (panelId: string) => {
    if (!dashboardId) return

    try {
      await deletePanel(dashboardId, panelId)

      if (dashboard.value) {
        dashboard.value.panels = dashboard.value.panels?.filter(p => p.id !== panelId)
      }

      // 清除缓存
      delete panelDataCache.value[panelId]
      const timer = autoRefreshTimers.get(panelId)
      if (timer) {
        clearInterval(timer)
        autoRefreshTimers.delete(panelId)
      }

      ElMessage.success('面板已删除')
      return true
    } catch (err) {
      console.error('删除面板失败:', err)
      ElMessage.error('删除面板失败')
      return false
    }
  }

  // 切换背景
  const changeBackground = async (bgId: string) => {
    selectedBgId.value = bgId
    await updateDashboardSettings({ background_id: bgId })
  }

  // 运行全部面板
  const runAllPanels = async () => {
    if (!dashboard.value?.panels?.length) return
    const panelIds = dashboard.value.panels.map(p => p.id)
    const summary = await loadAllPanelData(panelIds, buildPanelMemberDatasourceIds(panelIds))
    if (summary.total === 0) {
      return
    }
    if (summary.failed > 0 && summary.succeeded > 0) {
      ElMessage.warning(`部分面板刷新成功，失败 ${summary.failed} 个`)
      return
    }
    if (summary.failed > 0) {
      ElMessage.error('所有面板刷新失败')
      return
    }
    ElMessage.success('所有面板已刷新')
  }

  // 自动刷新控制
  watch(autoRefreshEnabled, (enabled) => {
    // 清除所有定时器
    autoRefreshTimers.forEach(timer => clearInterval(timer))
    autoRefreshTimers.clear()

    if (!enabled || !dashboard.value?.panels) return

    // 为有刷新间隔的面板设置定时器
    dashboard.value.panels.forEach(panel => {
      const interval = panel.refresh_interval
      if (interval && interval > 0) {
        const timer = setInterval(() => {
          loadPanelData(panel.id, getPanelMemberDatasourceId?.(panel.id))
        }, interval * 1000)
        autoRefreshTimers.set(panel.id, timer)
      }
    })
  })

  // 清理
  onUnmounted(() => {
    batchLoadController?.abort()
    batchLoadController = null
    autoRefreshTimers.forEach(timer => clearInterval(timer))
    autoRefreshTimers.clear()
  })

  return {
    // 状态
    dashboard,
    loading,
    saving,
    panelDataCache,
    selectedBgId,
    autoRefreshEnabled,
    // 计算属性
    currentTheme,
    theme,
    currentBackground,
    panels,
    // 方法
    loadDashboard,
    loadPanelData,
    loadAllPanelData,
    saveLayout,
    updateDashboardSettings,
    handleAddPanel,
    handleUpdatePanel,
    handleDeletePanel,
    changeBackground,
    runAllPanels,
  }
}
