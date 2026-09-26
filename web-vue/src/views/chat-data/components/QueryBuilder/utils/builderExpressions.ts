/**
 * QueryBuilder 纯函数与常量（自 index.vue 抽出，无组件耦合）。
 */
import type { BuilderConfig, BuilderFilterNode } from '../../QuickQueries.vue'
import type {
  AggregationField,
  AdvancedFilterGroup,
  AdvancedFilterLeaf,
  ColumnInfo,
  DimensionField,
  FilterLogic,
} from '../types'
import { createNodeId } from './advancedFilterTree'

// ============ 聚合函数 ============

export const aggFunctions = [
  { value: 'RAW', label: '原始值' },
  { value: 'COUNT', label: '计数' },
  { value: 'SUM', label: '求和' },
  { value: 'AVG', label: '平均' },
  { value: 'MAX', label: '最大' },
  { value: 'MIN', label: '最小' },
  { value: 'COUNT_DISTINCT', label: '去重计数' }
]

export const normalizeAggFunc = (func?: string): AggregationField['func'] => {
  const upper = String(func || 'RAW').toUpperCase()
  if (upper === 'COUNT' || upper === 'SUM' || upper === 'AVG' || upper === 'MAX' || upper === 'MIN' || upper === 'COUNT_DISTINCT' || upper === 'RAW') {
    return upper as AggregationField['func']
  }
  return 'RAW'
}

export const isRawAggFunc = (func?: string) => normalizeAggFunc(func) === 'RAW'

export const AGG_EXPR_PATTERN = /^(COUNT|SUM|AVG|MAX|MIN)\((DISTINCT\s+)?(.+)\)$/i

export const getAggFuncFromExpression = (expression?: string): AggregationField['func'] | undefined => {
  const expr = (expression || '').trim()
  if (!expr) return undefined
  const matched = expr.match(AGG_EXPR_PATTERN)
  if (!matched) return undefined
  const func = (matched[1] || '').toUpperCase()
  const hasDistinct = !!(matched[2] || '').trim()
  if (func === 'COUNT' && hasDistinct) return 'COUNT_DISTINCT'
  if (func === 'COUNT' || func === 'SUM' || func === 'AVG' || func === 'MAX' || func === 'MIN') {
    return func as AggregationField['func']
  }
  return undefined
}

