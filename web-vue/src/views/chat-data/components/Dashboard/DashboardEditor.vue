<script setup lang="ts">
/**
 * Dashboard 编辑器
 * 支持拖拽布局、图表渲染、面板管理
 */
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { GridLayout, GridItem } from 'grid-layout-plus'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useDashboard } from './composables/useDashboard'
import PanelCard from './components/PanelCard.vue'
import { DASHBOARD_BACKGROUNDS, GRID_CONFIG, LIGHT_BACKGROUNDS, MONITOR_BACKGROUNDS } from './constants'
import type { DataSource } from '../../types'
import type { Panel } from '../../api/dashboard'

interface Props {
  dashboardId: string
  datasourceId?: string
  datasources?: DataSource[]
  currentUserId?: string
}

const props = defineProps<Props>()

const emit = defineEmits<{
  close: []
}>()

// 使用 composable
const {
  dashboard,
  loading,
  saving,
  panelDataState,
  selectedBgId,
  autoRefreshEnabled,
  currentTheme,
  theme,
  currentBackground,
  panels,
  loadDashboard,
  loadPanelData,
  saveLayout,
  updateDashboardSettings,
  handleAddPanel,
  handleUpdatePanel,
  handleDeletePanel,
  runAllPanels,
} = useDashboard(props.dashboardId)

// 编辑状态
const isPreviewMode = ref(true)
const isEditingName = ref(false)
const editName = ref('')
const showBgSelector = ref(false)
const showAddPanelModal = ref(false)
const newPanelName = ref('')
const newPanelSql = ref('')
const isAddingPanel = ref(false)

// 看板容器引用
const editorRef = ref<HTMLDivElement | null>(null)
const isFullscreen = ref(false)
const showSettingsMenu = ref(false)

// 布局数据
const layoutData = ref<any[]>([])
const isLayoutInitialized = ref(false)
const savedLayoutSignature = ref('')
const savedDashboardSignature = ref('')
const savedPanelConfigSignatures = ref<Record<string, string>>({})

// 初始化布局 - 只执行一次
const initLayout = () => {
  if (isLayoutInitialized.value) return
  if (panels.value && panels.value.length > 0) {
    // 清空并重新填充 reactive 数组
    layoutData.value = []
    panels.value.forEach(panel => {
      layoutData.value.push({
        i: panel.id,
        x: panel.layout?.x ?? 0,
        y: panel.layout?.y ?? 0,
        w: panel.layout?.w ?? 4,
        h: panel.layout?.h ?? 4,
      })
    })
    isLayoutInitialized.value = true
    markAllSaved()
  }
}

const syncLayoutWithPanels = () => {
  const currentPanels = panels.value || []
  const panelMap = new Map(currentPanels.map(panel => [panel.id, panel]))
  const nextLayout = layoutData.value
    .filter(item => panelMap.has(item.i))
    .map(item => ({ ...item }))

  currentPanels.forEach(panel => {
    if (nextLayout.some(item => item.i === panel.id)) return
    nextLayout.push({
      i: panel.id,
      x: panel.layout?.x ?? 0,
      y: panel.layout?.y ?? 0,
      w: panel.layout?.w ?? 4,
      h: panel.layout?.h ?? 4,
    })
  })

  const hasChanged = nextLayout.length !== layoutData.value.length || nextLayout.some((item, index) => {
    const current = layoutData.value[index]
    return !current
      || current.i !== item.i
      || current.x !== item.x
      || current.y !== item.y
      || current.w !== item.w
      || current.h !== item.h
  })
  if (!hasChanged) return
  layoutData.value = nextLayout
}

// 根据 panel id 获取对应的 layout item
const getLayoutItem = (panelId: string) => {
  return layoutData.value.find(item => item.i === panelId)
}

const getBgPreviewStyle = (bg: { src?: string; id: string; theme: string }) => {
  if (bg.src) {
    return { backgroundImage: `url(${bg.src})` }
  }
  if (MONITOR_BACKGROUNDS[bg.id]) {
    return {
      background: MONITOR_BACKGROUNDS[bg.id],
      boxShadow: 'inset 0 0 0 1px rgba(148, 163, 184, 0.45)',
    }
  }
  const background = LIGHT_BACKGROUNDS[bg.id] || (bg.theme === 'dark' ? '#1e293b' : '#f5f7fa')
  return {
    background,
    boxShadow: 'inset 0 0 0 1px rgba(148, 163, 184, 0.45)',
  }
}

