<script setup lang="ts">
/**
 * 面板卡片组件
 * 负责渲染图表内容
 */
import { ref, reactive, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import * as echarts from 'echarts'
import { CHART_COLORS } from '../../../utils/chartOptionBuilder'
import { Codemirror } from 'vue-codemirror'
import { sql } from '@codemirror/lang-sql'
import { EditorView } from '@codemirror/view'
import { ElMessage } from 'element-plus'
import type { Panel, PanelData } from '../../../api/dashboard'
import type { DataSource, ExecutionTargetOption } from '../../../types'
import { CHART_TYPE_HELP_TEXT } from '../../../chartTypes'
import type { ThemeConfig } from '../constants'
import { CHART_TYPES, KPI_ICONS, KPI_COLORS, CHART_COLOR_OPTIONS, LINE_STYLES } from '../constants'
import { flattenQueryResultForDisplay, getSafeNumericColumns, isNumericValue, QUERY_MEMBER_ID_COLUMN, QUERY_MEMBER_NAME_COLUMN, toSafeNumber } from '../../../utils'
import { registerChinaMap, normalizeProvinceName, isChinaMapRegistered } from '../../../utils/mapRegistry'
import ExecutionTargetPicker from '../../ExecutionTargetPicker.vue'

// SQL 编辑器主题（浅色）
const sqlReadonlyTheme = EditorView.theme({
  '&': {
    backgroundColor: '#ffffff',
    color: '#1f2937',
    fontSize: '14px',
    height: '100%',
  },
  '&.cm-editor': {
    height: '100%',
    outline: 'none',
  },
  '&.cm-editor.cm-focused': {
    outline: 'none',
  },
  '.cm-scroller': {
    fontFamily: '"SF Mono", SFMono-Regular, ui-monospace, Menlo, Monaco, Consolas, monospace',
    lineHeight: '1.6',
    overflow: 'auto',
    height: '100%',
  },
  '.cm-content': {
    padding: '8px 0',
    caretColor: '#1677FF',
  },
  '.cm-gutters': {
    backgroundColor: '#f9fafb',
    color: '#9ca3af',
    border: 'none',
    borderRight: '1px solid #e5e7eb',
  },
  '.cm-lineNumbers .cm-gutterElement': {
    padding: '0 8px 0 12px',
    minWidth: '32px',
    fontSize: '12px',
  },
  '.cm-activeLine': {
    backgroundColor: 'transparent',
  },
  '.cm-activeLineGutter': {
    backgroundColor: 'transparent',
  },
  '.cm-foldGutter': {
    display: 'none !important',
  },
  // SQL 语法高亮
  '.cm-keyword': {
    color: '#7c3aed',
    fontWeight: '600',
  },
  '.cm-string': {
    color: '#059669',
  },
  '.cm-number': {
    color: '#d97706',
  },
  '.cm-comment': {
    color: '#9ca3af',
    fontStyle: 'italic',
  },
  '.cm-operator': {
    color: '#0891b2',
  },
  '.cm-punctuation': {
    color: '#6b7280',
  },
  '.cm-variableName, .cm-propertyName': {
    color: '#dc2626',
  },
})

// CodeMirror 扩展
const sqlExtensions = [
  sql(),
  sqlReadonlyTheme,
]

interface Props {
  panel: Panel
  data: PanelData | null
  loading: boolean
  error: string | null
  theme: ThemeConfig
  isPreviewMode: boolean
  datasources?: DataSource[]
}

const props = defineProps<Props>()

const currentDatasource = computed(() => {
  if (!props.panel.datasource_id || !props.datasources) return null
  return props.datasources.find(d => d.id === props.panel.datasource_id) || null
})

// 数据源名称
const datasourceName = computed(() => {
  return currentDatasource.value?.group_name || props.panel.datasource_id || ''
})

const displayError = computed(() => {
  const text = String(props.error || '').replace(/\\n/g, ' ').replace(/\s+/g, ' ').trim()
  if (!text) return ''
  return text.length > 160 ? `${text.slice(0, 160).trim()}...` : text
})

// 去除 SQL 公共缩进
const formattedSql = computed(() => {
  const sql = props.panel.sql || ''
  const lines = sql.split('\n')

  // 找出非空行的最小缩进
  const nonEmptyLines = lines.filter(line => line.trim().length > 0)
  if (nonEmptyLines.length === 0) return sql

  const minIndent = Math.min(
    ...nonEmptyLines.map(line => {
      const match = line.match(/^(\s*)/)
      return match?.[1]?.length ?? 0
    })
  )

  // 去除公共缩进
  if (minIndent > 0) {
    return lines.map(line => line.slice(minIndent)).join('\n')
  }
  return sql
})

const emit = defineEmits<{
  refresh: []
  delete: []
  editName: [name: string]
  updateSql: [sql: string]
  changeChartType: [type: string]
  updateChartConfig: [config: Record<string, unknown>]
  updateMemberDatasourceId: [memberDatasourceId: string]
}>()

// 编辑弹窗状态
const showEditModal = ref(false)
const editModalTab = ref<'basic' | 'chart'>('basic')
const editState = reactive({
  name: '',
  sql: '',
  chartType: props.panel.chart_type as Panel['chart_type'],
  chartConfig: {} as Record<string, unknown>,
  memberDatasourceIds: [] as string[],
})

const executionTargets = computed<ExecutionTargetOption[]>(() => {
  const datasource = currentDatasource.value
  if (!datasource) {
    return props.panel.member_datasource_id
      ? [{ member_datasource_id: props.panel.member_datasource_id, label: props.panel.member_datasource_id }]
      : []
  }

  const members = datasource.member_sources || []
  if (members.length) {
    return members
      .filter(member => member.member_datasource_id)
      .map(member => ({
        member_datasource_id: String(member.member_datasource_id),
        org_name: member.org_name || '',
        org_code: member.org_code || '',
        source_type: member.source_type || datasource.source_type || '',
        host: member.host,
        port: member.port,
        database: member.database,
        label: member.org_name || `${member.host}:${member.port}/${member.database}`,
      }))
  }

  const fallbackId = datasource.template_datasource_id || props.panel.member_datasource_id || datasource.id
  if (!fallbackId) return []
  return [{
    member_datasource_id: fallbackId,
    label: datasource.group_name || fallbackId,
    source_type: datasource.source_type || '',
  }]
})

const allExecutionTargetIds = computed<string[]>(() => executionTargets.value
  .map(item => String(item.member_datasource_id || '').trim())
  .filter(Boolean)
)
const isAllEditTargetsSelected = computed(() => (
  allExecutionTargetIds.value.length > 0 && allExecutionTargetIds.value.every(id => editState.memberDatasourceIds.includes(id))
))

const normalizeMemberIds = (ids: unknown): string[] => {
  const result: string[] = []
  if (!Array.isArray(ids)) return result
  ids.forEach(item => {
    const memberId = String(item || '').trim()
    if (memberId && !result.includes(memberId)) {
      result.push(memberId)
    }
  })
  return result
}

const filterAvailableMemberIds = (ids: string[]) => {
  const availableIds = allExecutionTargetIds.value
  if (!availableIds.length) return ids
  return ids.filter(id => availableIds.includes(id))
}

const getPanelSelectedMemberIds = (): string[] => {
  const config = props.panel.chart_config || {}
  const multiSource = config.multiSource as { enabled?: boolean; memberDatasourceIds?: unknown } | undefined
  const configuredIds = multiSource?.enabled
    ? normalizeMemberIds(multiSource.memberDatasourceIds)
    : normalizeMemberIds([props.panel.member_datasource_id])
  const availableConfiguredIds = filterAvailableMemberIds(configuredIds)
  if (availableConfiguredIds.length) return availableConfiguredIds
  const firstAvailableId = allExecutionTargetIds.value[0] || ''
  return firstAvailableId ? [firstAvailableId] : []
}

const getExecutionTargetName = (target: ExecutionTargetOption) => target.org_name || target.label || target.member_datasource_id
const selectAllEditingTargets = () => {
  editState.memberDatasourceIds = [...allExecutionTargetIds.value]
}
const clearEditingTargets = () => {
  editState.memberDatasourceIds = []
}

const cloneChartConfig = (value: Record<string, unknown> | undefined | null) => {
  try {
    return JSON.parse(JSON.stringify(value || {})) as Record<string, unknown>
  } catch {
    return { ...(value || {}) }
  }
}

const resetEditState = () => {
  editState.name = props.panel.name || ''
  editState.sql = formattedSql.value
  editState.chartType = props.panel.chart_type
  editState.chartConfig = cloneChartConfig(props.panel.chart_config || {})
  editState.memberDatasourceIds = getPanelSelectedMemberIds()
}

const openEditModal = () => {
  resetEditState()
  editModalTab.value = 'basic'
  showEditModal.value = true
}

const handleSaveEditModal = () => {
  const trimmedSql = editState.sql.trim()
  if (!trimmedSql) return
  const trimmedName = editState.name.trim()
  const selectedIds = filterAvailableMemberIds(normalizeMemberIds(editState.memberDatasourceIds))
  if (executionTargets.value.length > 0 && selectedIds.length === 0) {
    ElMessage.warning('请选择执行对象')
    return
  }
  const { multiSource: _oldMultiSource, ...baseChartConfig } = editState.chartConfig || {}
  if (editState.chartType === 'combo') {
    delete baseChartConfig.seriesField
  } else {
    delete baseChartConfig.yField2
  }
  const nextChartConfig = {
    ...baseChartConfig,
    ...(selectedIds.length > 1
      ? { multiSource: { enabled: true, memberDatasourceIds: selectedIds } }
      : {})
  }
  if (trimmedName && trimmedName !== (props.panel.name || '').trim()) {
    emit('editName', trimmedName)
  }
  if (editState.chartType !== props.panel.chart_type) {
    emit('changeChartType', editState.chartType)
  }
  const firstSelectedId = selectedIds[0] || ''
  if (firstSelectedId) {
    emit('updateMemberDatasourceId', firstSelectedId)
  }
  emit('updateChartConfig', nextChartConfig)
  emit('updateSql', trimmedSql)
  showEditModal.value = false
}

// 地图加载状态
const mapLoaded = ref(isChinaMapRegistered())
const mapLoading = ref(false)

// 加载地图
const loadMap = async () => {
  if (mapLoaded.value || mapLoading.value) return
  mapLoading.value = true
  try {
    await registerChinaMap()
    mapLoaded.value = true
    // 地图加载完成后重新渲染
    await nextTick()
    renderChart()
  } catch (err) {
    console.error('地图加载失败:', err)
  } finally {
    mapLoading.value = false
  }
}

// 如果是地图类型，加载地图数据
watch(() => props.panel.chart_type, (type) => {
  if (type === 'map') {
    loadMap()
  }
}, { immediate: true })

// 全屏功能
const panelRef = ref<HTMLDivElement | null>(null)
const isPanelFullscreen = ref(false)

const togglePanelFullscreen = async () => {
  if (!panelRef.value) return
  if (document.fullscreenElement === panelRef.value) {
    await document.exitFullscreen()
    return
  }
  await panelRef.value.requestFullscreen()
}

const handlePanelFullscreenChange = () => {
  isPanelFullscreen.value = document.fullscreenElement === panelRef.value
}

onMounted(() => {
  document.addEventListener('fullscreenchange', handlePanelFullscreenChange)
})

onUnmounted(() => {
  document.removeEventListener('fullscreenchange', handlePanelFullscreenChange)
})

const themeChartPalette = computed(() => {
  // 主题色板在前、标准 12 色板续后（去重）：主题视觉不变，10+ 曲线不再循环撞色。
  const themeColors = props.theme.chartPalette?.length ? props.theme.chartPalette : ['#1677FF', '#36CFC9', '#52C41A', '#FAAD14', '#F5222D']
  const extra = CHART_COLORS.filter(color => !themeColors.some(existing => existing.toLowerCase() === color.toLowerCase()))
  return [...themeColors, ...extra]
})
const defaultAccentColor = computed(() => themeChartPalette.value[0] || props.theme.accentColor || '#1677FF')
const isLightTheme = computed(() => props.theme.cardBg === '#ffffff')

const getAlphaColor = (color: string, alpha: number) => {
  const normalized = color.trim()
  const alphaHex = Math.round(Math.min(Math.max(alpha, 0), 1) * 255).toString(16).padStart(2, '0')
  if (/^#[0-9a-f]{6}$/i.test(normalized)) {
    return `${normalized}${alphaHex}`
  }
  if (/^#[0-9a-f]{3}$/i.test(normalized)) {
    const r = normalized.slice(1, 2)
    const g = normalized.slice(2, 3)
    const b = normalized.slice(3, 4)
    return `#${r}${r}${g}${g}${b}${b}${alphaHex}`
  }
  return normalized
}

const buildColorOptions = (themeColors: string[], baseOptions: { color: string; label: string }[]) => {
  const seen = new Set<string>()
  const options: { color: string; label: string }[] = []
  themeColors.forEach((color, index) => {
    const key = color.toLowerCase()
    if (seen.has(key)) return
    seen.add(key)
    options.push({ color, label: `主题色 ${index + 1}` })
  })
  baseOptions.forEach(item => {
    const key = item.color.toLowerCase()
    if (seen.has(key)) return
    seen.add(key)
    options.push(item)
  })
  return options
}

// KPI 配置
const currentKpiIcon = computed(() => (props.panel.chart_config?.kpiIcon as string) || 'fa-users')
const currentKpiColor = computed(() => (props.panel.chart_config?.kpiColor as string) || defaultAccentColor.value)
const editKpiIcon = computed(() => (editState.chartConfig?.kpiIcon as string) || 'fa-users')
const editKpiColor = computed(() => (editState.chartConfig?.kpiColor as string) || defaultAccentColor.value)
const kpiColorOptions = computed(() => buildColorOptions(themeChartPalette.value, KPI_COLORS))
const currentKpiBgColor = computed(() => {
  const color = currentKpiColor.value
  // 将颜色转换为 rgba 格式，透明度 0.1
  if (color.startsWith('#')) {
    const r = parseInt(color.slice(1, 3), 16)
    const g = parseInt(color.slice(3, 5), 16)
    const b = parseInt(color.slice(5, 7), 16)
    return `rgba(${r}, ${g}, ${b}, 0.12)`
  }
  return color
})

const handleKpiIconSelect = (icon: string) => {
  editState.chartConfig = { ...editState.chartConfig, kpiIcon: icon }
}

const handleKpiColorSelect = (color: string) => {
  editState.chartConfig = { ...editState.chartConfig, kpiColor: color }
}

const handleKpiReset = () => {
  const newConfig = { ...editState.chartConfig }
  delete newConfig.kpiIcon
  delete newConfig.kpiColor
  editState.chartConfig = newConfig
}

// 图表样式配置
const editChartColor = computed(() => (editState.chartConfig?.chartColor as string) || defaultAccentColor.value)
// 组合图折线指标单独调色；未配置时跟随主题调色板第 2 色（与渲染兜底一致）
const editComboLineColor = computed(() => (editState.chartConfig?.comboLineColor as string) || themeChartPalette.value[1] || defaultAccentColor.value)
const chartColorOptions = computed(() => buildColorOptions(themeChartPalette.value, CHART_COLOR_OPTIONS))
const editLineStyle = computed(() => (editState.chartConfig?.lineStyle as string) || 'smooth')

const getBarGradientColor = (color: string, isHorizontal: boolean) => {
  const fadeColor = getAlphaColor(color, 0.22)
  return new echarts.graphic.LinearGradient(
    0,
    isHorizontal ? 0 : 1,
    isHorizontal ? 1 : 0,
    0,
    [
      { offset: 0, color: fadeColor },
      { offset: 1, color },
    ]
  )
}

const getLineAreaGradientColor = (color: string) => {
  return new echarts.graphic.LinearGradient(0, 0, 0, 1, [
    { offset: 0, color: getAlphaColor(color, 0.34) },
    { offset: 0.55, color: getAlphaColor(color, 0.12) },
    { offset: 1, color: getAlphaColor(color, 0) },
  ])
}

const handleChartColorSelect = (color: string) => {
  editState.chartConfig = { ...editState.chartConfig, chartColor: color }
}

const handleComboLineColorSelect = (color: string) => {
  editState.chartConfig = { ...editState.chartConfig, comboLineColor: color }
}

// 面板名称对齐方式（默认居左，存 chart_config.titleAlign）
const editTitleAlign = computed(() => (editState.chartConfig?.titleAlign as string) || 'left')
const handleTitleAlignSelect = (align: string) => {
  const newConfig = { ...editState.chartConfig }
  if (align === 'left') {
    delete newConfig.titleAlign
  } else {
    newConfig.titleAlign = align
  }
  editState.chartConfig = newConfig
}

// 面板名称颜色（默认跟随主题文字色，存 chart_config.titleColor；清空恢复默认）
const editTitleColor = computed(() => (editState.chartConfig?.titleColor as string) || props.theme.textColor)
const handleTitleColorSelect = (color: string | null) => {
  const newConfig = { ...editState.chartConfig }
  if (!color || color === props.theme.textColor) {
    delete newConfig.titleColor
  } else {
    newConfig.titleColor = color
  }
  editState.chartConfig = newConfig
}

// 点击色号文字复制，方便跨面板统一用色
const copyColorCode = async (color: string) => {
  try {
    await navigator.clipboard.writeText(color)
    ElMessage.success(`已复制色号 ${color}`)
  } catch {
    ElMessage.warning('复制失败，请手动复制')
  }
}

const handleLineStyleSelect = (styleId: string) => {
  editState.chartConfig = { ...editState.chartConfig, lineStyle: styleId }
}

const handleChartStyleReset = () => {
  const newConfig = { ...editState.chartConfig }
  delete newConfig.chartColor
  delete newConfig.comboLineColor
  delete newConfig.lineStyle
  editState.chartConfig = newConfig
}

// 图表容器
const chartRef = ref<HTMLDivElement | null>(null)
let chartInstance: echarts.ECharts | null = null

const chartTypes = CHART_TYPES
const chartStyleConfigTypes = ['bar', 'horizontal_bar', 'line', 'area', 'pie', 'combo', 'funnel', 'graph']
const fieldConfigTypes = ['bar', 'horizontal_bar', 'line', 'area', 'pie', 'map', 'combo', 'funnel', 'graph']
const numericChartTypes = ['bar', 'horizontal_bar', 'line', 'area', 'pie', 'map', 'combo', 'funnel'] as const
const supportsChartStyleConfig = computed(() => editState.chartType === 'kpi' || chartStyleConfigTypes.includes(editState.chartType))
const supportsFieldConfig = computed(() => fieldConfigTypes.includes(editState.chartType))
const currentChartTypeMeta = computed(() => {
  return chartTypes.find(item => item.key === editState.chartType) ?? chartTypes[0]
})
const currentChartTypeHelp = computed(() => {
  return CHART_TYPE_HELP_TEXT[editState.chartType] ?? CHART_TYPE_HELP_TEXT.table
})
const DASHBOARD_MEMBER_COLUMN = QUERY_MEMBER_NAME_COLUMN
const DASHBOARD_MEMBER_ID_COLUMN = QUERY_MEMBER_ID_COLUMN

const parseNumericValue = (value: unknown): number | null => {
  return toSafeNumber(value)
}

// 可用列（分类列 & 数值列）
const displayResult = computed(() => flattenQueryResultForDisplay(props.data?.result, { includeMemberColumn: true }))

const availableColumns = computed(() => {
  const result = displayResult.value
  if (!result.columns.length || !result.data.length) {
    return { all: [] as string[], categoryColumns: [] as string[], numericColumns: [] as string[] }
  }
  const columns = result.columns.filter(col => col !== DASHBOARD_MEMBER_ID_COLUMN)
  const numericColumns = getSafeNumericColumns(result.data, columns)
  const categoryColumns = columns.filter(col => !numericColumns.includes(col))
  return { all: columns, categoryColumns, numericColumns }
})

const inferFields = (chartType: Panel['chart_type'], chartConfig: Record<string, unknown> | undefined | null) => {
  const { all, categoryColumns, numericColumns } = availableColumns.value
  if (all.length === 0) {
    return { xField: '', yFields: [] as string[], seriesField: '' }
  }

  const config = chartConfig || {}
  const manualX = config.xField as string | undefined
  const manualY = config.yField as string | undefined
  const manualYSecondary = config.yField2 as string | undefined

  const isMultiSourceResult = all.includes(DASHBOARD_MEMBER_COLUMN)
  const preferredCategoryColumns = categoryColumns.filter(field => field !== DASHBOARD_MEMBER_COLUMN)

  // X 轴：手动设定 > 第一个业务分类字段 > 第一列
  const xField = (manualX && all.includes(manualX)) ? manualX : (preferredCategoryColumns[0] ?? all[0] ?? '')

  if (chartType === 'graph') {
    const targetField = (manualY && all.includes(manualY))
      ? manualY
      : (categoryColumns.find(field => field !== xField) ?? all.find(field => field !== xField) ?? '')
    return { xField, yFields: targetField ? [targetField] : [], seriesField: '' }
  }

  if (chartType === 'combo') {
    const primaryYField = (manualY && numericColumns.includes(manualY))
      ? manualY
      : (numericColumns[0] ?? '')
    const secondaryYField = (manualYSecondary && numericColumns.includes(manualYSecondary) && manualYSecondary !== primaryYField)
      ? manualYSecondary
      : (numericColumns.find(field => field !== primaryYField) ?? '')
    const yFields = [primaryYField, secondaryYField].filter((field): field is string => !!field)
    return { xField, yFields, seriesField: '' }
  }

  // Y 轴只允许使用可安全计算的数值字段，防止大数被 JavaScript 静默截断。
  let yFields: string[]
  if (manualY && numericColumns.includes(manualY)) {
    yFields = [manualY]
  } else {
    yFields = numericColumns
  }

  // 分组字段：手动设定 > 多数据源执行对象 > 第二个业务分类字段
  const seriesField = (config.seriesField as string | undefined && all.includes(config.seriesField as string))
    ? String(config.seriesField)
    : (isMultiSourceResult && ['bar', 'horizontal_bar', 'line', 'area'].includes(chartType)
      ? DASHBOARD_MEMBER_COLUMN
      : (preferredCategoryColumns.find(field => field !== xField) || ''))

  return { xField, yFields, seriesField }
}

// 智能推断字段（支持手动覆盖）
const inferredFields = computed(() => inferFields(props.panel.chart_type, props.panel.chart_config || {}))
const editInferredFields = computed(() => inferFields(editState.chartType, editState.chartConfig))
const chartUnavailable = computed(() => {
  return numericChartTypes.includes(props.panel.chart_type as typeof numericChartTypes[number])
    && displayResult.value.data.length > 0
    && inferredFields.value.yFields.length === 0
})

// 字段配置处理
const handleFieldConfigChange = (field: 'xField' | 'yField' | 'yField2' | 'seriesField', value: string) => {
  editState.chartConfig = { ...editState.chartConfig, [field]: value || undefined }
}

const handleFieldConfigReset = () => {
  const newConfig = { ...editState.chartConfig }
  delete newConfig.xField
  delete newConfig.yField
  delete newConfig.yField2
  delete newConfig.seriesField
  editState.chartConfig = newConfig
}

const buildGraphData = (
  rows: Record<string, unknown>[],
  sourceField: string,
  targetField: string,
  valueField?: string
) => {
  const nodeMap = new Map<string, number>()
  const links: Array<{ source: string; target: string; value?: number; lineStyle?: Record<string, unknown> }> = []

  rows.forEach((row) => {
    const source = String(row.source ?? row.from ?? row.Source ?? row[sourceField] ?? '').trim()
    const target = String(row.target ?? row.to ?? row.Target ?? row[targetField] ?? '').trim()
    if (!source || !target) return
    const value = parseNumericValue(valueField ? row[valueField] : row.value ?? row.weight ?? row.count) ?? undefined
    nodeMap.set(source, (nodeMap.get(source) || 0) + (value ?? 1))
    nodeMap.set(target, nodeMap.get(target) || 0)
    links.push({
      source,
      target,
      value,
      lineStyle: value ? { width: Math.min(5, 1 + Math.log10(value + 1)) } : undefined,
    })
  })

  const values = Array.from(nodeMap.values())
  const maxValue = values.length ? Math.max(...values) : 1
  const nodes = Array.from(nodeMap.entries()).map(([name, value]) => ({
    name,
    value,
    symbolSize: 18 + (maxValue > 0 ? (value / maxValue) * 24 : 0),
  }))
  return { nodes, links }
}

const buildSeriesByField = (
  rows: Record<string, unknown>[],
  xField: string,
  yField: string,
  seriesField: string,
) => {
  if (!seriesField) return null
  const xValues = Array.from(new Set(rows.map(row => String(row[xField] || ''))))
  const seriesNames = Array.from(new Set(rows.map(row => String(row[seriesField] || '')).filter(Boolean)))
  if (!seriesNames.length) return null
  return {
    xValues,
    seriesNames,
    valuesBySeries: seriesNames.map(seriesName => {
      const rowMap = new Map<string, Record<string, unknown>>()
      rows.forEach(row => {
        if (String(row[seriesField] || '') === seriesName) {
          rowMap.set(String(row[xField] || ''), row)
        }
      })
      return xValues.map(xValue => parseNumericValue(rowMap.get(xValue)?.[yField]) ?? 0)
    })
  }
}

// 生成图表配置
const chartOption = computed(() => {
  const result = displayResult.value
  if (!result.data.length) return null

  const { xField, yFields, seriesField } = inferredFields.value
  if (!xField || yFields.length === 0) return null
  const primaryYField = yFields[0]
  if (!primaryYField) return null
  const chartType = props.panel.chart_type
  const data = result.data
  const theme = props.theme

  // 基础配置
  const baseOption: echarts.EChartsOption = {
    backgroundColor: 'transparent',
    animation: true,
    animationDuration: 900,
    animationEasing: 'cubicOut',
    animationDurationUpdate: 650,
    animationEasingUpdate: 'cubicInOut',
    tooltip: {
      trigger: ['pie', 'funnel', 'graph'].includes(chartType) ? 'item' : 'axis',
      backgroundColor: theme.chartTooltipBg,
      borderColor: theme.chartTooltipBorder,
      textStyle: { color: theme.chartTooltipText },
    },
    grid: {
      left: 10,
      right: chartType === 'combo' && yFields.length > 1 ? 44 : 20,
      top: 40,
      bottom: 10,
      containLabel: true,
    },
  }

  // 柱状图
  if (chartType === 'bar' || chartType === 'horizontal_bar') {
    const isHorizontal = chartType === 'horizontal_bar'
    const grouped = seriesField && seriesField !== xField
      ? buildSeriesByField(data, xField, primaryYField, seriesField)
      : null
    const xData = grouped?.xValues ?? data.map(row => String(row[xField] || ''))
    const configuredColor = props.panel.chart_config?.chartColor as string | undefined
    const defaultColors = themeChartPalette.value
    const seriesSource = grouped
      ? grouped.seriesNames.map((name, idx) => ({ name, field: primaryYField, data: grouped.valuesBySeries[idx] || [] }))
      : yFields.map(field => ({ name: field, field, data: data.map(row => parseNumericValue(row[field]) ?? 0) }))
    const series = seriesSource.map((item, idx) => {
      const color = configuredColor && idx === 0 ? configuredColor : defaultColors[idx % defaultColors.length]!
      return {
        name: item.name,
        type: 'bar' as const,
        data: item.data,
        barMaxWidth: 26,
        showBackground: true,
        backgroundStyle: {
          color: getAlphaColor(theme.accentColor, isLightTheme.value ? 0.035 : 0.055),
          borderRadius: isHorizontal ? [0, 4, 4, 0] : [4, 4, 0, 0],
        },
        itemStyle: {
          color: getBarGradientColor(color, isHorizontal),
          borderRadius: isHorizontal ? [0, 4, 4, 0] : [4, 4, 0, 0],
          shadowBlur: isLightTheme.value ? 4 : 10,
          shadowColor: getAlphaColor(color, isLightTheme.value ? 0.16 : 0.28),
        },
        emphasis: {
          focus: 'series' as const,
          itemStyle: {
            shadowBlur: isLightTheme.value ? 8 : 16,
            shadowColor: getAlphaColor(color, isLightTheme.value ? 0.26 : 0.42),
          },
        },
      }
    })

    const splitLineStyle = { lineStyle: { type: 'dashed' as const, color: theme.chartGridLine } }

    return {
      ...baseOption,
      // 多系列时显示图例，说明每个系列的含义；单系列不占空间
      legend: series.length > 1
        ? { top: 4, left: 'center', itemWidth: 14, itemHeight: 8, textStyle: { color: theme.chartAxisText, fontSize: 12 } }
        : undefined,
      xAxis: isHorizontal
        ? { type: 'value', axisLabel: { color: theme.chartAxisText }, splitLine: splitLineStyle }
        : { type: 'category', data: xData, axisLabel: { color: theme.chartAxisText }, splitLine: { show: false } },
      yAxis: isHorizontal
        ? { type: 'category', data: xData, axisLabel: { color: theme.chartAxisText }, splitLine: { show: false } }
        : { type: 'value', axisLabel: { color: theme.chartAxisText }, splitLine: splitLineStyle },
      series,
    }
  }

  // 折线图、面积图、柱线组合图
  if (chartType === 'line' || chartType === 'area' || chartType === 'combo') {
    const grouped = seriesField && seriesField !== xField
      ? buildSeriesByField(data, xField, primaryYField, seriesField)
      : null
    const xData = grouped?.xValues ?? data.map(row => String(row[xField] || ''))
    const configuredColor = props.panel.chart_config?.chartColor as string | undefined
    const configuredLineColor = props.panel.chart_config?.comboLineColor as string | undefined
    const lineStyleId = (props.panel.chart_config?.lineStyle as string) || 'smooth'
    const lineStyleConfig = LINE_STYLES.find(s => s.id === lineStyleId) ?? LINE_STYLES[0]!
    const defaultColors = themeChartPalette.value
    const seriesSource = grouped
      ? grouped.seriesNames.map((name, idx) => ({ name, field: primaryYField, data: grouped.valuesBySeries[idx] || [] }))
      : yFields.map(field => ({ name: field, field, data: data.map(row => parseNumericValue(row[field]) ?? 0) }))
    const useDualAxisCombo = chartType === 'combo' && !grouped && seriesSource.length > 1
    const series = seriesSource.map((item, idx) => {
      // 组合图柱/线各自调色：chartColor 管柱状（idx 0），comboLineColor 管折线（idx >= 1）
      const color = chartType === 'combo' && idx >= 1 && configuredLineColor
        ? configuredLineColor
        : configuredColor && idx === 0 ? configuredColor : defaultColors[idx % defaultColors.length]!
      const isComboBar = chartType === 'combo' && idx === 0
      return {
        name: item.name,
        type: isComboBar ? 'bar' as const : 'line' as const,
        data: item.data,
        yAxisIndex: useDualAxisCombo ? (idx === 0 ? 0 : 1) : 0,
        smooth: !isComboBar && lineStyleConfig.smooth,
        showSymbol: !isComboBar && data.length <= 12,
        symbolSize: isComboBar ? undefined : 6,
        barMaxWidth: isComboBar ? 26 : undefined,
        lineStyle: isComboBar ? undefined : {
          width: 2.5,
          color,
          shadowBlur: isLightTheme.value ? 4 : 10,
          shadowColor: getAlphaColor(color, isLightTheme.value ? 0.18 : 0.34),
        },
        itemStyle: {
          color,
          borderColor: isComboBar ? undefined : theme.panelBg,
          borderRadius: isComboBar ? [4, 4, 0, 0] : undefined,
          borderWidth: isComboBar ? undefined : 1,
        },
        areaStyle: !isComboBar && (chartType === 'area' || lineStyleConfig.areaFill) ? { color: getLineAreaGradientColor(color) } : undefined,
        emphasis: {
          focus: 'series' as const,
          lineStyle: isComboBar ? undefined : { width: 3.2 },
        },
      }
    })

    const splitLineStyle = { lineStyle: { type: 'dashed' as const, color: theme.chartGridLine } }

    return {
      ...baseOption,
      // 多系列时显示图例，说明柱/线各自代表的指标；单系列不占空间
      legend: series.length > 1
        ? { top: 4, left: 'center', itemWidth: 14, itemHeight: 8, textStyle: { color: theme.chartAxisText, fontSize: 12 } }
        : undefined,
      xAxis: { type: 'category', data: xData, axisLabel: { color: theme.chartAxisText }, splitLine: { show: false } },
      yAxis: useDualAxisCombo
        ? [
            {
              type: 'value',
              axisLine: { show: false },
              axisTick: { show: false },
              axisLabel: { color: theme.chartAxisText },
              splitLine: splitLineStyle,
            },
            {
              type: 'value',
              position: 'right',
              axisLine: { show: true, lineStyle: { color: theme.chartGridLine } },
              axisTick: { show: false },
              axisLabel: { color: theme.chartAxisText, margin: 10 },
              splitLine: { show: false },
            },
          ]
        : { type: 'value', axisLabel: { color: theme.chartAxisText }, splitLine: splitLineStyle },
      series,
    }
  }

  // 饼图
  if (chartType === 'pie') {
    const configuredColor = props.panel.chart_config?.chartColor as string | undefined
    const pieColors = configuredColor
      ? [configuredColor, ...themeChartPalette.value.filter(color => color.toLowerCase() !== configuredColor.toLowerCase())]
      : themeChartPalette.value
    const pieData = data.slice(0, 10).map(row => ({
      name: String(row[xField] || ''),
      value: parseNumericValue(row[primaryYField]) ?? 0,
    }))
    const pieTotal = pieData.reduce((sum, item) => sum + item.value, 0)
    const pieTotalText = String(pieTotal)

    return {
      ...baseOption,
      color: pieColors,
      title: {
        text: pieTotalText,
        subtext: '合计',
        left: 'center',
        top: 'center',
        textStyle: {
          color: theme.textColor,
          fontSize: 20,
          fontWeight: 700,
        },
        subtextStyle: {
          color: theme.textMuted,
          fontSize: 11,
          lineHeight: 16,
        },
      },
      series: [{
        type: 'pie',
        radius: ['40%', '70%'],
        center: ['50%', '50%'],
        avoidLabelOverlap: true,
        data: pieData,
        label: {
          color: theme.textMuted,
          formatter: '{b}: {d}%',
        },
        labelLine: {
          lineStyle: { color: theme.accentColor },
        },
        itemStyle: {
          borderRadius: 0,
          borderColor: theme.panelBg,
          borderWidth: 2,
          shadowBlur: isLightTheme.value ? 2 : 8,
          shadowColor: getAlphaColor(theme.accentColor, isLightTheme.value ? 0.12 : 0.24),
        },
        emphasis: {
          scaleSize: 6,
          itemStyle: {
            shadowBlur: isLightTheme.value ? 8 : 16,
            shadowColor: getAlphaColor(theme.accentColor, isLightTheme.value ? 0.22 : 0.38),
          },
        },
      }],
    }
  }

  // 漏斗图
  if (chartType === 'funnel') {
    const configuredColor = props.panel.chart_config?.chartColor as string | undefined
    const funnelColors = configuredColor
      ? [configuredColor, ...themeChartPalette.value.filter(color => color.toLowerCase() !== configuredColor.toLowerCase())]
      : themeChartPalette.value
    const funnelData = data.map(row => ({
      name: String(row[xField] || ''),
      value: parseNumericValue(row[primaryYField]) ?? 0,
    }))

    return {
      ...baseOption,
      color: funnelColors,
      tooltip: {
        trigger: 'item',
        formatter: '{b}: {c}',
        backgroundColor: theme.chartTooltipBg,
        borderColor: theme.chartTooltipBorder,
        textStyle: { color: theme.chartTooltipText },
      },
      series: [{
        type: 'funnel',
        left: '5%',
        right: '5%',
        top: 12,
        bottom: 12,
        minSize: '18%',
        maxSize: '100%',
        sort: 'descending',
        gap: 4,
        data: funnelData,
        label: {
          show: true,
          position: 'inside',
          color: '#fff',
          formatter: '{b}\n{c}',
          fontSize: 12,
          fontWeight: 600,
          lineHeight: 18,
        },
        labelLine: { show: false },
        itemStyle: {
          borderColor: theme.panelBg,
          borderWidth: 2,
          shadowBlur: isLightTheme.value ? 2 : 8,
          shadowColor: getAlphaColor(theme.accentColor, isLightTheme.value ? 0.12 : 0.24),
        },
        emphasis: { focus: 'self' },
      }],
    }
  }

  // 关系图
  if (chartType === 'graph') {
    const { nodes, links } = buildGraphData(data, xField, primaryYField, yFields[1])
    if (!nodes.length || !links.length) return null
    return {
      ...baseOption,
      tooltip: {
        trigger: 'item',
        backgroundColor: theme.chartTooltipBg,
        borderColor: theme.chartTooltipBorder,
        textStyle: { color: theme.chartTooltipText },
      },
      series: [{
        type: 'graph',
        layout: 'force',
        roam: true,
        draggable: true,
        data: nodes,
        links,
        edgeSymbol: ['none', 'arrow'],
        edgeSymbolSize: 8,
        label: {
          show: true,
          color: theme.textColor,
          fontSize: 11,
        },
        force: {
          repulsion: 160,
          edgeLength: [45, 130],
        },
        itemStyle: {
          color: props.panel.chart_config?.chartColor as string | undefined || theme.accentColor,
          shadowBlur: isLightTheme.value ? 4 : 12,
          shadowColor: getAlphaColor(theme.accentColor, isLightTheme.value ? 0.18 : 0.36),
        },
        lineStyle: {
          color: theme.chartAxisText,
          opacity: 0.72,
          curveness: 0.18,
        },
        emphasis: { focus: 'adjacency' },
      }],
    }
  }

  // 地图
  if (chartType === 'map' && mapLoaded.value) {
    const mapData = data.map(row => ({
      name: normalizeProvinceName(String(row[xField] || '')),
      value: parseNumericValue(row[primaryYField]) ?? 0,
    }))

    // 计算数值范围
    const values = mapData.map(d => d.value).filter(v => v > 0)
    const minValue = values.length > 0 ? Math.min(...values) : 0
    const maxValue = values.length > 0 ? Math.max(...values) : 100

    const isDark = theme.pageBg !== '#F5F7FA'

    return {
      tooltip: {
        trigger: 'item',
        formatter: (params: any) => {
          if (params.value !== undefined && !isNaN(params.value)) {
            return `${params.name}: ${String(params.value)}`
          }
          return `${params.name}: 暂无数据`
        },
        backgroundColor: theme.chartTooltipBg,
        borderColor: theme.chartTooltipBorder,
        textStyle: { color: theme.chartTooltipText },
      },
      visualMap: {
        min: minValue,
        max: maxValue,
        left: 'left',
        top: 'bottom',
        text: ['高', '低'],
        calculable: true,
        inRange: {
          color: isDark
            ? ['#7f1d1d', '#b91c1c', '#dc2626', '#ef4444', '#fca5a5']
            : ['#fee0d2', '#fcbba1', '#fc9272', '#fb6a4a', '#cb181d'],
        },
        textStyle: { color: theme.chartAxisText },
      },
      series: [{
        type: 'map',
        map: 'china',
        roam: true,
        zoom: 1.2,
        scaleLimit: { min: 0.8, max: 3 },
        label: {
          show: false,
          fontSize: 10,
          color: theme.textColor,
        },
        emphasis: {
          label: { show: true, fontWeight: 'bold' },
          itemStyle: {
            areaColor: '#ffd666',
            shadowBlur: 20,
            shadowColor: 'rgba(0, 0, 0, 0.3)',
          },
        },
        itemStyle: {
          areaColor: isDark ? '#374151' : '#f0f0f0',
          borderColor: isDark ? '#4b5563' : '#ffffff',
          borderWidth: 1,
        },
        data: mapData,
      }],
    }
  }

  return null
})

// 渲染图表
const renderChart = () => {
  if (!chartRef.value || props.panel.chart_type === 'table' || props.panel.chart_type === 'kpi') {
    return
  }

  // 地图需要等待加载完成
  if (props.panel.chart_type === 'map' && !mapLoaded.value) {
    return
  }

  if (chartInstance && chartInstance.getDom() !== chartRef.value) {
    chartInstance.dispose()
    chartInstance = null
  }

  if (!chartInstance) {
    chartInstance = echarts.init(chartRef.value)
  }

  if (chartOption.value) {
    chartInstance.resize()
    chartInstance.setOption(chartOption.value, true)
  } else {
    chartInstance.clear()
  }
}

// 监听数据变化
watch(() => [props.data, props.panel.chart_type, props.panel.chart_config, props.theme, mapLoaded.value], async () => {
  await nextTick()
  renderChart()
}, { deep: true, flush: 'post' })

watch(chartRef, async (currentEl, previousEl) => {
  if (previousEl) {
    resizeObserver.unobserve(previousEl)
  }
  if (!currentEl) {
    chartInstance?.dispose()
    chartInstance = null
    return
  }
  resizeObserver.observe(currentEl)
  await nextTick()
  renderChart()
}, { flush: 'post' })

// 监听容器大小
const resizeObserver = new ResizeObserver(() => {
  chartInstance?.resize()
})

onMounted(() => {
  if (chartRef.value) {
    resizeObserver.observe(chartRef.value)
  }
  renderChart()
})

onUnmounted(() => {
  resizeObserver.disconnect()
  chartInstance?.dispose()
  chartInstance = null
})

// KPI 原始值
const kpiRawValue = computed(() => {
  const result = displayResult.value
  if (!result.data.length) return null
  const firstRow = result.data[0]
  const { yFields } = inferredFields.value
  const primaryYField = yFields[0]
  if (!firstRow || !primaryYField) return null
  return firstRow[primaryYField]
})

// KPI 值格式化
const kpiValue = computed(() => {
  const value = kpiRawValue.value
  if (value === null || value === undefined || value === '') return { value: '-', unit: '' }
  return { value: String(value), unit: '' }
})

// KPI 完整值（用于 tooltip）
const kpiFullValue = computed(() => {
  const value = kpiRawValue.value
  if (value === null || value === undefined || value === '') return ''
  return String(value)
})

const kpiItems = computed(() => {
  const result = displayResult.value
  if (!result.data.length || !result.columns.includes(DASHBOARD_MEMBER_COLUMN)) return []
  const { yFields } = inferredFields.value
  const primaryYField = yFields[0]
  if (!primaryYField) return []
  return result.data.map(row => {
    const rawValue = row[primaryYField]
    return {
      name: String(row[DASHBOARD_MEMBER_COLUMN] || '执行对象'),
      display: { value: String(rawValue ?? '-'), unit: '' },
      fullValue: String(rawValue ?? ''),
    }
  })
})

const isMultiKpiCard = computed(() => kpiItems.value.length > 1)
</script>

<template>
  <div
    ref="panelRef"
    class="panel-card flex flex-col h-full border border-solid overflow-visible"
    :style="{
      background: theme.panelBg,
      borderColor: theme.panelBorder,
      '--panel-border-color': theme.panelBorder,
      '--panel-corner-color': theme.accentColor,
      '--panel-breath-glow': isLightTheme ? `${theme.accentColor}22` : `${theme.accentColor}33`,
      '--panel-icon-glow': `${currentKpiColor}2e`,
    }"
  >
    <!-- 面板头部 -->
    <div :class="['relative flex items-center justify-end px-2.5 py-2 gap-2 min-h-10', !isPreviewMode && 'drag-handle cursor-move']" :style="{ background: theme.panelHeaderBg }">
      <div
        class="flex items-center gap-2 text-[13px] font-medium min-w-0 flex-1"
        :class="panel.chart_config?.titleAlign === 'center'
          ? 'pointer-events-none absolute inset-x-0 justify-center px-16'
          : ''"
        :style="{ color: (panel.chart_config?.titleColor as string) || theme.textColor }"
      >
        <span class="overflow-hidden text-ellipsis whitespace-nowrap">{{ panel.name }}</span>
      </div>
      <div v-if="!isPreviewMode" class="flex gap-0.5 flex-shrink-0">
        <button
          type="button"
          class="action-btn inline-flex h-6 w-6 items-center justify-center rounded border-none bg-transparent text-xs transition-all duration-200 hover:bg-gray-500/15"
          :style="{ color: theme.textMuted }"
          title="修改"
          @click="openEditModal"
        >
          <i class="fa-solid fa-pen-to-square"></i>
        </button>
        <button
          class="action-btn w-6 h-6 flex items-center justify-center border-none bg-transparent rounded cursor-pointer transition-all duration-200 text-xs hover:bg-gray-500/15"
          @click="emit('refresh')"
          :style="{ color: theme.textMuted }"
          title="刷新"
        >
          <i class="fa-solid fa-sync-alt"></i>
        </button>
        <!-- 删除 -->
        <button
          class="action-btn w-6 h-6 flex items-center justify-center border-none bg-transparent rounded cursor-pointer transition-all duration-200 text-xs hover:bg-red-500/20 hover:!text-red-500"
          @click="emit('delete')"
          :style="{ color: theme.textMuted }"
          title="删除"
        >
          <i class="fa-solid fa-trash"></i>
        </button>
      </div>
      <!-- 预览模式下的全屏按钮 -->
      <button
        v-if="isPreviewMode"
        class="fullscreen-btn w-6 h-6 flex items-center justify-center border-none bg-transparent rounded cursor-pointer transition-all duration-200 text-xs ml-auto opacity-0 hover:bg-gray-500/15"
        @click="togglePanelFullscreen"
        :style="{ color: theme.textMuted }"
        :title="isPanelFullscreen ? '退出全屏' : '全屏查看'"
      >
        <i :class="isPanelFullscreen ? 'fa-solid fa-compress' : 'fa-solid fa-expand'"></i>
      </button>
    </div>

    <!-- 面板内容 -->
    <div class="flex-1 min-h-0 flex flex-col overflow-hidden">
      <!-- 加载状态 -->
      <div v-if="loading" class="flex-1 flex flex-col items-center justify-center gap-2 text-[13px] [&>i]:text-2xl [&>i]:opacity-50" :style="{ color: theme.textMuted }">
        <i class="fa-solid fa-spinner fa-spin"></i>
        <span>加载中...</span>
      </div>

      <!-- 错误状态 -->
      <div v-else-if="displayError" class="flex-1 min-h-0 flex flex-col items-center justify-center gap-2 px-4 text-center text-[13px] text-red-500 [&>i]:text-2xl">
        <i class="fa-solid fa-exclamation-triangle"></i>
        <span class="max-w-full leading-relaxed break-words">{{ displayError }}</span>
      </div>

      <!-- 无数据 -->
      <div v-else-if="!displayResult.data.length" class="flex-1 flex flex-col items-center justify-center gap-2 text-[13px] [&>i]:text-2xl [&>i]:opacity-50" :style="{ color: theme.textMuted }">
        <i class="fa-solid fa-inbox"></i>
        <span>暂无数据</span>
      </div>

      <!-- 地图加载中 -->
      <div v-else-if="panel.chart_type === 'map' && !mapLoaded" class="flex-1 flex flex-col items-center justify-center gap-2 text-[13px] [&>i]:text-2xl [&>i]:opacity-50" :style="{ color: theme.textMuted }">
        <i class="fa-solid fa-spinner fa-spin"></i>
        <span>加载地图数据...</span>
      </div>

      <!-- KPI 卡片 -->
      <template v-else-if="panel.chart_type === 'kpi'">
        <div v-if="isMultiKpiCard" class="flex-1 min-h-0 overflow-auto px-4 pb-4 pt-1">
          <div class="grid gap-2 sm:grid-cols-2">
            <div
              v-for="item in kpiItems"
              :key="item.name"
              class="flex min-h-[88px] flex-col justify-between rounded-xl px-3 py-3"
              :style="{ background: currentKpiBgColor }"
              :title="`${item.name}: ${item.fullValue}`"
            >
              <div class="flex items-center justify-between gap-3">
                <span class="min-w-0 truncate text-[12px] font-medium tracking-normal" :style="{ color: theme.textMuted }">{{ item.name }}</span>
                <div class="kpi-mini-icon flex h-7 w-7 items-center justify-center rounded-lg text-[14px]" :style="{ color: currentKpiColor, background: `${currentKpiColor}14` }">
                  <i :class="['fa-solid', currentKpiIcon]"></i>
                </div>
              </div>
              <div class="flex items-end gap-1 leading-none">
                <span class="text-[26px] font-bold tabular-nums" :style="{ color: theme.textColor }">{{ item.display.value }}</span>
                <span v-if="item.display.unit" class="pb-0.5 text-sm font-medium" :style="{ color: theme.textMuted }">{{ item.display.unit }}</span>
              </div>
            </div>
          </div>
        </div>
        <div v-else class="flex-1 flex flex-row items-center justify-between px-5 pb-4">
          <div class="flex items-baseline leading-none" :title="kpiFullValue">
            <span class="kpi-value text-[32px] font-bold tabular-nums" :style="{ color: theme.textColor }">{{ kpiValue.value }}</span>
            <span v-if="kpiValue.unit" class="text-lg font-medium ml-0.5" :style="{ color: theme.textMuted }">{{ kpiValue.unit }}</span>
          </div>
          <div class="kpi-icon w-12 h-12 flex items-center justify-center text-[22px] rounded-xl" :style="{ color: currentKpiColor, background: currentKpiBgColor }">
            <i :class="['fa-solid', currentKpiIcon]"></i>
          </div>
        </div>
      </template>

      <!-- 表格 -->
      <template v-else-if="panel.chart_type === 'table'">
        <div class="flex-1 overflow-auto px-3 pb-3">
          <table class="w-full border-collapse border-spacing-0 text-[13px]" :style="{ color: theme.textColor }">
            <thead class="sticky top-0 z-[1]" :style="{ background: theme.tableHeaderBg, borderColor: theme.tableBorder }">
              <tr>
                <th v-for="col in displayResult.columns" :key="col" class="px-4 py-3 text-left font-semibold whitespace-nowrap" :style="{ borderBottom: `1px solid ${theme.tableBorder}` }">{{ col }}</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="(row, idx) in displayResult.data"
                :key="idx"
                class="transition-colors duration-200 hover:bg-[var(--row-hover)]"
                :style="{ '--row-hover': theme.tableRowHover }"
              >
                <td
                  v-for="col in displayResult.columns"
                  :key="col"
                  class="px-4 py-2.5 text-left"
                  :class="{ 'font-semibold tabular-nums': isNumericValue(row[col]) }"
                  :style="{ borderBottom: `1px solid ${theme.tableBorder}` }"
                >
                  {{ row[col] ?? '' }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>

      <!-- 无可安全计算的数值列 -->
      <div v-else-if="chartUnavailable" class="flex-1 flex flex-col items-center justify-center gap-2 px-4 text-center text-[13px] [&>i]:text-2xl [&>i]:opacity-50" :style="{ color: theme.textMuted }">
        <i class="fa-solid fa-chart-column"></i>
        <span>当前结果没有可安全计算的数值字段</span>
      </div>

      <!-- 图表 -->
      <template v-else>
        <div ref="chartRef" class="flex-1 min-h-[200px]"></div>
      </template>
    </div>

    <el-dialog v-model="showEditModal" title="修改面板" width="860px" append-to-body>
      <el-tabs v-model="editModalTab">
        <el-tab-pane label="基础" name="basic">
          <div class="space-y-4">
            <div>
              <div class="mb-1.5 flex items-center justify-between">
                <label class="block text-[13px] text-gray-500">面板名称</label>
                <div class="flex items-center gap-1">
                  <button
                    v-for="item in [{ id: 'left', icon: 'fa-align-left', label: '居左' }, { id: 'center', icon: 'fa-align-center', label: '居中' }]"
                    :key="item.id"
                    type="button"
                    class="inline-flex h-7 items-center gap-1 rounded border border-solid px-2 text-[12px] transition-all duration-200"
                    :class="editTitleAlign === item.id ? 'border-blue-500 bg-blue-50 text-blue-600' : 'border-gray-200 bg-white text-gray-500 hover:border-blue-300'"
                    @click="handleTitleAlignSelect(item.id)"
                    :title="`名称${item.label}`"
                  >
                    <i :class="['fa-solid', item.icon]"></i>{{ item.label }}
                  </button>
                  <el-color-picker
                    :model-value="editTitleColor"
                    size="small"
                    :predefine="chartColorOptions.map(item => item.color)"
                    title="标题颜色（清空恢复默认）"
                    @change="handleTitleColorSelect"
                  />
                </div>
              </div>
              <el-input
                v-model="editState.name"
                maxlength="24"
                show-word-limit
                placeholder="请输入面板名称"
              />
            </div>

            <div v-if="panel.datasource_id">
              <ExecutionTargetPicker
                v-if="executionTargets.length > 0"
                v-model="editState.memberDatasourceIds"
                :targets="executionTargets"
                :multiple="true"
                :root-label="datasourceName"
                inline
              />
            </div>

            <div>
              <label class="mb-1.5 block text-[13px] text-gray-500">SQL</label>
              <div class="sql-editor-body rounded border border-gray-200 !p-0">
                <Codemirror
                  v-model="editState.sql"
                  :extensions="sqlExtensions"
                  :style="{ maxHeight: '300px', overflow: 'auto' }"
                />
              </div>
            </div>
          </div>
        </el-tab-pane>

        <el-tab-pane label="图表" name="chart">
          <div class="space-y-5">
            <section>
              <div class="mb-2 text-[13px] text-gray-500">展示方式</div>
              <div class="grid grid-cols-6 gap-2">
                <button
                  v-for="type in chartTypes"
                  :key="type.key"
                  type="button"
                  class="flex min-h-[56px] flex-col items-center justify-center gap-1 rounded-lg border border-solid px-2 py-2 text-[12px] transition-all duration-200"
                  :class="editState.chartType === type.key ? 'border-blue-500 bg-blue-50 text-blue-600' : 'border-gray-200 bg-white text-gray-700 hover:border-blue-300 hover:bg-blue-50/50'"
                  @click="editState.chartType = type.key"
                >
                  <i :class="['fa-solid', type.icon, type.key === 'kpi' ? 'text-[14px]' : 'text-[13px]']"></i>
                  <span class="max-w-full text-center leading-4">{{ type.label }}</span>
                </button>
              </div>
              <div class="mt-3 rounded-lg border border-blue-100 bg-blue-50/70 px-3 py-2.5">
                <div class="flex items-center gap-2 text-[12px] font-medium text-blue-700">
                  <i :class="['fa-solid', currentChartTypeMeta.icon]"></i>
                  <span>{{ currentChartTypeMeta.label }}</span>
                </div>
                <div class="mt-1 text-[12px] leading-5 text-blue-700/90">{{ currentChartTypeHelp.summary }}</div>
                <div v-for="(item, idx) in currentChartTypeHelp.examples" :key="idx" class="mt-1">
                  <div class="text-[12px] leading-5 text-blue-600">场景{{ currentChartTypeHelp.examples.length > 1 ? ` ${idx + 1}` : '' }}：{{ item.scene }}</div>
                  <div class="mt-0.5 flex min-w-0 items-start gap-1.5 text-[12px] leading-5 text-blue-600">
                    <span class="shrink-0">SQL：</span>
                    <code class="min-w-0 flex-1 whitespace-normal break-words rounded bg-white/80 px-1.5 py-0.5 font-mono text-[11px] text-blue-700">{{ item.sql }}</code>
                  </div>
                </div>
              </div>
            </section>

            <section v-if="supportsChartStyleConfig" class="rounded-lg border border-gray-200 bg-gray-50/60 p-3">
              <div class="mb-3 flex items-center justify-between gap-3">
                <div class="text-[13px] font-medium text-gray-600">
                  {{ editState.chartType === 'kpi' ? 'KPI 样式' : '图表样式' }}
                </div>
                <el-button text size="small" @click="editState.chartType === 'kpi' ? handleKpiReset() : handleChartStyleReset()">恢复默认</el-button>
              </div>

              <div v-if="editState.chartType === 'kpi'" class="mb-4">
                <div class="mb-2 text-[12px] text-gray-500">图标</div>
                <div class="grid grid-cols-10 gap-2">
                  <button
                    v-for="item in KPI_ICONS"
                    :key="item.icon"
                    type="button"
                    class="flex h-9 w-9 items-center justify-center rounded-lg border border-solid bg-white text-sm transition-all duration-200 hover:scale-105"
                    :style="{
                      color: editKpiIcon === item.icon ? editKpiColor : '#999',
                      borderColor: editKpiIcon === item.icon ? editKpiColor : '#e5e7eb'
                    }"
                    @click="handleKpiIconSelect(item.icon)"
                    :title="item.label"
                  >
                    <i :class="['fa-solid', item.icon]"></i>
                  </button>
                </div>
              </div>

              <div>
                <div class="mb-2 text-[12px] text-gray-500">{{ editState.chartType === 'combo' ? '柱状颜色' : '颜色' }}</div>
                <div class="flex flex-wrap gap-2.5">
                  <button
                    v-for="item in editState.chartType === 'kpi' ? kpiColorOptions : chartColorOptions"
                    :key="item.color"
                    type="button"
                    class="flex h-9 w-9 items-center justify-center rounded-full border-2 border-transparent text-xs text-white transition-all duration-200 hover:scale-105"
                    :class="{ 'border-gray-300 shadow-[0_0_8px_currentColor]': (editState.chartType === 'kpi' ? editKpiColor : editChartColor) === item.color }"
                    :style="{ background: item.color }"
                    @click="editState.chartType === 'kpi' ? handleKpiColorSelect(item.color) : handleChartColorSelect(item.color)"
                    :title="item.label"
                  >
                    <i v-if="(editState.chartType === 'kpi' ? editKpiColor : editChartColor) === item.color" class="fa-solid fa-check"></i>
                  </button>
                  <div class="flex items-center gap-1.5">
                    <el-color-picker
                      :model-value="editState.chartType === 'kpi' ? editKpiColor : editChartColor"
                      :predefine="(editState.chartType === 'kpi' ? kpiColorOptions : chartColorOptions).map(item => item.color)"
                      @change="(val: string | null) => val && (editState.chartType === 'kpi' ? handleKpiColorSelect(val) : handleChartColorSelect(val))"
                    />
                    <span
                      class="cursor-pointer font-mono text-[12px] text-gray-500 hover:text-blue-500"
                      title="点击复制色号"
                      @click="copyColorCode(editState.chartType === 'kpi' ? editKpiColor : editChartColor)"
                    >{{ editState.chartType === 'kpi' ? editKpiColor : editChartColor }}</span>
                  </div>
                </div>
              </div>

              <div v-if="editState.chartType === 'combo'" class="mt-4">
                <div class="mb-2 text-[12px] text-gray-500">折线颜色</div>
                <div class="flex flex-wrap gap-2.5">
                  <button
                    v-for="item in chartColorOptions"
                    :key="item.color"
                    type="button"
                    class="flex h-9 w-9 items-center justify-center rounded-full border-2 border-transparent text-xs text-white transition-all duration-200 hover:scale-105"
                    :class="{ 'border-gray-300 shadow-[0_0_8px_currentColor]': editComboLineColor === item.color }"
                    :style="{ background: item.color }"
                    @click="handleComboLineColorSelect(item.color)"
                    :title="item.label"
                  >
                    <i v-if="editComboLineColor === item.color" class="fa-solid fa-check"></i>
                  </button>
                  <div class="flex items-center gap-1.5">
                    <el-color-picker
                      :model-value="editComboLineColor"
                      :predefine="chartColorOptions.map(item => item.color)"
                      @change="(val: string | null) => val && handleComboLineColorSelect(val)"
                    />
                    <span
                      class="cursor-pointer font-mono text-[12px] text-gray-500 hover:text-blue-500"
                      title="点击复制色号"
                      @click="copyColorCode(editComboLineColor)"
                    >{{ editComboLineColor }}</span>
                  </div>
                </div>
              </div>

              <div v-if="['line', 'area', 'combo'].includes(editState.chartType)" class="mt-4">
                <div class="mb-2 text-[12px] text-gray-500">线条样式</div>
                <div class="grid grid-cols-4 gap-2">
                  <button
                    v-for="item in LINE_STYLES"
                    :key="item.id"
                    type="button"
                    class="rounded-lg border border-solid px-3 py-2.5 text-center text-[13px] transition-all duration-200"
                    :class="editLineStyle === item.id ? 'border-blue-500 bg-blue-50 text-blue-600' : 'border-gray-200 bg-white text-gray-700 hover:border-blue-300'"
                    @click="handleLineStyleSelect(item.id)"
                  >
                    {{ item.label }}
                  </button>
                </div>
              </div>
            </section>

            <section v-if="supportsFieldConfig" class="rounded-lg border border-gray-200 bg-gray-50/60 p-3">
              <div class="mb-3 flex items-center justify-between gap-3">
                <div class="text-[13px] font-medium text-gray-600">字段配置</div>
                <el-button text size="small" @click="handleFieldConfigReset">重置为智能推断</el-button>
              </div>
              <div class="grid grid-cols-3 gap-3">
                <div>
                  <label class="mb-1.5 block text-[12px] text-gray-500">
                    {{ editState.chartType === 'graph' ? '起点字段' : 'X轴（分类维度）' }}
                  </label>
                  <el-select :model-value="editInferredFields.xField" class="w-full" @change="(val: string) => handleFieldConfigChange('xField', val)">
                    <el-option v-for="col in availableColumns.all" :key="col" :value="col" :label="`${col}${availableColumns.categoryColumns.includes(col) ? ' (分类)' : ' (数值)'}`" />
                  </el-select>
                </div>
                <div>
                  <label class="mb-1.5 block text-[12px] text-gray-500">
                    {{ editState.chartType === 'graph' ? '终点字段' : editState.chartType === 'combo' ? '柱状指标' : 'Y轴（数值）' }}
                  </label>
                  <el-select :model-value="editInferredFields.yFields[0] || ''" class="w-full" @change="(val: string) => handleFieldConfigChange('yField', val)">
                    <el-option
                      v-for="col in editState.chartType === 'graph' ? availableColumns.all : availableColumns.numericColumns"
                      :key="col"
                      :value="col"
                      :label="`${col}${availableColumns.numericColumns.includes(col) ? ' (数值)' : ' (分类)'}`"
                    />
                  </el-select>
                </div>
                <div v-if="editState.chartType === 'combo'">
                  <label class="mb-1.5 block text-[12px] text-gray-500">折线指标</label>
                  <el-select :model-value="editInferredFields.yFields[1] || ''" class="w-full" @change="(val: string) => handleFieldConfigChange('yField2', val)">
                    <el-option v-for="col in availableColumns.numericColumns" :key="col" :value="col" :label="col" />
                  </el-select>
                </div>
                <div v-else-if="['bar', 'horizontal_bar', 'line', 'area'].includes(editState.chartType)">
                  <label class="mb-1.5 block text-[12px] text-gray-500">分组字段（可选）</label>
                  <el-select :model-value="(editState.chartConfig?.seriesField as string) || ''" class="w-full" @change="(val: string) => handleFieldConfigChange('seriesField', val)">
                    <el-option value="" label="不分组" />
                    <el-option v-for="col in availableColumns.categoryColumns" :key="col" :value="col" :label="col" />
                  </el-select>
                </div>
              </div>
            </section>
          </div>
        </el-tab-pane>
      </el-tabs>

      <template #footer>
        <el-button @click="showEditModal = false">取消</el-button>
        <el-button type="primary" :disabled="!editState.name.trim() || !editState.sql.trim()" @click="handleSaveEditModal">
          应用修改
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.panel-card {
  position: relative;
  border-radius: 0;
  box-shadow:
    inset 0 0 0 1px var(--panel-border-color),
    0 0 0 rgba(0, 0, 0, 0);
  animation: panel-breathe 4.8s ease-in-out infinite;
}

