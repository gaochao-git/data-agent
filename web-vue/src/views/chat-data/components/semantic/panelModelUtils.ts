/**
 * SemanticConfigPanel 领域纯函数与类型（自 SemanticConfigPanel.vue 抽出）。
 * 仅容纳不依赖组件实例（props/refs/computed）的逻辑；需要组件上下文的函数
 * 通过显式 deps 参数注入，保持可单测。
 */
import { SQL_EXPRESSION_FUNCTION_KEYWORDS, quoteIdentifier } from '../../utils'
import type {
  SemanticConfigItem,
  SemanticDimension,
  SemanticMetric,
  SemanticRelationship,
  TableInfo,
} from '../../types'

// ============ 类型（组件与工具模块共用，单一来源在此） ============

export type SemanticLogicalType = NonNullable<SemanticDimension['logical_type']>

export interface TableScopeEntry {
  table: string
  alias: string
}

export interface FieldConfigRow {
  key: string
  table: string
  tableAlias: string
  column: string
  sourceColumn?: string
  columnType: string
  displayName: string
  columnComment?: string
  description?: string
  metadataSupplement?: string
  logicalType: SemanticLogicalType
  expression: string
  /** 是否参与维度：未勾选行仅作占位展示，应用字段设置时不写入 dimensions */
  isDimension: boolean
  isCustom?: boolean
  isMissingPhysical?: boolean
}

// ============ SQL 别名 / 作用域 ============

export const normalizeSqlAliasBase = (tableName: string) => {
  const normalized = String(tableName || '').trim().replace(/[^\w]/g, '_').replace(/_+/g, '_').replace(/^_+|_+$/g, '')
  const base = normalized || 'table'
  return /^[A-Za-z_]/.test(base) ? base : `t_${base}`
}

export const isNumericPhysicalTableName = (tableName: string) => /^\d+$/.test(String(tableName || '').trim())

export const toLetterSuffix = (index: number) => {
  let value = Math.max(1, index)
  let suffix = ''
  while (value > 0) {
    value -= 1
    suffix = String.fromCharCode(97 + (value % 26)) + suffix
    value = Math.floor(value / 26)
  }
  return suffix
}

export const generateScopeAlias = (tableName: string, entries: TableScopeEntry[]) => {
  const base = normalizeSqlAliasBase(tableName)
  const usedAliases = new Set(entries.map(item => item.alias))
  if (!usedAliases.has(base)) return base
  if (isNumericPhysicalTableName(tableName)) {
    let index = 1
    let candidate = `${base}_${toLetterSuffix(index)}`
    while (usedAliases.has(candidate)) {
      index += 1
      candidate = `${base}_${toLetterSuffix(index)}`
    }
    return candidate
  }
  let index = 2
  while (usedAliases.has(`${base}_${index}`)) {
    index += 1
  }
  return `${base}_${index}`
}

export const getScopeAlias = (entry: TableScopeEntry | string | undefined | null) => {
  if (!entry) return ''
  if (typeof entry === 'string') return entry
  return entry.alias || entry.table
}

export const getScopePhysicalTable = (entry: TableScopeEntry | string | undefined | null) => {
  if (!entry) return ''
  if (typeof entry === 'string') return entry
  return entry.table
}

export const normalizeScopeEntries = (entries: Array<TableScopeEntry | string>) => {
  const seenAliases = new Set<string>()
  const result: TableScopeEntry[] = []
  entries.forEach(item => {
    const table = getScopePhysicalTable(item).trim()
    const alias = (getScopeAlias(item) || table).trim()
    if (!table || !alias || seenAliases.has(alias)) return
    seenAliases.add(alias)
    result.push({ table, alias })
  })
  return result
}

export const getFieldTableAlias = (item: { table: string; table_alias?: string }) => {
  return String(item.table_alias || item.table || '').trim()
}

export const getRelationshipLeftAlias = (rel: SemanticRelationship) => String(rel.left_alias || rel.left_table || '').trim()
export const getRelationshipRightAlias = (rel: SemanticRelationship) => String(rel.right_alias || rel.right_table || '').trim()

export const deriveScopeFromConfig = (config: SemanticConfigItem) => {
  const entries: TableScopeEntry[] = []
  const addEntry = (table: string, alias?: string) => {
    const physical = String(table || '').trim()
    const effectiveAlias = String(alias || physical).trim()
    if (!physical || !effectiveAlias || entries.some(item => item.alias === effectiveAlias)) return
    entries.push({ table: physical, alias: effectiveAlias })
  }
  config.dimensions.forEach(item => addEntry(item.table, getFieldTableAlias(item)))
  config.metrics.forEach(item => addEntry(item.table, getFieldTableAlias(item)))
  config.relationships.forEach(item => {
    addEntry(item.left_table, getRelationshipLeftAlias(item))
    addEntry(item.right_table, getRelationshipRightAlias(item))
  })
  return entries
}