export const parseMetricExpressionMeta = (expression?: string) => {
  const expr = (expression || '').trim()
  const normalizeColumn = (raw: string) => {
    const cleaned = (raw || '').replace(/`/g, '').trim()
    if (!cleaned) return '*'
    const parts = cleaned.split('.')
    return parts[parts.length - 1] || '*'
  }
  const distinctMatch = expr.match(/^COUNT\s*\(\s*DISTINCT\s+([^)]+)\s*\)$/i)
  if (distinctMatch) {
    return { func: 'COUNT_DISTINCT' as AggregationField['func'], column: normalizeColumn(distinctMatch[1] || '') }
  }
  const aggMatch = expr.match(/^(COUNT|SUM|AVG|MAX|MIN)\s*\(\s*([^)]+)\s*\)$/i)
  if (aggMatch) {
    const fn = (aggMatch[1] || 'COUNT').toUpperCase() as AggregationField['func']
    const raw = (aggMatch[2] || '').trim()
    const col = raw === '*' ? '*' : normalizeColumn(raw)
    return { func: fn, column: col || '*' }
  }
  const fallbackCol = normalizeColumn(expr)
  return { func: 'RAW' as AggregationField['func'], column: fallbackCol }
}

export const parseFirstQualifiedFromExpression = (expression?: string): { table: string; column: string } | null => {
  const expr = (expression || '').trim()
  if (!expr) return null
  const matched = expr.match(/`?([\w\u4e00-\u9fa5]+)`?\.`?([\w\u4e00-\u9fa5]+)`?/)
  if (!matched) return null
  return { table: matched[1] || '', column: matched[2] || '' }
}

export const getAggFuncLabel = (func?: string) => {
  const key = String(func || '').toUpperCase()
  return aggFunctions.find(item => item.value === key)?.label || key
}

export const isLogicalAggregation = (a: AggregationField) => a.metricKind === 'derived' || a.metricKind === 'composite'

// ============ 字段展示名 ============

export const stripFieldPrefix = (name?: string) => {
  const text = (name || '').trim()
  const idx = text.lastIndexOf('.')
  return idx > 0 ? text.slice(idx + 1) : text
}

export const getDimensionDisplayName = (d: DimensionField) => {
  return stripFieldPrefix(d.displayName || d.label || d.column) || d.column
}

export const getMetricDisplayName = (a: AggregationField) => {
  return stripFieldPrefix(a.displayName || a.label || a.column) || a.column
}

// 逻辑口径指标（派生/复合）没有聚合函数概念，chip 显示不带函数前缀
export const formatMetricChipLabel = (a: AggregationField) => {
  const name = getMetricDisplayName(a)
  return isLogicalAggregation(a) ? name : `${getAggFuncLabel(a.func)}(${name})`
}

// ============ 运算符 ============

export const operators = [
  { value: '=', label: '等于' },
  { value: '!=', label: '不等于' },
  { value: '>', label: '大于' },
  { value: '>=', label: '大于等于' },
  { value: '<', label: '小于' },
  { value: '<=', label: '小于等于' },
  { value: 'BETWEEN', label: '介于' },
  { value: 'STARTS WITH', label: '开头为' },
  { value: 'ENDS WITH', label: '结尾为' },
  { value: 'EMPTY STRING', label: '为空字符串' },
  { value: 'IS NULL', label: '为空' },
  { value: 'IS NOT NULL', label: '不为空' }
]

export const fieldRefOperators = [
  { value: '=', label: '等于' },
  { value: '!=', label: '不等于' },
  { value: '>', label: '大于' },
  { value: '>=', label: '大于等于' },
  { value: '<', label: '小于' },
  { value: '<=', label: '小于等于' }
]

export const listOperators = [
  { value: 'IN', label: '包含' },
  { value: 'NOT IN', label: '不包含' }
]

export const isListOperator = (operator: string) => operator === 'IN' || operator === 'NOT IN'

export const getOperatorLabel = (operator: string) => {
  const allOps = [...operators, ...fieldRefOperators, ...listOperators]
  return allOps.find(op => op.value === operator)?.label || operator
}

// ============ 日期 ============

export const startOfDay = (date: Date) => new Date(date.getFullYear(), date.getMonth(), date.getDate())
export const shiftDays = (base: Date, offset: number) => {
  const shifted = new Date(base)
  shifted.setDate(shifted.getDate() + offset)
  return shifted
}
export const monthStart = (date: Date) => new Date(date.getFullYear(), date.getMonth(), 1)
export const monthEnd = (date: Date) => new Date(date.getFullYear(), date.getMonth() + 1, 0)

export const fixedDateShortcuts = [
  {
    text: '今天',
    value: () => {
      const today = startOfDay(new Date())
      return [today, today]
    }
  },
  {
    text: '昨天',
    value: () => {
      const yesterday = startOfDay(shiftDays(new Date(), -1))
      return [yesterday, yesterday]
    }
  },
  {
    text: '最近7天',
    value: () => [startOfDay(shiftDays(new Date(), -6)), startOfDay(new Date())]
  },
  {
    text: '最近30天',
    value: () => [startOfDay(shiftDays(new Date(), -29)), startOfDay(new Date())]
  },
  {
    text: '本月',
    value: () => [startOfDay(monthStart(new Date())), startOfDay(new Date())]
  },
  {
    text: '上月',
    value: () => {
      const now = new Date()
      const lastMonth = new Date(now.getFullYear(), now.getMonth() - 1, 1)
      return [startOfDay(monthStart(lastMonth)), startOfDay(monthEnd(lastMonth))]
    }
  }
]

export const singleDateShortcuts = [
  { text: '今天', value: () => startOfDay(new Date()) },
  { text: '昨天', value: () => startOfDay(shiftDays(new Date(), -1)) },
  { text: '7天前', value: () => startOfDay(shiftDays(new Date(), -7)) },
  { text: '月初', value: () => startOfDay(monthStart(new Date())) },
  { text: '月末', value: () => startOfDay(monthEnd(new Date())) }
]

export interface DateQuickPreset {
  label: string
  operator: string
  expression: string
}

export const dateQuickPresets: DateQuickPreset[] = [
  { label: '最近 1 天', operator: '>=', expression: 'DATE_SUB(CURRENT_DATE, INTERVAL 1 DAY)' },
  { label: '最近 7 天', operator: '>=', expression: 'DATE_SUB(CURRENT_DATE, INTERVAL 7 DAY)' },
  { label: '最近 30 天', operator: '>=', expression: 'DATE_SUB(CURRENT_DATE, INTERVAL 30 DAY)' },
  { label: '本月', operator: '>=', expression: "DATE_FORMAT(CURRENT_DATE, '%Y-%m-01')" },
  { label: '上月', operator: 'BETWEEN', expression: "DATE_FORMAT(DATE_SUB(CURRENT_DATE, INTERVAL 1 MONTH), '%Y-%m-01') AND LAST_DAY(DATE_SUB(CURRENT_DATE, INTERVAL 1 MONTH))" },
  { label: '今年', operator: '>=', expression: "DATE_FORMAT(CURRENT_DATE, '%Y-01-01')" },
]

// ============ 其他 ============

// 日期转换选项
export const transformOptions = [
  { value: 'raw', label: '原始值' },
  { value: 'year', label: '按年' },
  { value: 'quarter', label: '按季度' },
  { value: 'month', label: '按月' },
  { value: 'week', label: '按周' },
  { value: 'day', label: '按日' }
]

export const normalizeOptionalInteger = (value: unknown): number | null => {
  if (value === null || value === undefined || value === '') return null
  if (typeof value !== 'number' && typeof value !== 'string') return null
  const normalizedValue = typeof value === 'string' ? value.trim() : value
  if (normalizedValue === '') return null
  const num = Number(normalizedValue)
  return Number.isInteger(num) ? num : null
}

// 根据字段类型返回图标类型（DataWind 风格）
export const getFieldType = (col: ColumnInfo): 'string' | 'number' | 'date' => {
  const t = (col.mysql_type || col.type || '').toLowerCase()
  if (/int|float|double|decimal|numeric|number|bigint|tinyint|smallint|mediumint/.test(t)) return 'number'
  if (/date|time|timestamp|datetime|year/.test(t)) return 'date'
  return 'string'
}

export const normalizeBuilderErrorMessage = (raw: unknown, fallback: string) => {
  const text = String(raw || '').trim()
  if (!text) return fallback
  if (text.includes('不能混用明细过滤和结果过滤')) {
    return '组合筛选配置冲突：同一筛选组内不能同时使用明细过滤和结果过滤，请在组合筛选弹窗中调整字段类型'
  }
  return text
}

// 提取后端统一响应中的错误信息（msg 优先，其次 detail）
export const getApiErrorDetail = (err: unknown): string => {
  const data = (err as { response?: { data?: { msg?: string; detail?: string } } } | null)?.response?.data
  return data?.msg || data?.detail || ''
}

export const getBuilderSemanticConfigId = (config: BuilderConfig | null | undefined) => {
  return String(config?.semantic_config_id || config?.semanticConfigId || '').trim()
}

export const getBuilderSemanticConfigName = (config: BuilderConfig | null | undefined) => {
  return String(config?.semantic_config_name || config?.semanticConfigName || '').trim()
}

export const parseAdvancedFilterFromConfig = (node: BuilderFilterNode | null | undefined): AdvancedFilterGroup | AdvancedFilterLeaf | null => {
  if (!node || typeof node !== 'object') return null
  if (Array.isArray(node.and) || Array.isArray(node.or)) {
    const logic: FilterLogic = Array.isArray(node.and) ? 'AND' : 'OR'
    const source = node[logic === 'AND' ? 'and' : 'or'] || []
    const children = source
      .map((child) => parseAdvancedFilterFromConfig(child))
      .filter((child): child is AdvancedFilterLeaf | AdvancedFilterGroup => Boolean(child))
    return {
      kind: 'group',
      id: createNodeId(),
      logic,
      children
    }
  }
  if (!node.table || !node.column) return null
  return {
    kind: 'leaf',
    id: createNodeId(),
    filter: {
      id: node.id || createNodeId(),
      table: node.table,
      tableAlias: node.tableAlias || node.table,
      column: node.column,
      semanticName: node.semanticName,
      displayName: node.displayName,
      operator: node.operator || '=',
      value: node.value || '',
      valueType: node.valueType || 'literal',
      rightTable: node.rightTable,
      rightTableAlias: node.rightTableAlias || node.rightTable,
      rightColumn: node.rightColumn,
      rightExpression: node.rightExpression,
      logic: node.logic || 'AND',
      expression: node.expression,
      aggFunc: node.aggFunc,
      aggId: node.aggId
    }
  }
}