.panel-card::before,
.panel-card::after {
  content: "";
  position: absolute;
  inset: -1px;
  pointer-events: none;
  z-index: 2;
}

.panel-card::before {
  background:
    linear-gradient(var(--panel-corner-color), var(--panel-corner-color)) left top / 9px 2px no-repeat,
    linear-gradient(var(--panel-corner-color), var(--panel-corner-color)) left top / 2px 9px no-repeat,
    linear-gradient(var(--panel-corner-color), var(--panel-corner-color)) right top / 9px 2px no-repeat,
    linear-gradient(var(--panel-corner-color), var(--panel-corner-color)) right top / 2px 9px no-repeat;
  animation: panel-corner-breathe 4.8s ease-in-out infinite;
}

.panel-card::after {
  background:
    linear-gradient(var(--panel-corner-color), var(--panel-corner-color)) left bottom / 9px 2px no-repeat,
    linear-gradient(var(--panel-corner-color), var(--panel-corner-color)) left bottom / 2px 9px no-repeat,
    linear-gradient(var(--panel-corner-color), var(--panel-corner-color)) right bottom / 9px 2px no-repeat,
    linear-gradient(var(--panel-corner-color), var(--panel-corner-color)) right bottom / 2px 9px no-repeat;
  animation: panel-corner-breathe 4.8s ease-in-out infinite;
}