const buildLayoutSignature = (items: Array<{ i: string; x: number; y: number; w: number; h: number }>) => {
  const normalized = items
    .map(item => ({ i: item.i, x: item.x, y: item.y, w: item.w, h: item.h }))
    .sort((a, b) => a.i.localeCompare(b.i))
  return JSON.stringify(normalized)
}

const buildDashboardSignature = () => {
  return JSON.stringify({
    name: dashboard.value?.name || '',
    background_id: selectedBgId.value || '',
  })
}

const buildPanelConfigSignature = (panel: Panel) => {
  return JSON.stringify({
    name: panel?.name || '',
    sql: panel?.sql || '',
    chart_type: panel?.chart_type || 'table',
    chart_config: panel?.chart_config || {},
    refresh_interval: panel?.refresh_interval ?? null,
    member_datasource_id: panel?.member_datasource_id || '',
  })
}

const markLayoutSaved = () => {
  savedLayoutSignature.value = buildLayoutSignature(layoutData.value)
}

const markAllSaved = () => {
  markLayoutSaved()
  savedDashboardSignature.value = buildDashboardSignature()
  const next: Record<string, string> = {}
  ;(panels.value || []).forEach((panel: Panel) => {
    next[panel.id] = buildPanelConfigSignature(panel)
  })
  savedPanelConfigSignatures.value = next
}

const getPanelById = (panelId: string) => {
  return (panels.value || []).find(panel => panel.id === panelId)
}

const getDatasourceById = (datasourceId?: string) => {
  if (!datasourceId) return undefined
  return (props.datasources || []).find(item => item.id === datasourceId)
}

const hasUnsavedLayoutChanges = computed(() => {
  if (!savedLayoutSignature.value) return false
  return buildLayoutSignature(layoutData.value) !== savedLayoutSignature.value
})

const hasUnsavedDashboardChanges = computed(() => {
  if (!savedDashboardSignature.value) return false
  return buildDashboardSignature() !== savedDashboardSignature.value
})

const hasUnsavedPanelConfigChanges = computed(() => {
  const map = savedPanelConfigSignatures.value || {}
  const currentPanels = panels.value || []
  if (currentPanels.length !== Object.keys(map).length) return true
  return currentPanels.some((panel: Panel) => map[panel.id] !== buildPanelConfigSignature(panel))
})

const hasUnsavedChanges = computed(() => {
  return hasUnsavedLayoutChanges.value || hasUnsavedDashboardChanges.value || hasUnsavedPanelConfigChanges.value
})
const canEditDashboard = computed(() => {
  if (!dashboard.value) return false
  if (typeof dashboard.value.can_manage === 'boolean') return dashboard.value.can_manage
  return String(dashboard.value.user_id || '').trim() === String(props.currentUserId || '').trim()
})

// 监听 loading 状态，加载完成后初始化布局
watch(loading, (isLoading) => {
  if (!isLoading && !isLayoutInitialized.value) {
    initLayout()
  }
  if (!isLoading && (!savedLayoutSignature.value || !savedDashboardSignature.value)) {
    markAllSaved()
  }
}, { immediate: true })

// 同时监听 panels 变化（用于首次加载）
watch(() => panels.value, (newPanels) => {
  if (!isLayoutInitialized.value && newPanels && newPanels.length > 0) {
    initLayout()
    return
  }
  if (isLayoutInitialized.value) {
    syncLayoutWithPanels()
  }
}, { immediate: true })

