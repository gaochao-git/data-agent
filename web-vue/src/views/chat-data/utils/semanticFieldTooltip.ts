/**
 * 语义模型字段/指标 tooltip 的唯一权威构造入口。
 *
 * 背景：会话侧栏（ConversationSidebar）与可视化查询侧栏（SemanticDatasetPanel）
 * 曾各自复制一份 buildDimensionTooltip / buildMetricTooltip，导致新增派生/复合指标
 * 时只更新了 builder 侧栏、漏改会话侧栏，出现"数据智能体看不到派生口径"的漂移。
 * 这里收敛为单一实现，两个入口只在编排层注入 context（表结构 + 字段字典解析）。
 */
import type {
  DatasourceColumnDictInfo,
  SemanticDimension,
  SemanticMetric,
  TableInfo,
} from '../types'
import {
  buildFieldDictSection,
  createTooltipSection,
  extractMetricFieldName,
  type FieldTooltipSection,
} from './fieldDict'

export interface SemanticTooltipContext {
  tables: TableInfo[]
  /** 解析某表某列的字段字典；调用方负责合并缓存结果与表内联字典。 */
  resolveColumnDict: (tableName?: string, columnName?: string) => DatasourceColumnDictInfo | null | undefined
}

/** 派生/复合指标为逻辑口径，无物理表/表达式。 */
export const isLogicalMetric = (metric: SemanticMetric): boolean =>
  metric.metric_kind === 'derived' || metric.metric_kind === 'composite'

/**
 * 指标前导图标配色（单一来源，两个侧栏共用）：
 * 原子=蓝、派生=青、复合=橙，便于在指标列表里一眼区分物理聚合与逻辑口径。
 */
export const metricIconColorClass = (metric: SemanticMetric): string => {
  if (metric.metric_kind === 'composite') return 'text-[#D97706]'
  if (metric.metric_kind === 'derived') return 'text-[#0D9488]'
  return 'text-[#165DFF]'
}

/** 派生指标口径文案：基础指标 · 统计周期 · 修饰词过滤。 */
export const derivedMetricVerbalization = (metric: SemanticMetric): string => {
  const parts: string[] = [`指标[${metric.base_metric || '-'}]`]
  const period = metric.period
  if (period?.kind) {
    const kindLabels: Record<string, string> = {
      today: '今日',
      yesterday: '昨日',
      this_week: '本周',
      this_month: '本月',
      last_n_days: `近${period.n || 7}天`,
      between: `${period.start || '?'}~${period.end || '?'}`,
    }
    parts.push(kindLabels[period.kind] || period.kind)
  }
  for (const filter of metric.filters || []) {
    if (filter.dimension) {
      parts.push(`${filter.dimension}${filter.operator || '='}${filter.value ?? ''}`)
    }
  }
  return parts.join(' · ')
}

const findTable = (ctx: SemanticTooltipContext, tableName?: string) => {
  const normalized = String(tableName || '').trim()
  if (!normalized) return undefined
  return ctx.tables.find(item => item.table_name === normalized)
}

const getTableRoleLabel = (table?: string, alias?: string): string => {
  const tableName = String(table || '').trim()
  const tableAlias = String(alias || tableName).trim()
  if (!tableName) return tableAlias || '-'
  if (!tableAlias || tableAlias === tableName) return tableName
  return `${tableAlias}（${tableName}）`
}

const getTableDescription = (ctx: SemanticTooltipContext, tableName?: string): string => {
  if (!tableName) return '-'
  return findTable(ctx, tableName)?.description || '-'
}

const buildTableBusinessDescLines = (ctx: SemanticTooltipContext, tableName?: string): string[] => {
  const businessDesc = findTable(ctx, tableName)?.business_desc || ''
  return businessDesc ? [`表业务说明：${businessDesc}`] : []
}

const buildMetadataSupplementSection = (metadataSupplement?: string): FieldTooltipSection | null => {
  const content = String(metadataSupplement || '').trim()
  if (!content) return null
  return createTooltipSection('元数据补充', content.split(/\n+/).map(item => item.trim()).filter(Boolean))
}

export const buildDimensionTooltip = (
  dimension: SemanticDimension,
  ctx: SemanticTooltipContext,
): FieldTooltipSection[] => {
  const expression = dimension.expression || `\`${dimension.column}\``
  const fieldDict = ctx.resolveColumnDict(dimension.table, dimension.column)
  return [
    createTooltipSection('表信息', [
      `数据源表：${getTableRoleLabel(dimension.table, dimension.table_alias)}`,
      `数据表备注：${getTableDescription(ctx, dimension.table)}`,
      ...buildTableBusinessDescLines(ctx, dimension.table),
    ]),
    createTooltipSection('字段信息', [
      `显示名称：${dimension.name || '-'}`,
      `字段名称：${dimension.column || '-'}`,
      `表达式：${expression}`,
      dimension.description ? `描述：${dimension.description}` : '',
    ]),
    buildFieldDictSection(fieldDict),
    buildMetadataSupplementSection(dimension.metadata_supplement),
  ].filter(Boolean) as FieldTooltipSection[]
}

export const buildMetricTooltip = (
  metric: SemanticMetric,
  ctx: SemanticTooltipContext,
): FieldTooltipSection[] => {
  if (isLogicalMetric(metric)) {
    const definition = metric.metric_kind === 'composite'
      ? (metric.formula || '-')
      : derivedMetricVerbalization(metric)
    return [
      createTooltipSection('扩展指标', [
        `类型：${metric.metric_kind === 'composite' ? '复合指标' : '派生指标'}`,
        `口径：${definition}`,
        metric.description ? `备注：${metric.description}` : '',
      ].filter(Boolean)),
    ].filter(Boolean) as FieldTooltipSection[]
  }
  const expression = metric.expression || ''
  const fieldName = extractMetricFieldName(metric)
  const fieldDict = ctx.resolveColumnDict(metric.table, fieldName)
  return [
    createTooltipSection('表信息', [
      `数据源表：${getTableRoleLabel(metric.table, metric.table_alias)}`,
      `数据表备注：${getTableDescription(ctx, metric.table)}`,
      ...buildTableBusinessDescLines(ctx, metric.table),
    ]),
    createTooltipSection('字段信息', [
      `显示名称：${metric.name || '-'}`,
      `字段名称：${fieldName || '-'}`,
      `表达式：${expression || '-'}`,
      metric.description ? `描述：${metric.description}` : '',
    ]),
    buildFieldDictSection(fieldDict),
    buildMetadataSupplementSection(metric.metadata_supplement),
  ].filter(Boolean) as FieldTooltipSection[]
}
