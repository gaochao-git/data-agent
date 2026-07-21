import type { AnalysisResult, BuilderQueryResultPayload, DimensionField } from '../types'
import { getPreviousCompareKey, normalizeCompareKey } from './compareKeys'

export const toNumericValue = (value: unknown): number | null => {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : null
  }
  return null
}

export const getDefaultAnalysisSortField = (
  columns: string[],
  rows: Record<string, unknown>[] = []
) => {
  if (columns.length === 0) return ''
  const numericColumn = columns.find(column => rows.some(row => toNumericValue(row[column]) !== null))
  return numericColumn || columns[columns.length - 1] || ''
}

export const applyPeriodCompareAnalysis = (
  payload: BuilderQueryResultPayload,
  options: {
    transform: DimensionField['transform']
    compareType: 'mom' | 'yoy'
    precheckWarning?: string | null
  }
): AnalysisResult => {
  if (options.precheckWarning) {
    return { payload, warning: options.precheckWarning }
  }

  const columns = payload.columns || []
  const rows = payload.data || []
  if (columns.length < 2 || rows.length === 0) {
    return { payload, warning: '同环比结果分析需要可用的时间列和指标列，已展示原始查询结果' }
  }

  const timeCol = columns[0] as string | undefined
  const metricCol = columns[1] as string | undefined
  if (!timeCol || !metricCol) {
    return { payload, warning: '同环比结果分析需要可用的时间列和指标列，已展示原始查询结果' }
  }

  const transform = options.transform || 'raw'
  const keyedRows = rows.map((row) => {
    const compareKey = normalizeCompareKey(row[timeCol], transform)
    return { row, compareKey }
  })
  const compareMap = new Map<string, Record<string, unknown>>()
  keyedRows.forEach(({ row, compareKey }) => {
    if (compareKey) compareMap.set(compareKey, row)
  })

  const transformedRows = rows.map((row, index) => {
    const currentValue = toNumericValue(row[metricCol])
    const compareKey = keyedRows[index]?.compareKey || null
    const previousKey = compareKey ? getPreviousCompareKey(compareKey, transform, options.compareType) : null
    const previousRow = previousKey ? compareMap.get(previousKey) : null
    const previousValue = previousRow ? toNumericValue(previousRow[metricCol]) : null
    const diffValue = currentValue !== null && previousValue !== null ? currentValue - previousValue : null
    const ratioValue = diffValue !== null && previousValue ? diffValue / previousValue : null

    return {
      [timeCol]: row[timeCol],
      当前值: currentValue,
      上期值: previousValue,
      差值: diffValue,
      比率: ratioValue
    }
  })

  return {
    payload: {
      ...payload,
      columns: [timeCol, '当前值', '上期值', '差值', '比率'],
      data: transformedRows,
      total_count: transformedRows.length
    },
    warning: null
  }
}

export const applyResultSortAnalysis = (
  payload: BuilderQueryResultPayload,
  field: string,
  direction: 'ASC' | 'DESC'
): AnalysisResult => {
  const columns = payload.columns || []
  const rows = [...(payload.data || [])]
  if (!field || !columns.includes(field)) {
    return { payload, warning: '请选择有效的排序字段' }
  }

  rows.sort((left, right) => {
    const leftNumeric = toNumericValue(left[field])
    const rightNumeric = toNumericValue(right[field])
    let result = 0

    if (leftNumeric !== null && rightNumeric !== null) {
      result = leftNumeric - rightNumeric
    } else {
      result = String(left[field] ?? '').localeCompare(String(right[field] ?? ''), 'zh-CN')
    }

    return direction === 'ASC' ? result : -result
  })

  return {
    payload: {
      ...payload,
      data: rows,
      total_count: rows.length
    },
    warning: null
  }
}

export const applyResultTopNAnalysis = (
  payload: BuilderQueryResultPayload,
  field: string,
  direction: 'ASC' | 'DESC',
  size: number
): AnalysisResult => {
  const columns = payload.columns || []
  const rows = [...(payload.data || [])]
  if (!Number.isInteger(size) || size <= 0) {
    return { payload, warning: 'Top N 需要输入大于 0 的整数' }
  }
  if (!field || !columns.includes(field)) {
    return { payload, warning: '请选择 Top N 排序字段' }
  }

  rows.sort((left, right) => {
    const leftNumeric = toNumericValue(left[field])
    const rightNumeric = toNumericValue(right[field])
    let result = 0

    if (leftNumeric !== null && rightNumeric !== null) {
      result = leftNumeric - rightNumeric
    } else {
      result = String(left[field] ?? '').localeCompare(String(right[field] ?? ''), 'zh-CN')
    }

    return direction === 'ASC' ? result : -result
  })

  const sliced = rows.slice(0, size)
  return {
    payload: {
      ...payload,
      data: sliced,
      total_count: sliced.length
    },
    warning: null
  }
}