export const isFieldRowInScope = (row: Pick<FieldConfigRow, 'table' | 'tableAlias'>, scopeEntries: TableScopeEntry[]) => {
  const alias = getFieldRowAlias(row)
  return scopeEntries.some(entry => entry.alias === alias && entry.table === row.table)
}

export const canManageConfig = (config?: SemanticConfigItem | null) => {
  if (!config) return false
  return typeof config.can_manage === 'boolean' ? config.can_manage : true
}

// ============ 展示文案 ============

export const formatConfigOperator = (value?: string) => {
  const text = String(value || '').trim()
  return text || '未知'
}

export const getConfigCreatorName = (config: SemanticConfigItem) => {
  return formatConfigOperator(config.create_by_name)
}

export const getConfigUpdaterName = (config: SemanticConfigItem) => {
  return formatConfigOperator(config.update_by_name)
}

export const buildConfigMaintainerTitle = (config: SemanticConfigItem) => {
  const creator = getConfigCreatorName(config)
  const updater = getConfigUpdaterName(config)
  const createTime = config.create_time || '-'
  const updateTime = config.update_time || '-'
  return `创建人：${creator}\n创建时间：${createTime}\n最近维护人：${updater}\n维护时间：${updateTime}`
}

export const formatJoinTypeLabel = (joinType?: string) => {
  if (joinType === 'inner') return '内连接'
  if (joinType === 'right') return '右连接'
  return '左连接'
}

export const formatRelationshipTypeLabel = (relationshipType?: string) => {
  if (relationshipType === 'one_to_many') return '一对多'
  if (relationshipType === 'many_to_one') return '多对一'
  return '一对一'
}

export const buildConfigSignature = (config: SemanticConfigItem) => {
  return JSON.stringify({
    name: config.name || '',
    description: config.description || '',
    business_notes: config.business_notes || '',
    enabled: !!config.enabled,
    metrics: config.metrics || [],
    dimensions: config.dimensions || [],
    relationships: config.relationships || [],
  })
}

export const LOGICAL_TYPE_OPTIONS: Array<{ value: SemanticLogicalType; label: string }> = [
  { value: 'text', label: '文本' },
  { value: 'number', label: '数值' },
  { value: 'date', label: '日期' },
  { value: 'datetime', label: '日期时间' },
  { value: 'enum', label: '枚举' },
]

// ============ 表达式解析 ============

export const normalizeExpressionColumn = (value: string) => {
  const cleaned = (value || '').trim().replace(/`/g, '')
  if (!cleaned) return ''
  const parts = cleaned.split('.')
  return parts[parts.length - 1] || ''
}

export const isCountAllExpression = (expression: string) => {
  return ['COUNT(*)', 'COUNT(1)'].includes((expression || '').replace(/\s+/g, '').toUpperCase())
}

export const expressionUsesColumn = (expression: string, column: string) => {
  const expr = (expression || '').trim().replace(/`/g, '')
  const col = (column || '').trim()
  if (!expr || !col) return false

  const escapedCol = col.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const directPattern = new RegExp(`(^|[^A-Za-z0-9_])(?:[A-Za-z_][A-Za-z0-9_]*\\.)?${escapedCol}([^A-Za-z0-9_]|$)`, 'i')
  if (directPattern.test(expr)) return true

  const aggMatch = expr.match(/^(COUNT|SUM|AVG|MAX|MIN)\((DISTINCT\s+)?(.+)\)$/i)
  if (aggMatch?.[3]) {
    return normalizeExpressionColumn(aggMatch[3]) === col
  }

  return normalizeExpressionColumn(expr) === col
}