.kpi-value {
  transition: color 0.25s ease, text-shadow 0.25s ease;
}

.kpi-icon {
  transition: transform 0.3s ease, box-shadow 0.3s ease;
  animation: kpi-icon-breathe 4.2s ease-in-out infinite;
}

@keyframes panel-breathe {
  0%,
  100% {
    box-shadow:
      inset 0 0 0 1px var(--panel-border-color),
      0 0 0 rgba(0, 0, 0, 0);
  }
  50% {
    box-shadow:
      inset 0 0 0 1px var(--panel-border-color),
      0 0 18px var(--panel-breath-glow);
  }
}

@keyframes panel-corner-breathe {
  0%,
  100% {
    opacity: 0.68;
  }
  50% {
    opacity: 1;
  }
}

@keyframes kpi-icon-breathe {
  0%,
  100% {
    transform: scale(1);
    box-shadow: 0 0 0 var(--panel-icon-glow);
  }
  50% {
    transform: scale(1.035);
    box-shadow: 0 0 16px var(--panel-icon-glow);
  }
}

@media (prefers-reduced-motion: reduce) {
  .panel-card,
  .panel-card::before,
  .panel-card::after,
  .kpi-icon {
    animation: none;
  }
}

/* 全屏按钮悬停显示 */
.panel-card:hover .fullscreen-btn {
  opacity: 1;
}

/* 全屏状态样式 */
.panel-card:fullscreen {
  border-radius: 0;
}

.panel-card:fullscreen > div:last-of-type {
  padding: 20px;
}

.panel-card:fullscreen .flex-1.min-h-\[200px\] {
  min-height: 80vh;
}

/* SQL 弹窗 CodeMirror 深度样式 */
.sql-editor-body :deep(.cm-editor) {
  width: 100%;
}

.sql-editor-body :deep(.cm-scroller) {
  width: 100%;
}

.sql-editor-body :deep(.v-codemirror),
.sql-editor-body :deep(.cm-editor) {
  border: none !important;
  outline: none !important;
}

.sql-editor-body :deep(.cm-panels) {
  display: none !important;
}
</style>