// 手动保存布局到后端
const handleSaveLayout = async () => {
  if (!canEditDashboard.value) {
    ElMessage.warning('公共看板仅创建者可编辑')
    return
  }
  let ok = true

  if (hasUnsavedDashboardChanges.value) {
    ok = !!(await updateDashboardSettings({
      name: dashboard.value?.name || '',
      background_id: selectedBgId.value || undefined,
    })) && ok
  }

  if (hasUnsavedPanelConfigChanges.value) {
    const map = savedPanelConfigSignatures.value || {}
    const currentPanels = panels.value || []
    for (const panel of currentPanels) {
      const nextSig = buildPanelConfigSignature(panel)
      if (map[panel.id] === nextSig) continue
      const updated = await handleUpdatePanel(panel.id, {
        name: panel.name,
        sql: panel.sql,
        chart_type: panel.chart_type,
        chart_config: panel.chart_config,
        member_datasource_id: panel.member_datasource_id,
      })
      if (!updated) ok = false
    }
  }

  if (hasUnsavedLayoutChanges.value) {
    const layouts = layoutData.value.map(item => ({
      panel_id: item.i,
      x: item.x,
      y: item.y,
      w: item.w,
      h: item.h,
    }))
    ok = !!(await saveLayout(layouts)) && ok
  }

  if (!ok) return
  markAllSaved()
  ElMessage.success('已保存')
}

// 开始编辑名称
const startEditName = () => {
  if (!canEditDashboard.value) return
  editName.value = dashboard.value?.name || ''
  isEditingName.value = true
}

// 保存名称
const saveName = async () => {
  const trimmed = editName.value.trim()
  if (trimmed && dashboard.value) {
    dashboard.value = {
      ...dashboard.value,
      name: trimmed
    }
  }
  isEditingName.value = false
}

