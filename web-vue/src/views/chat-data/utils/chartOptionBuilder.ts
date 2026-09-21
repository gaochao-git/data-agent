/**
 * ECharts option 共享构建器（单一权威）。
 *
 * `ResultChart.vue`（会话内查询结果）与 `ChartSpecRenderer.vue`（spec 驱动的看板/产物）
 * 共用本模块生成 option；图表样式与布局只在这里调整，两个组件不得各自手写 series 配置。
 *
 * 调用方负责：字段推断/选择（encoding）、数值安全转换（toNumber）、表格式与 KPI 的
 * 非渲染分支、ECharts 实例生命周期。
 */
import * as echarts from 'echarts'
import type { ChartType } from '../chartTypes'
import { normalizeProvinceName } from './mapRegistry'

export interface ChartEncoding {
  xField: string
  yFields: string[]
}

export interface GraphSeriesData {
  nodes: Array<Record<string, unknown>>
  links: Array<Record<string, unknown>>
}

export interface ChartOptionContext {
  type: ChartType
  rows: Array<Record<string, unknown>>
  encoding: ChartEncoding
  /** 字段显示名（spec 的 series label）；缺省用字段名。 */
  labels?: Record<string, string>
  /** graph 类型可传入预构建节点/链接（spec data 内置）；缺省由 rows 自动推断。 */
  graphData?: GraphSeriesData | null
  /** map 类型需确认地图已注册后传 true。 */
  isMapReady?: boolean
  /** 数值安全转换（调用方注入，保持与各自工具函数一致）。 */
  toNumber: (value: unknown) => number | null
}

// 12 色板：支持单图 10+ 曲线/序列不撞色（超出仍循环，但 12 条以内唯一）。
// 深浅底均友好；刻意避开紫色系与黄绿（深色看板背景上观感差）。
export const CHART_COLORS = [
  '#1677FF', '#10B981', '#F59E0B', '#EF4444', '#06B6D4', '#EC4899',
  '#64748B', '#F97316', '#0EA5E9', '#FF7043', '#26C6DA', '#FFCA28',
]

const AXIS_LABEL_COLOR = '#64748B'
const AXIS_LINE_COLOR = '#CBD5E1'
const SPLIT_LINE_COLOR = '#E2E8F0'
const MAX_X_LABELS_BEFORE_ROTATE = 8
const COMBO_MAX_Y_FIELDS = 2

const labelOf = (labels: Record<string, string> | undefined, field: string) => labels?.[field] || field

/** graph 类型：从行数据自动推断节点与链接（source/target 字段名宽容匹配）。 */
export function buildGraphSeriesData(
  rows: Array<Record<string, unknown>>,
  encoding: ChartEncoding,
  valueField: string | undefined,
  toNumber: (value: unknown) => number | null,
): GraphSeriesData {
  const nodeMap = new Map<string, number>()
  const links: Array<{ source: string; target: string; value?: number }> = []

  rows.forEach((row) => {
    const source = String(row.source ?? row.from ?? row.Source ?? row[encoding.xField] ?? '').trim()
    const target = String(row.target ?? row.to ?? row.Target ?? row[encoding.yFields[0] ?? ''] ?? '').trim()
    if (!source || !target) return
    const rawValue = valueField ? row[valueField] : row.value ?? row.weight ?? row.count
    const value = toNumber(rawValue) ?? undefined
    nodeMap.set(source, (nodeMap.get(source) || 0) + (value ?? 1))
    nodeMap.set(target, nodeMap.get(target) || 0)
    links.push({ source, target, value })
  })

  const values = Array.from(nodeMap.values())
  const maxValue = values.length ? Math.max(...values) : 1
  const nodes = Array.from(nodeMap.entries()).map(([name, value]) => ({
    name,
    value,
    symbolSize: 18 + (maxValue > 0 ? (value / maxValue) * 22 : 0),
  }))
  return { nodes, links }
}