export const isRawColumnExpression = (expression: string | undefined, column: string) => {
  const expr = (expression || '').trim().replace(/`/g, '').replace(/\s+/g, '')
  const col = (column || '').trim()
  if (!col) return false
  if (!expr) return true
  const escapedCol = col.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  return new RegExp(`^(?:[A-Za-z_][A-Za-z0-9_]*\\.)?${escapedCol}$`, 'i').test(expr)
}

export const AGG_DISPLAY_SUFFIX_MAP: Record<string, string> = {
  COUNT: '数量',
  SUM: '合计',
  AVG: '平均值',
  MAX: '最大值',
  MIN: '最小值',
}

export const EXPANDABLE_AGG_FUNCS = ['SUM', 'AVG', 'MAX', 'MIN']

export const parseAggFuncFromExpression = (expression: string) => {
  const match = (expression || '').trim().match(/^(COUNT|SUM|AVG|MAX|MIN)\((DISTINCT\s+)?(.+)\)$/i)
  if (!match) return 'COUNT'
  if (match[1]?.toUpperCase() === 'COUNT' && match[2]) return 'COUNT_DISTINCT'
  return (match[1] || 'COUNT').toUpperCase()
}

export const parseSimpleMetricExpression = (expression: string) => {
  const match = (expression || '').trim().match(/^(COUNT|SUM|AVG|MAX|MIN)\(\s*(?:`?([A-Za-z_][A-Za-z0-9_]*)`?|`?([A-Za-z_][A-Za-z0-9_]*)`?\.`?([A-Za-z_][A-Za-z0-9_]*)`?)\s*\)$/i)
  if (!match) return null
  return {
    aggFunc: (match[1] || 'COUNT').toUpperCase(),
    column: match[4] || match[2] || '',
  }
}

export const extractExpressionColumns = (expression: string) => {
  const text = (expression || '').trim()
  if (!text) return []
  const keywords = new Set([
    'case', 'when', 'then', 'else', 'end', 'filter', 'where', 'distinct', 'count', 'sum', 'avg', 'max', 'min',
    'and', 'or', 'not', 'null', 'is', 'in', 'as', 'on', 'if', 'cast', 'char', 'date', 'date_format', 'str_to_date',
    'unix_timestamp', 'from_unixtime', 'year', 'month', 'day',
    ...SQL_EXPRESSION_FUNCTION_KEYWORDS,
  ])
  const result = new Set<string>()
  const pattern = /`([^`]+)`|(?<!['"])\b([A-Za-z_][A-Za-z0-9_]*)\b(?!['"])/g
  let match: RegExpExecArray | null
  while ((match = pattern.exec(text)) !== null) {
    if (text.slice(match.index + match[0].length).trimStart().startsWith('.')) continue
    const isQuotedIdentifier = Boolean(match[1])
    const token = (match[1] || match[2] || '').trim().split('.').pop() || ''
    if (!token || (!isQuotedIdentifier && keywords.has(token.toLowerCase()))) continue
    result.add(token)
  }
  return Array.from(result)
}

export const isNumericColumnType = (columnType: string) => {
  return /int|float|double|decimal|numeric|number|bigint|tinyint|smallint|mediumint/i.test(columnType || '')
}

export const inferLogicalType = (columnType: string): SemanticLogicalType => {
  const normalized = (columnType || '').toLowerCase()
  if (/timestamp|datetime/.test(normalized)) return 'datetime'
  if (/date|year/.test(normalized)) return 'date'
  if (isNumericColumnType(normalized)) return 'number'
  return 'text'
}

export const getMetricDataType = (columnType: string) => {
  const normalized = (columnType || '').trim()
  if (!normalized) return undefined
  return normalized
}

// 原子指标的物理列从表达式解析（AGG(col) / AGG(DISTINCT col)），复杂表达式返回空
export const atomicMetricColumn = (metric: SemanticMetric) => {
  const parsed = parseSimpleMetricExpression(metric.expression || '')
  return normalizeExpressionColumn(parsed?.column || metric.expression || '')
}

export const buildMetricExpandDisplayName = (baseName: string, aggFunc: string) => {
  const normalizedBase = (baseName || '').trim()
  const suffix = AGG_DISPLAY_SUFFIX_MAP[aggFunc] || aggFunc
  if (!normalizedBase) return suffix
  if (normalizedBase.endsWith(suffix)) return normalizedBase
  return `${normalizedBase}${suffix}`
}

// ============ 字段行键 / 显示名 ============

export const getFieldRowAlias = (row: Pick<FieldConfigRow, 'table' | 'tableAlias'>) => row.tableAlias || row.table

export const getFieldPhysicalColumn = (row: Pick<FieldConfigRow, 'sourceColumn' | 'column'>) => String(row.sourceColumn || row.column || '').trim()

export const buildFieldRowKey = (tableAlias: string, column: string, suffix = '') => {
  const base = `${tableAlias}.${column}`
  return suffix ? `${base}${suffix}` : base
}

export const buildTablePrefixedDisplayName = (tableName: string, displayName: string) => {
  const normalizedTable = (tableName || '').trim()
  let normalizedName = (displayName || '').trim()
  if (!normalizedTable || !normalizedName) return normalizedName
  const repeatedPrefix = new RegExp(`^(?:${normalizedTable.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}[_.])+`)
  normalizedName = normalizedName.replace(repeatedPrefix, '')
  return `${normalizedTable}_${normalizedName}`
}

export const buildAliasAwareDefaultDisplayName = (entry: TableScopeEntry, columnName: string, columnComment?: string) => {
  const baseName = (columnComment || columnName || '').trim()
  if (!baseName) return ''
  return entry.alias && entry.alias !== entry.table ? buildTablePrefixedDisplayName(entry.alias, baseName) : baseName
}

export const normalizeFieldName = (value: string) => (value || '').trim()

export const buildDimensionPreserveKey = (dimension: Pick<SemanticDimension, 'table' | 'table_alias' | 'column' | 'field_name'>) => {
  const tableAlias = getFieldTableAlias(dimension)
  return `${tableAlias}.${dimension.field_name || dimension.column || ''}.${dimension.column || ''}`
}

export const copyDimensionRuntimeConfig = (dimension: SemanticDimension, source?: SemanticDimension) => {
  if (!source) return dimension
  const next = { ...dimension }
  if (source.allowed_values) next.allowed_values = [...source.allowed_values]
  if (source.mapping) next.mapping = { ...source.mapping }
  if (source.allowed_operators) next.allowed_operators = [...source.allowed_operators]
  return next
}

// ============ 字段数据字典 ============

export const getColumnDictKey = (tableName: string, columnName: string) => `${tableName}.${columnName}`

export const getFieldColumnDictKey = (row: Pick<FieldConfigRow, 'table' | 'sourceColumn' | 'column'>) => {
  const tableName = String(row.table || '').trim()
  const columnName = getFieldPhysicalColumn(row)
  return tableName && columnName ? getColumnDictKey(tableName, columnName) : ''
}

export const hasColumnDictContent = (dict?: { enum_values?: unknown[]; storage_format?: string; unit?: string } | null) => {
  if (!dict) return false
  return Boolean(
    (dict.enum_values || []).length > 0 ||
    (dict.storage_format || '').trim() ||
    (dict.unit || '').trim()
  )
}

export const hasColumnDictEnumValues = (dict?: { enum_values?: Array<{ value?: string }> } | null) => {
  return Boolean(dict?.enum_values?.some(item => String(item.value || '').trim()))
}

// ============ 字段行构建（需要组件上下文的部分以 deps 注入） ============

export interface BuildFieldRowsDeps {
  configs: SemanticConfigItem[]
  getScopeEntries: (configId: string) => TableScopeEntry[]
  getScopeTableInfo: (entry: TableScopeEntry | string | undefined | null) => TableInfo | undefined | null
  getDefaultDimensionAliasSet: (configId: string) => Set<string>
  getTableColumnComment: (tableName: string, columnName: string) => string
  getTableColumnType: (tableName: string, columnName: string) => string
}

export const buildFieldRows = (configId: string, deps: BuildFieldRowsDeps): FieldConfigRow[] => {
  const config = deps.configs.find(c => c.id === configId)
  if (!config) return []
  const scopeEntries = deps.getScopeEntries(configId)
  const scopeAliasSet = new Set(scopeEntries.map(entry => entry.alias))
  const defaultDimensionAliasSet = deps.getDefaultDimensionAliasSet(configId)

  const dimensionSet = new Set<string>()
  const dimensionByColumnMap = new Map<string, SemanticDimension>()
  const customRowsMap = new Map<string, FieldConfigRow>()
  const configuredKeys = new Set<string>()

  config.dimensions.forEach(d => {
    const tableAlias = getFieldTableAlias(d)
    if (!scopeAliasSet.has(tableAlias)) return
    const physicalKey = `${tableAlias}.${d.column}`
    const fieldName = (d.field_name || d.column || '').trim()
    const expression = (d.expression || quoteIdentifier(d.column)).trim()
    configuredKeys.add(physicalKey)
    if (fieldName && d.column && fieldName !== d.column && !isRawColumnExpression(expression, d.column)) {
      const customKey = buildFieldRowKey(tableAlias, fieldName)
      const existed = customRowsMap.get(customKey)
      customRowsMap.set(customKey, {
        key: customKey,
        table: d.table,
        tableAlias,
        column: fieldName,
        sourceColumn: d.column,
        columnType: existed?.columnType || '',
        displayName: existed?.displayName || d.name || fieldName,
        isDimension: true,
        columnComment: deps.getTableColumnComment(d.table, d.column),
        description: existed?.description || d.description || '',
        metadataSupplement: existed?.metadataSupplement || d.metadata_supplement || '',
        logicalType: existed?.logicalType || d.logical_type || inferLogicalType(existed?.columnType || deps.getTableColumnType(d.table, d.column)),
        expression: existed?.expression || expression,
        isCustom: true,
      })
      return
    }
    if (!dimensionByColumnMap.has(physicalKey)) {
      dimensionByColumnMap.set(physicalKey, d)
    }
    dimensionSet.add(physicalKey)
  })

  const rows: FieldConfigRow[] = []
  scopeEntries.forEach(entry => {
    const table = deps.getScopeTableInfo(entry)
    if (!table) return
    table.columns?.forEach(col => {
      const key = buildFieldRowKey(entry.alias, col.name)
      const dimInfo = dimensionByColumnMap.get(key)
      const expression = dimInfo?.expression || quoteIdentifier(col.name)
      const columnComment = (col.description || '').trim()
      const displayName = dimInfo?.name || buildAliasAwareDefaultDisplayName(entry, col.name, columnComment)
      const columnType = col.mysql_type || col.hive_type || col.type || ''
      rows.push({
        key,
        table: table.table_name,
        tableAlias: entry.alias,
        column: col.name,
        sourceColumn: col.name,
        columnType,
        isDimension: dimensionSet.has(key) || defaultDimensionAliasSet.has(entry.alias),
        displayName,
        columnComment,
        description: dimInfo?.description || '',
        metadataSupplement: dimInfo?.metadata_supplement || '',
        logicalType: dimInfo?.logical_type || inferLogicalType(columnType),
        expression,
      })
    })
  })
  customRowsMap.forEach(row => {
    const tableInfo = deps.getScopeTableInfo({ table: row.table, alias: row.tableAlias })
    const sourceInfo = tableInfo?.columns?.find(c => c.name === (row.sourceColumn || row.column))
    rows.push({
      ...row,
      columnType: row.columnType || sourceInfo?.mysql_type || sourceInfo?.type || '',
      logicalType: row.logicalType || inferLogicalType(row.columnType || sourceInfo?.mysql_type || sourceInfo?.hive_type || sourceInfo?.type || ''),
      expression: row.expression || quoteIdentifier(row.sourceColumn || row.column),
      isCustom: true,
      isMissingPhysical: !sourceInfo,
    })
  })
  config.dimensions.forEach(d => {
    const tableAlias = getFieldTableAlias(d)
    if (!scopeAliasSet.has(tableAlias)) return
    const key = `${tableAlias}.${d.column}`
    if (!configuredKeys.has(key) || rows.some(row => getFieldRowAlias(row) === tableAlias && (row.sourceColumn || row.column) === d.column)) return
    rows.push({
      key,
      table: d.table,
      tableAlias,
      column: d.field_name || d.column,
      sourceColumn: d.column,
      columnType: '',
      isDimension: true,
      displayName: d.name || d.field_name || d.column,
      columnComment: deps.getTableColumnComment(d.table, d.column),
      description: d.description || '',
      metadataSupplement: d.metadata_supplement || '',
      logicalType: d.logical_type || inferLogicalType(deps.getTableColumnType(d.table, d.column)),
      expression: d.expression || quoteIdentifier(d.column),
      isMissingPhysical: true,
    })
  })
  return rows
}

export const mergeFieldRowsWithDraft = (
  nextRows: FieldConfigRow[],
  deps: { scopeEntries: TableScopeEntry[]; currentRows: FieldConfigRow[] },
): FieldConfigRow[] => {
  const currentMap = new Map(deps.currentRows.map(row => [row.key, row]))
  const nextKeySet = new Set(nextRows.map(row => row.key))
  const mergedRows = nextRows.map(row => {
    const draft = currentMap.get(row.key)
    if (!draft) return row
    return {
      ...row,
      isDimension: draft.isDimension,
      displayName: draft.displayName,
      description: draft.description ?? row.description,
      metadataSupplement: draft.metadataSupplement,
      logicalType: draft.logicalType,
      expression: draft.expression,
    }
  })
  deps.currentRows
    .filter(row => row.isCustom && !nextKeySet.has(row.key) && isFieldRowInScope(row, deps.scopeEntries))
    .forEach(row => {
      mergedRows.push({ ...row })
    })
  return mergedRows
}