// 删除面板确认
const confirmDeletePanel = async (panelId: string) => {
  if (!canEditDashboard.value) {
    ElMessage.warning('公共看板仅创建者可编辑')
    return
  }
  try {
    await ElMessageBox.confirm('确定要删除该面板吗？', '删除确认', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
    const deleted = await handleDeletePanel(panelId)
    if (!deleted) return
    layoutData.value = layoutData.value.filter(item => item.i !== panelId)
    markAllSaved()
  } catch {
    // 取消
  }
}

// 添加面板
const handleAddNewPanel = async () => {
  if (!canEditDashboard.value) {
    ElMessage.warning('公共看板仅创建者可编辑')
    return
  }
  if (isAddingPanel.value) return

  if (!newPanelName.value.trim() || !newPanelSql.value.trim()) {
    ElMessage.warning('请填写面板名称和SQL')
    return
  }

  isAddingPanel.value = true
  try {
    const newPanel = await handleAddPanel({
      name: newPanelName.value.trim(),
      sql: newPanelSql.value.trim(),
      chart_type: 'table',
      datasource_id: props.datasourceId,
    })
    if (newPanel) {
      syncLayoutWithPanels()
      markAllSaved()
      newPanelName.value = ''
      newPanelSql.value = ''
      showAddPanelModal.value = false
      isPreviewMode.value = false
    }
  } finally {
    isAddingPanel.value = false
  }
}

// 切换图表类型
const handleChangeChartType = async (panelId: string, chartType: string) => {
  if (!canEditDashboard.value) return
  if (!dashboard.value?.panels) return
  dashboard.value = {
    ...dashboard.value,
    panels: dashboard.value.panels.map((p: Panel) => p.id === panelId ? { ...p, chart_type: chartType as Panel['chart_type'] } : p)
  }
}

// 更新图表配置
const handleUpdateChartConfig = async (panelId: string, config: Record<string, unknown>) => {
  if (!canEditDashboard.value) return
  if (!dashboard.value?.panels) return
  dashboard.value = {
    ...dashboard.value,
    panels: dashboard.value.panels.map((p: Panel) => p.id === panelId ? { ...p, chart_config: config } : p)
  }
}

const handleUpdatePanelSql = (panelId: string, sql: string) => {
  if (!canEditDashboard.value) return
  if (!dashboard.value?.panels) return
  dashboard.value = {
    ...dashboard.value,
    panels: dashboard.value.panels.map((p: Panel) => p.id === panelId ? { ...p, sql } : p)
  }
}

const handleUpdatePanelMemberDatasourceId = (panelId: string, memberDatasourceId: string) => {
  if (!canEditDashboard.value) return
  if (!dashboard.value?.panels) return
  dashboard.value = {
    ...dashboard.value,
    panels: dashboard.value.panels.map((p: Panel) => (
      p.id === panelId ? { ...p, member_datasource_id: memberDatasourceId } : p
    ))
  }
}

const handleEditPanelName = (panelId: string, name: string) => {
  if (!canEditDashboard.value) return
  const trimmed = (name || '').trim()
  if (!trimmed || !dashboard.value?.panels) return
  dashboard.value = {
    ...dashboard.value,
    panels: dashboard.value.panels.map((p: Panel) => p.id === panelId ? { ...p, name: trimmed } : p)
  }
}

// 看板全屏切换
const toggleFullscreen = () => {
  if (document.fullscreenElement) {
    document.exitFullscreen()
    isFullscreen.value = false
  } else if (editorRef.value) {
    editorRef.value.requestFullscreen()
    isFullscreen.value = true
  }
}

// 监听全屏变化（ESC退出等情况）
const handleFullscreenChange = () => {
  isFullscreen.value = !!document.fullscreenElement
}

onMounted(() => {
  loadDashboard()
  document.addEventListener('fullscreenchange', handleFullscreenChange)
})

onUnmounted(() => {
  document.removeEventListener('fullscreenchange', handleFullscreenChange)
})

</script>

<template>
  <div
    ref="editorRef"
    class="dashboard-editor flex flex-col h-full overflow-hidden bg-cover bg-center"
    :class="{ 'theme-dark': currentTheme === 'dark' }"
    :style="{ background: currentBackground }"
    @click="showSettingsMenu = false; showBgSelector = false"
  >
    <!-- 顶部工具栏 -->
    <div
      class="dashboard-header flex items-center justify-between px-5 py-3 border-b"
      :style="{
        background: theme.panelHeaderBg,
        borderColor: theme.panelBorder,
        '--dashboard-header-accent': theme.accentColor,
        '--dashboard-header-border': theme.panelBorder,
        '--dashboard-header-glow': `${theme.accentColor}33`,
        '--dashboard-header-strong-glow': `${theme.accentColor}66`,
        '--dashboard-header-surface': theme.panelHeaderBg,
      }"
    >
      <div class="relative z-[1] w-6 shrink-0"></div>

      <div class="dashboard-title-wrap flex items-center gap-3">
        <!-- 看板名称 -->
        <div v-if="!isEditingName" class="flex items-center gap-2 group" :class="canEditDashboard ? 'cursor-pointer' : 'cursor-default'" @click="!isFullscreen && !isPreviewMode && startEditName()">
          <h2
            class="dashboard-title-breathe m-0 text-lg font-semibold"
            :style="{ color: theme.titleColor, '--dashboard-title-glow': `${theme.titleColor}66` }"
          >
            {{ dashboard?.name || '加载中...' }}
          </h2>
          <span v-if="dashboard?.is_public" class="rounded border border-cyan-400/30 bg-cyan-400/10 px-1.5 py-0.5 text-[11px]" :style="{ color: theme.titleColor }">公共</span>
        </div>
        <div v-else>
          <input
            v-model="editName"
            type="text"
            class="px-3 py-1.5 border rounded-md bg-transparent text-lg font-semibold outline-none"
            :style="{ color: theme.textColor, borderColor: theme.accentColor }"
            @keyup.enter="saveName"
            @blur="saveName"
            autofocus
          />
        </div>
      </div>

      <div class="relative z-[3] flex items-center gap-3">
        <!-- 保存布局（编辑模式 & 非全屏） -->
        <button
          v-if="canEditDashboard && !isPreviewMode && !isFullscreen"
          class="flex items-center gap-1.5 px-3.5 py-2 border-none rounded-md text-[13px] cursor-pointer transition-all duration-200 hover:brightness-110 bg-gradient-to-br from-[#1677FF] to-[#0958d9] text-white disabled:opacity-60 disabled:cursor-not-allowed"
          @click="handleSaveLayout"
          :disabled="saving || !hasUnsavedChanges"
          title="保存当前页面所有改动"
        >
          <i :class="saving ? 'fa-solid fa-spinner fa-spin' : 'fa-solid fa-save'"></i>
          <span>{{ saving ? '保存中...' : '保存' }}</span>
        </button>

        <!-- 设置下拉菜单（非全屏时显示） -->
        <div v-if="!isFullscreen" class="relative">
          <button
            class="flex items-center gap-1.5 px-3.5 py-2 border-none rounded-md text-[13px] cursor-pointer transition-all duration-200 hover:brightness-110"
            @click.stop="showSettingsMenu = !showSettingsMenu"
            :style="{ color: theme.buttonText, background: theme.buttonBg }"
          >
            <i class="fa-solid fa-cog"></i>
          </button>
          <div
            v-if="showSettingsMenu"
            class="absolute top-full right-0 mt-2 p-2 border rounded-lg min-w-[160px] z-[320] shadow-[0_8px_24px_rgba(0,0,0,0.2)]"
            :style="{ background: theme.cardBg, borderColor: theme.panelBorder }"
            @click.stop
          >
            <!-- 新建面板（自动进入编辑态） -->
            <button
              v-if="canEditDashboard"
              class="flex items-center gap-2.5 w-full px-3 py-2.5 border-none bg-transparent rounded-md text-[13px] cursor-pointer text-left transition-all duration-200 hover:bg-white/10"
              @click="showAddPanelModal = true; showSettingsMenu = false"
              :style="{ color: theme.textColor }"
            >
              <i class="fa-solid fa-plus w-4 text-center opacity-80"></i>
              <span>新建面板</span>
            </button>

            <!-- 编辑 -->
            <button
              v-if="canEditDashboard && isPreviewMode"
              class="flex items-center gap-2.5 w-full px-3 py-2.5 border-none bg-transparent rounded-md text-[13px] cursor-pointer text-left transition-all duration-200 hover:bg-white/10"
              @click="isPreviewMode = false; showSettingsMenu = false"
              :style="{ color: theme.textColor }"
            >
              <i class="fa-solid fa-pen-to-square w-4 text-center opacity-80"></i>
              <span>编辑</span>
            </button>

            <!-- 预览 -->
            <button
              v-else-if="canEditDashboard"
              class="flex items-center gap-2.5 w-full px-3 py-2.5 border-none bg-transparent rounded-md text-[13px] cursor-pointer text-left transition-all duration-200 hover:bg-white/10"
              @click="isPreviewMode = true; showSettingsMenu = false"
              :style="{ color: theme.textColor }"
            >
              <i class="fa-solid fa-eye w-4 text-center opacity-80"></i>
              <span>预览</span>
            </button>

            <!-- 背景选择 -->
            <button
              v-if="canEditDashboard"
              class="flex items-center gap-2.5 w-full px-3 py-2.5 border-none bg-transparent rounded-md text-[13px] cursor-pointer text-left transition-all duration-200 hover:bg-white/10"
              @click="showBgSelector = !showBgSelector; showSettingsMenu = false"
              :style="{ color: theme.textColor }"
            >
              <i class="fa-solid fa-image w-4 text-center opacity-80"></i>
              <span>更换背景</span>
            </button>
            <!-- 自动刷新 -->
            <label class="flex items-center gap-2.5 w-full px-3 py-2.5 border-none bg-transparent rounded-md text-[13px] cursor-pointer text-left transition-all duration-200 hover:bg-white/10 justify-start" :style="{ color: theme.textColor }">
              <i class="fa-solid fa-sync-alt w-4 text-center opacity-80"></i>
              <span>自动刷新</span>
              <input type="checkbox" v-model="autoRefreshEnabled" class="ml-auto cursor-pointer" />
            </label>
          </div>
        </div>

        <!-- 全屏切换 -->
        <button
          class="flex items-center gap-1.5 px-3.5 py-2 border-none rounded-md text-[13px] cursor-pointer transition-all duration-200 hover:brightness-110"
          @click="toggleFullscreen"
          :style="{ color: theme.buttonText, background: theme.buttonBg }"
          :title="isFullscreen ? '退出全屏' : '全屏'"
        >
          <i :class="isFullscreen ? 'fa-solid fa-compress' : 'fa-solid fa-expand'"></i>
        </button>
      </div>
    </div>

    <!-- 背景选择器 -->
    <div v-if="canEditDashboard && showBgSelector" class="absolute top-[60px] right-5 p-4 border rounded-xl z-[310] shadow-[0_8px_32px_rgba(0,0,0,0.3)]" :style="{ background: theme.cardBg, borderColor: theme.panelBorder }" @click.stop>
      <div class="text-sm font-medium mb-3" :style="{ color: theme.textColor }">选择背景</div>
      <div class="grid grid-cols-5 gap-2">
        <div
          v-for="bg in DASHBOARD_BACKGROUNDS"
          :key="bg.id"
          class="cursor-pointer text-center group"
          @click="selectedBgId = bg.id; showBgSelector = false"
        >
          <div
            class="w-[60px] h-10 rounded-md bg-cover bg-center mb-1 border-2 transition-all duration-200"
            :class="selectedBgId === bg.id ? 'border-[#1677FF]' : 'border-transparent group-hover:border-[#1677FF]'"
            :style="getBgPreviewStyle(bg)"
          ></div>
          <span class="text-[10px] text-gray-400">{{ bg.name }}</span>
        </div>
      </div>
    </div>

    <!-- 加载状态 -->
    <div v-if="loading" class="flex-1 flex flex-col items-center justify-center gap-3 text-gray-400">
      <i class="fa-solid fa-spinner fa-spin text-[32px]"></i>
      <span>加载中...</span>
    </div>

    <!-- 面板网格 -->
    <div v-else class="relative z-0 flex-1 p-4 overflow-auto">
      <GridLayout
        v-model:layout="layoutData"
        :col-num="GRID_CONFIG.COLS"
        :row-height="GRID_CONFIG.ROW_HEIGHT"
        :is-draggable="canEditDashboard && !isPreviewMode"
        :is-resizable="canEditDashboard && !isPreviewMode"
        :vertical-compact="true"
        :margin="[12, 12]"
        :use-css-transforms="true"
      >
        <GridItem
          v-for="item in layoutData"
          :key="item.i"
          :i="item.i"
          :x="item.x"
          :y="item.y"
          :w="item.w"
          :h="item.h"
          :min-w="2"
          :min-h="2"
          :resize-option="{ edges: { top: false, right: true, bottom: true, left: true } }"
          drag-allow-from=".drag-handle"
        >
          <PanelCard
            :panel="getPanelById(item.i)!"
            :data="panelDataState[item.i]?.data ?? null"
            :loading="panelDataState[item.i]?.loading ?? false"
            :error="panelDataState[item.i]?.error ?? null"
            :theme="theme"
            :is-preview-mode="isPreviewMode || !canEditDashboard"
            :datasources="props.datasources"
            @refresh="loadPanelData(item.i)"
            @delete="confirmDeletePanel(item.i)"
            @edit-name="(name) => handleEditPanelName(item.i, name)"
            @update-sql="(sql) => handleUpdatePanelSql(item.i, sql)"
            @change-chart-type="(type) => handleChangeChartType(item.i, type)"
            @update-chart-config="(config) => handleUpdateChartConfig(item.i, config)"
            @update-member-datasource-id="(memberId) => handleUpdatePanelMemberDatasourceId(item.i, memberId)"
          />
        </GridItem>
      </GridLayout>

      <!-- 空状态 -->
      <div v-if="layoutData.length === 0" class="flex flex-col items-center justify-center gap-4 min-h-[400px] mx-5 my-5 border-2 border-dashed rounded-xl" :style="{ borderColor: theme.emptyBorder, background: theme.emptyBg }">
        <i class="fa-solid fa-chart-pie text-5xl opacity-50" :style="{ color: theme.textMuted }"></i>
        <p :style="{ color: theme.textMuted }">暂无面板，点击"添加面板"开始创建</p>
        <button
          v-if="canEditDashboard"
          class="flex items-center gap-2 px-5 py-2.5 border-none rounded-lg text-sm text-white cursor-pointer transition-all duration-200 hover:brightness-110"
          @click="showAddPanelModal = true"
          :style="{ background: theme.accentColor }"
        >
          <i class="fa-solid fa-plus"></i>
          <span>添加面板</span>
        </button>
      </div>
    </div>

    <!-- 添加面板弹窗 -->
    <el-dialog v-model="showAddPanelModal" title="添加面板" width="500px" append-to-body>
      <div class="mb-4">
        <label class="block text-[13px] mb-1.5 text-gray-500">面板名称</label>
        <el-input v-model="newPanelName" placeholder="请输入面板名称" />
      </div>
      <div class="mb-4">
        <label class="block text-[13px] mb-1.5 text-gray-500">SQL 查询</label>
        <el-input
          v-model="newPanelSql"
          type="textarea"
          :rows="6"
          placeholder="请输入 SQL 查询语句"
          style="font-family: monospace"
        />
      </div>
      <template #footer>
        <el-button :disabled="isAddingPanel" @click="showAddPanelModal = false">取消</el-button>
        <el-button :loading="isAddingPanel" :disabled="isAddingPanel" type="primary" @click="handleAddNewPanel">添加</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