/** 共享 option 构建：table/kpi 返回空对象（由调用方的非 ECharts 分支渲染）。 */
export function buildEChartsOption(context: ChartOptionContext): echarts.EChartsOption {
  const { type, rows, encoding, labels, toNumber } = context
  if (type === 'table' || type === 'kpi') return {}

  if (type === 'graph') {
    const graphData = context.graphData && context.graphData.nodes.length && context.graphData.links.length
      ? context.graphData
      : buildGraphSeriesData(rows, encoding, encoding.yFields[1] || undefined, toNumber)
    if (!graphData.nodes.length || !graphData.links.length) return {}
    return {
      tooltip: { trigger: 'item' },
      series: [{
        type: 'graph',
        layout: 'force',
        roam: true,
        draggable: true,
        label: { show: true, color: '#334155' },
        force: { repulsion: 180, edgeLength: [60, 160] },
        lineStyle: { color: '#94A3B8', width: 1.4, curveness: 0.18 },
        edgeSymbol: ['none', 'arrow'],
        edgeSymbolSize: 8,
        emphasis: { focus: 'adjacency' },
        data: graphData.nodes,
        links: graphData.links,
      }] as echarts.SeriesOption[],
      color: CHART_COLORS,
    }
  }

  const yFields = type === 'combo' ? encoding.yFields.slice(0, COMBO_MAX_Y_FIELDS) : encoding.yFields
  if (!rows.length || !yFields.length) return {}

  if (type === 'map') {
    const y = yFields[0] || ''
    if (!y || !context.isMapReady) return {}
    const mapData = rows.map(row => ({
      name: normalizeProvinceName(String(row[encoding.xField] ?? '')),
      value: toNumber(row[y]) ?? 0,
    }))
    const values = mapData.map(item => item.value).filter(value => value > 0)
    const minValue = values.length ? Math.min(...values) : 0
    const maxValue = values.length ? Math.max(...values) : 100
    return {
      tooltip: {
        trigger: 'item',
        // 参数类型由 EChartsOption 上下文推断（TopLevelFormatterParams）
        formatter: (params) => {
          const item = Array.isArray(params) ? params[0] : params
          if (!item) return '暂无数据'
          const name = 'name' in item ? String(item.name ?? '') : ''
          const value = 'value' in item ? item.value : undefined
          return Number.isFinite(Number(value)) ? `${name}: ${String(value)}` : `${name}: 暂无数据`
        },
      },
      visualMap: {
        min: minValue,
        max: maxValue,
        left: 'left',
        bottom: 8,
        text: ['高', '低'],
        calculable: true,
        inRange: { color: ['#E0F2FE', '#7DD3FC', '#0EA5E9', '#0369A1'] },
        textStyle: { color: AXIS_LABEL_COLOR, fontSize: 11 },
      },
      series: [{
        type: 'map',
        map: 'china',
        roam: true,
        zoom: 1.18,
        itemStyle: { areaColor: '#F8FAFC', borderColor: '#FFFFFF', borderWidth: 1 },
        emphasis: { label: { show: true }, itemStyle: { areaColor: '#FBBF24' } },
        data: mapData,
      }] as echarts.SeriesOption[],
      color: CHART_COLORS,
    }
  }

  if (type === 'pie') {
    const y = yFields[0] || ''
    if (!y) return {}
    return {
      tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
      legend: { type: 'scroll', bottom: 0, textStyle: { color: '#475569', fontSize: 11 } },
      series: [{
        name: labelOf(labels, y),
        type: 'pie',
        radius: ['42%', '68%'],
        center: ['50%', '45%'],
        itemStyle: { borderColor: '#fff', borderWidth: 2, borderRadius: 4 },
        data: rows.map(row => ({ name: String(row[encoding.xField] ?? ''), value: toNumber(row[y]) ?? 0 })),
      }] as echarts.SeriesOption[],
      color: CHART_COLORS,
    }
  }

  if (type === 'funnel') {
    const y = yFields[0] || ''
    if (!y) return {}
    return {
      tooltip: { trigger: 'item', formatter: '{b}: {c}' },
      legend: { show: false },
      series: [{
        name: labelOf(labels, y),
        type: 'funnel',
        left: '5%',
        top: 12,
        bottom: 12,
        width: '90%',
        minSize: '18%',
        maxSize: '100%',
        sort: 'descending',
        gap: 4,
        label: {
          show: true,
          position: 'inside',
          formatter: '{b}\n{c}',
          color: '#FFFFFF',
          fontSize: 12,
          fontWeight: 600,
          lineHeight: 18,
        },
        labelLine: { show: false },
        itemStyle: { borderColor: '#fff', borderWidth: 2 },
        emphasis: { label: { fontSize: 13, fontWeight: 700 } },
        data: rows.map(row => ({ name: String(row[encoding.xField] ?? ''), value: toNumber(row[y]) ?? 0 })),
      }] as echarts.SeriesOption[],
      color: CHART_COLORS,
    }
  }

  const xData = rows.map(row => String(row[encoding.xField] ?? ''))
  const isHorizontalBar = type === 'horizontal_bar'
  const isBar = type === 'bar' || isHorizontalBar
  const isCombo = type === 'combo'
  const useDualAxisCombo = isCombo && yFields.length > 1
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: isBar ? 'shadow' : 'line' } },
    legend: {
      show: yFields.length > 1,
      type: 'scroll',
      bottom: 0,
      textStyle: { color: '#475569', fontSize: 11 },
    },
    grid: {
      left: 24,
      right: useDualAxisCombo ? 42 : 16,
      top: yFields.length > 1 ? 34 : 18,
      bottom: yFields.length > 1 ? 34 : 28,
      containLabel: true,
    },
    xAxis: isHorizontalBar
      ? {
          type: 'value',
          axisLine: { show: false },
          axisTick: { show: false },
          axisLabel: { color: AXIS_LABEL_COLOR, fontSize: 11 },
          splitLine: { lineStyle: { color: SPLIT_LINE_COLOR, type: 'dashed' } },
        }
      : {
          type: 'category',
          data: xData,
          axisTick: { show: false },
          axisLine: { lineStyle: { color: AXIS_LINE_COLOR } },
          axisLabel: {
            color: AXIS_LABEL_COLOR,
            fontSize: 11,
            rotate: xData.length > MAX_X_LABELS_BEFORE_ROTATE ? 35 : 0,
            interval: 0,
            overflow: 'truncate',
            width: 80,
          },
        },
    yAxis: isHorizontalBar
      ? {
          type: 'category',
          data: xData,
          axisTick: { show: false },
          axisLine: { lineStyle: { color: AXIS_LINE_COLOR } },
          axisLabel: { color: AXIS_LABEL_COLOR, fontSize: 11 },
        }
      : useDualAxisCombo
        ? [
            {
              type: 'value',
              axisLine: { show: false },
              axisTick: { show: false },
              axisLabel: { color: AXIS_LABEL_COLOR, fontSize: 11 },
              splitLine: { lineStyle: { color: SPLIT_LINE_COLOR, type: 'dashed' } },
            },
            {
              type: 'value',
              position: 'right',
              axisLine: { show: true, lineStyle: { color: AXIS_LINE_COLOR } },
              axisTick: { show: false },
              axisLabel: { color: AXIS_LABEL_COLOR, fontSize: 11, margin: 10 },
              splitLine: { show: false },
            },
          ]
        : {
            type: 'value',
            axisLine: { show: false },
            axisTick: { show: false },
            axisLabel: { color: AXIS_LABEL_COLOR, fontSize: 11 },
            splitLine: { lineStyle: { color: SPLIT_LINE_COLOR, type: 'dashed' } },
          },
    series: yFields.map((field, index) => ({
      name: labelOf(labels, field),
      type: (isBar || (isCombo && index === 0)) ? 'bar' : 'line',
      yAxisIndex: useDualAxisCombo ? (index === 0 ? 0 : 1) : 0,
      smooth: !(isBar || (isCombo && index === 0)),
      barMaxWidth: 36,
      areaStyle: type === 'area' ? { opacity: 0.18 } : undefined,
      itemStyle: {
        color: CHART_COLORS[index % CHART_COLORS.length],
        borderRadius: isHorizontalBar ? [0, 4, 4, 0] : isBar || (isCombo && index === 0) ? [4, 4, 0, 0] : undefined,
      },
      data: rows.map(row => toNumber(row[field]) ?? 0),
      connectNulls: false,
    })) as echarts.SeriesOption[],
    color: CHART_COLORS,
  }
}