export const applyResultTotalAnalysis = (payload: BuilderQueryResultPayload): AnalysisResult => {
  const columns = payload.columns || []
  const rows = payload.data || []
  if (!columns.length || !rows.length) {
    return { payload, warning: '当前结果没有可合计的数据' }
  }

  const summaryRow: Record<string, unknown> = {}
  let hasNumericColumn = false

  columns.forEach((column, index) => {
    const numericValues = rows
      .map(row => toNumericValue(row[column]))
      .filter((value): value is number => value !== null)

    if (numericValues.length > 0) {
      hasNumericColumn = true
      summaryRow[column] = numericValues.reduce((sum, value) => sum + value, 0)
      return
    }

    summaryRow[column] = index === 0 ? '合计' : null
  })

  if (!hasNumericColumn) {
    return { payload, warning: '当前结果没有可合计的数值列' }
  }

  const mergedRows = [...rows, summaryRow]
  return {
    payload: {
      ...payload,
      data: mergedRows,
      total_count: mergedRows.length
    },
    warning: null
  }
}

export const applyResultPercentAnalysis = (
  payload: BuilderQueryResultPayload,
  metricColumn: string,
  scope: 'total' | 'group',
  groupColumn?: string
): AnalysisResult => {
  const columns = payload.columns || []
  const rows = payload.data || []
  if (!columns.length || !rows.length) {
    return { payload, warning: '当前结果没有可计算百分比的数据' }
  }

  if (!metricColumn || !columns.includes(metricColumn)) {
    return { payload, warning: '请选择需要计算百分比的指标列' }
  }

  const hasNumericValues = rows.some(row => toNumericValue(row[metricColumn]) !== null)
  if (!hasNumericValues) {
    return { payload, warning: '所选指标列不是数值列，无法计算百分比' }
  }

  if (scope === 'group' && (!groupColumn || !columns.includes(groupColumn))) {
    return { payload, warning: '请选择分组维度后再计算组内占比' }
  }

  const totalsByGroup = new Map<string, number>()
  const total = rows.reduce((sum, row) => {
    const value = toNumericValue(row[metricColumn]) || 0
    if (scope === 'group') {
      const groupKey = String(row[groupColumn as string] ?? '')
      totalsByGroup.set(groupKey, (totalsByGroup.get(groupKey) || 0) + value)
    }
    return sum + value
  }, 0)

  if (scope === 'total' && !total) {
    return { payload, warning: '当前结果总计为 0，无法计算百分比' }
  }
  if (scope === 'group' && Array.from(totalsByGroup.values()).every(value => !value)) {
    return { payload, warning: '当前分组总计为 0，无法计算百分比' }
  }

  const percentColumn = scope === 'group'
    ? `${metricColumn}_组内占比`
    : `${metricColumn}_百分比`
  const transformedRows = rows.map((row) => {
    const value = toNumericValue(row[metricColumn])
    const denominator = scope === 'group'
      ? totalsByGroup.get(String(row[groupColumn as string] ?? '')) || 0
      : total
    return {
      ...row,
      [percentColumn]: value === null || !denominator ? null : value / denominator
    }
  })

  return {
    payload: {
      ...payload,
      columns: [...columns, percentColumn],
      data: transformedRows,
      total_count: transformedRows.length
    },
    warning: null
  }
}

export const applyResultCompareAnalysis = (
  payload: BuilderQueryResultPayload,
  metricColumn: string,
  baselineMode: 'first' | 'max' | 'min' | 'avg'
): AnalysisResult => {
  const columns = payload.columns || []
  const rows = payload.data || []
  if (!columns.length || !rows.length) {
    return { payload, warning: '当前结果没有可对比的数据' }
  }
  if (!metricColumn || !columns.includes(metricColumn)) {
    return { payload, warning: '请选择需要对比的指标列' }
  }

  const numericValues = rows
    .map(row => toNumericValue(row[metricColumn]))
    .filter((value): value is number => value !== null)

  if (!numericValues.length) {
    return { payload, warning: '所选指标列不是数值列，无法进行对比' }
  }

  let baselineValue: number | null = null
  if (baselineMode === 'first') {
    baselineValue = toNumericValue(rows[0]?.[metricColumn]) ?? null
  } else if (baselineMode === 'max') {
    baselineValue = Math.max(...numericValues)
  } else if (baselineMode === 'min') {
    baselineValue = Math.min(...numericValues)
  } else {
    baselineValue = numericValues.reduce((sum, value) => sum + value, 0) / numericValues.length
  }

  if (baselineValue === null) {
    return { payload, warning: '基准值为空，无法进行对比' }
  }

  const baselineColumn = `${metricColumn}_基准值`
  const diffColumn = `${metricColumn}_差值`
  const ratioColumn = `${metricColumn}_差异率`
  const transformedRows = rows.map((row) => {
    const value = toNumericValue(row[metricColumn])
    const diffValue = value === null ? null : value - baselineValue!
    return {
      ...row,
      [baselineColumn]: baselineValue,
      [diffColumn]: diffValue,
      [ratioColumn]: value === null || !baselineValue ? null : diffValue! / baselineValue
    }
  })

  return {
    payload: {
      ...payload,
      columns: [...columns, baselineColumn, diffColumn, ratioColumn],
      data: transformedRows,
      total_count: transformedRows.length
    },
    warning: null
  }
}