/* 看板全屏状态 - 需要保留在 scoped 中 */
.dashboard-editor:fullscreen {
  width: 100vw;
  height: 100vh;
  min-height: 100vh;
}

.dashboard-header {
  position: relative;
  z-index: 300;
  overflow: visible;
  min-height: 64px;
  border-bottom-width: 1px;
  backdrop-filter: blur(16px) saturate(150%);
  -webkit-backdrop-filter: blur(16px) saturate(150%);
  box-shadow:
    inset 0 -1px 0 color-mix(in srgb, var(--dashboard-header-border) 78%, transparent),
    inset 0 1px 0 color-mix(in srgb, var(--dashboard-header-accent) 18%, transparent),
    0 10px 30px rgba(2, 6, 23, 0.22),
    0 0 28px color-mix(in srgb, var(--dashboard-header-glow) 64%, transparent);
}

.dashboard-header::before {
  content: "";
  position: absolute;
  inset: 0;
  pointer-events: none;
  background:
    linear-gradient(180deg, color-mix(in srgb, var(--dashboard-header-accent) 14%, transparent) 0%, transparent 34%),
    linear-gradient(90deg, transparent 0%, color-mix(in srgb, var(--dashboard-header-accent) 20%, transparent) 18%, color-mix(in srgb, var(--dashboard-header-strong-glow) 55%, transparent) 50%, color-mix(in srgb, var(--dashboard-header-accent) 20%, transparent) 82%, transparent 100%);
  opacity: 0.92;
  z-index: 0;
}

.dashboard-header::after {
  content: "";
  position: absolute;
  inset: -12% -8%;
  pointer-events: none;
  background:
    linear-gradient(108deg, transparent 0%, transparent 34%, color-mix(in srgb, var(--dashboard-header-strong-glow) 30%, transparent) 48%, color-mix(in srgb, #ffffff 12%, transparent) 50%, color-mix(in srgb, var(--dashboard-header-strong-glow) 34%, transparent) 52%, transparent 66%, transparent 100%),
    repeating-linear-gradient(90deg, transparent 0 22px, color-mix(in srgb, var(--dashboard-header-accent) 7%, transparent) 22px 23px, transparent 23px 46px);
  mix-blend-mode: screen;
  transform: translateX(-62%);
  animation: dashboard-header-scan 7.2s linear infinite;
  opacity: 0.9;
  z-index: 0;
}

.dashboard-header > * {
  position: relative;
  z-index: 1;
}

.dashboard-title-wrap {
  position: relative;
  z-index: 1;
  min-width: 420px;
  justify-content: center;
  gap: 18px;
}

.dashboard-title-wrap::before,
.dashboard-title-wrap::after {
  content: "";
  width: 132px;
  height: 2px;
  border-radius: 999px;
  background:
    linear-gradient(90deg, transparent 0%, color-mix(in srgb, var(--dashboard-header-accent) 54%, transparent) 28%, color-mix(in srgb, var(--dashboard-header-strong-glow) 92%, transparent) 100%);
  box-shadow:
    0 0 10px color-mix(in srgb, var(--dashboard-header-glow) 85%, transparent),
    0 0 24px color-mix(in srgb, var(--dashboard-header-glow) 55%, transparent);
  opacity: 0.96;
}

.dashboard-title-wrap::after {
  background:
    linear-gradient(90deg, color-mix(in srgb, var(--dashboard-header-strong-glow) 92%, transparent) 0%, color-mix(in srgb, var(--dashboard-header-accent) 54%, transparent) 72%, transparent 100%);
}

.dashboard-title-breathe {
  position: relative;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  animation: dashboard-title-breathe 4.2s ease-in-out infinite;
  filter: drop-shadow(0 0 8px color-mix(in srgb, var(--dashboard-title-glow) 58%, transparent));
}

.dashboard-title-breathe::before {
  content: "";
  position: absolute;
  left: -16px;
  top: 50%;
  width: 8px;
  height: 8px;
  border-radius: 999px;
  transform: translateY(-50%);
  background: var(--dashboard-header-accent);
  box-shadow:
    0 0 10px var(--dashboard-title-glow),
    0 0 22px color-mix(in srgb, var(--dashboard-title-glow) 70%, transparent);
}

.dashboard-title-breathe::after {
  content: "";
  position: absolute;
  left: 50%;
  bottom: -14px;
  width: 118px;
  height: 3px;
  border-radius: 999px;
  transform: translateX(-50%);
  background:
    linear-gradient(90deg, transparent 0%, color-mix(in srgb, var(--dashboard-header-accent) 88%, transparent) 26%, color-mix(in srgb, #ffffff 28%, transparent) 50%, color-mix(in srgb, var(--dashboard-header-accent) 88%, transparent) 74%, transparent 100%);
  box-shadow:
    0 0 12px color-mix(in srgb, var(--dashboard-title-glow) 82%, transparent),
    0 0 28px color-mix(in srgb, var(--dashboard-title-glow) 50%, transparent);
  animation: dashboard-title-line-pulse 3.4s ease-in-out infinite;
}

@keyframes dashboard-header-scan {
  0% {
    transform: translateX(-62%);
    opacity: 0;
  }
  10% {
    opacity: 0.42;
  }
  50% {
    opacity: 0.92;
  }
  88% {
    opacity: 0.38;
  }
  100% {
    transform: translateX(62%);
    opacity: 0;
  }
}

@keyframes dashboard-title-breathe {
  0%,
  100% {
    text-shadow:
      0 0 10px color-mix(in srgb, var(--dashboard-title-glow) 42%, transparent),
      0 0 20px color-mix(in srgb, var(--dashboard-title-glow) 22%, transparent);
    filter: brightness(1) drop-shadow(0 0 8px color-mix(in srgb, var(--dashboard-title-glow) 50%, transparent));
  }
  50% {
    text-shadow:
      0 0 12px color-mix(in srgb, var(--dashboard-title-glow) 78%, transparent),
      0 0 26px color-mix(in srgb, var(--dashboard-title-glow) 58%, transparent),
      0 0 42px color-mix(in srgb, var(--dashboard-title-glow) 28%, transparent);
    filter: brightness(1.14) drop-shadow(0 0 14px color-mix(in srgb, var(--dashboard-title-glow) 74%, transparent));
  }
}

@keyframes dashboard-title-line-pulse {
  0%,
  100% {
    opacity: 0.7;
    transform: translateX(-50%) scaleX(0.92);
  }
  50% {
    opacity: 1;
    transform: translateX(-50%) scaleX(1.08);
  }
}

@media (prefers-reduced-motion: reduce) {
  .dashboard-title-breathe,
  .dashboard-title-breathe::after,
  .dashboard-header::after {
    animation: none;
  }
}
</style>

<style>
/* grid-layout-plus 全局样式覆盖 */
.vgl-layout {
  background: transparent;
}

.vgl-item {
  touch-action: none;
  overflow: visible !important;
}

.vgl-item:hover {
  z-index: 80;
}

/* 只在非拖动/非调整大小时应用 transition */
.vgl-item:not(.vgl-item--dragging):not(.vgl-item--resizing) {
  transition: transform 0.2s ease;
}

/* 拖动/调整大小时禁用 transition，避免抖动 */
.vgl-item--dragging,
.vgl-item--resizing {
  transition: none !important;
  z-index: 100;
}

.vgl-item--placeholder {
  background: rgba(22, 119, 255, 0.2) !important;
  border: 2px dashed #1677FF !important;
  border-radius: 12px;
  transition: none !important;
}

.vgl-item--resizable {
  touch-action: none;
}
</style>
