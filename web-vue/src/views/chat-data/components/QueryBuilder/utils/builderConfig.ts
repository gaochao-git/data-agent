import type {
  AdvancedFilterGroup,
  AdvancedFilterLeaf,
  AggregationField,
  DimensionField,
  FilterCondition,
  FilterLogic,
  OrderByField,
  SelectedTable,
} from '../types'
import { detectAdvancedScopeFromTree } from './advancedFilterTree'

type Pagination = {
  enabled: boolean
  limit: string | number | null
  offset: string | number | null
}

const getTableAlias = (item: { table?: string; tableAlias?: string; alias?: string }) => (
  String(item.tableAlias || item.alias || item.table || '').trim()
)

const FILTER_OPERATORS_WITHOUT_VALUE = new Set(['IS NULL', 'IS NOT NULL', 'EMPTY STRING'])
const LIST_FILTER_OPERATORS = new Set(['IN', 'NOT IN'])

const hasFilterValue = (value: unknown) => {
  if (value === null || value === undefined) return false
  if (typeof value === 'string') return value.trim().length > 0
  return true
}

const getFilterLabel = (filter: FilterCondition) => (
  String(filter.displayName || filter.semanticName || filter.column || '筛选条件').trim()
)

const getFilterValidationError = (filter: FilterCondition): string | null => {
  const operator = String(filter.operator || '=').trim().toUpperCase()
  if (FILTER_OPERATORS_WITHOUT_VALUE.has(operator)) return null

  const label = getFilterLabel(filter)
  const valueType = filter.valueType || 'literal'
  if (valueType === 'field_ref') {
    const complete = String(filter.rightTable || '').trim() && String(filter.rightColumn || '').trim()
    return complete ? null : `筛选条件“${label}”请选择右侧字段`
  }
  if (valueType === 'expression') {
    return String(filter.rightExpression || '').trim() ? null : `筛选条件“${label}”请输入表达式`
  }
  if (operator === 'BETWEEN') {
    const complete = Array.isArray(filter.value) && filter.value.length === 2 && filter.value.every(hasFilterValue)
    return complete ? null : `筛选条件“${label}”请输入完整的起止值`
  }
  if (LIST_FILTER_OPERATORS.has(operator)) {
    const complete = Array.isArray(filter.value) && filter.value.length > 0 && filter.value.every(hasFilterValue)
    return complete ? null : `筛选条件“${label}”请至少输入一个有效值`
  }
  return hasFilterValue(filter.value) ? null : `筛选条件“${label}”请输入筛选值`
}

export const getBuilderFilterValidationError = (filters: FilterCondition[]): string | null => (
  filters.map(getFilterValidationError).find((message): message is string => Boolean(message)) || null
)

const serializeFilterCondition = (filter: FilterCondition) => ({
  id: filter.id,
  table: filter.table,
  tableAlias: getTableAlias(filter),
  column: filter.column,
  semanticName: filter.semanticName,
  displayName: filter.displayName,
  operator: filter.operator,
  value: filter.value,
  valueType: filter.valueType || 'literal',
  rightTable: filter.rightTable,
  rightTableAlias: filter.rightTableAlias,
  rightColumn: filter.rightColumn,
  rightExpression: filter.rightExpression,
  logic: filter.logic,
  expression: filter.expression,
  aggFunc: filter.aggFunc,
  aggId: filter.aggId,
})

const serializeFlatFilterCondition = (filter: FilterCondition) => ({
  ...serializeFilterCondition(filter),
  logic: 'AND' as FilterLogic,
})

const buildAdvancedFilterDsl = (node: AdvancedFilterGroup | AdvancedFilterLeaf): Record<string, unknown> => {
  if (node.kind === 'leaf') return serializeFilterCondition(node.filter)
  return {
    [node.logic === 'AND' ? 'and' : 'or']: node.children.map(child => buildAdvancedFilterDsl(child)),
  }
}

const buildQueryPaginationPayload = (pagination: Pagination) => {
  if (!pagination.enabled) return {}
  const normalizeOptionalInteger = (value: unknown): number | null => {
    if (value === null || value === undefined || value === '') return null
    const parsed = Number(value)
    return Number.isInteger(parsed) ? parsed : null
  }
  const limit = normalizeOptionalInteger(pagination.limit)
  const offset = normalizeOptionalInteger(pagination.offset)
  const payload: { limit?: number | null; offset?: number | null } = {
    limit: limit !== null && limit > 0 ? limit : null,
  }
  if (offset !== null && offset >= 0) payload.offset = offset
  return payload
}

export interface BuildBuilderConfigParams {
  semanticConfigId?: string | null
  semanticConfigName?: string
  selectedTables: SelectedTable[]
  dimensions: DimensionField[]
  aggregations: AggregationField[]
  filters: FilterCondition[]
  advancedFilterTree: AdvancedFilterGroup | null
  orderBy: OrderByField[]
  pagination: Pagination
}

export const buildBuilderConfigPayload = (params: BuildBuilderConfigParams) => {
  const whereFilters = params.filters.filter(filter => !filter.aggFunc)
  const havingFilters = params.filters.filter(filter => !!filter.aggFunc)
  const advancedScope = detectAdvancedScopeFromTree(params.advancedFilterTree)
  const payloadFilters = params.advancedFilterTree
    ? [
      buildAdvancedFilterDsl(params.advancedFilterTree),
      ...(advancedScope === 'aggregate'
        ? whereFilters.map(serializeFlatFilterCondition)
        : havingFilters.map(serializeFlatFilterCondition)),
    ]
    : params.filters.map(serializeFlatFilterCondition)

  return {
    semantic_config_id: params.semanticConfigId || undefined,
    semantic_config_name: params.semanticConfigName || undefined,
    mainTable: params.selectedTables[0]?.name || '',
    mainAlias: params.selectedTables[0] ? getTableAlias(params.selectedTables[0]) : '',
    joins: params.selectedTables.slice(1).map(table => ({
      id: `${getTableAlias(table)}-${table.joinOn?.leftAlias || table.joinOn?.leftTable || ''}`,
      leftTable: table.joinOn?.leftTable || params.selectedTables[0]?.name || '',
      leftAlias: table.joinOn?.leftAlias || (params.selectedTables[0] ? getTableAlias(params.selectedTables[0]) : ''),
      rightTable: table.name,
      rightAlias: getTableAlias(table),
      joinType: table.joinType || 'LEFT',
      joinConditions: (table.joinOn?.conditions || []).map(condition => ({
        leftTable: table.joinOn?.leftTable || params.selectedTables[0]?.name || '',
        leftAlias: table.joinOn?.leftAlias || (params.selectedTables[0] ? getTableAlias(params.selectedTables[0]) : ''),
        leftColumn: condition.leftCol,
        rightColumn: condition.rightCol,
      })),
      leftColumn: table.joinOn?.conditions?.[0]?.leftCol || '',
      rightColumn: table.joinOn?.conditions?.[0]?.rightCol || '',
    })),
    dimensions: params.dimensions.map(dimension => ({
      id: dimension.id,
      table: dimension.table,
      tableAlias: getTableAlias(dimension),
      column: dimension.column,
      semanticName: dimension.semanticName,
      alias: dimension.displayName || dimension.column,
      expression: dimension.expression,
      transform: dimension.transform || 'raw',
    })),
    aggregations: params.aggregations.map(aggregation => ({
      id: aggregation.id,
      func: aggregation.func,
      table: aggregation.table,
      tableAlias: getTableAlias(aggregation),
      column: aggregation.column,
      semanticName: aggregation.semanticName,
      alias: aggregation.displayName || aggregation.column,
      expression: aggregation.expression,
    })),
    filters: payloadFilters.length > 0 ? payloadFilters : whereFilters.map(serializeFlatFilterCondition),
    orderBy: params.orderBy.map(order => ({
      id: order.id,
      table: order.table,
      tableAlias: getTableAlias(order),
      column: order.column,
      semanticName: order.semanticName,
      direction: order.direction,
      aggFunc: order.aggFunc,
      aggId: order.aggId,
    })),
    ...buildQueryPaginationPayload(params.pagination),
  }
}

export type BuilderConfigPayload = ReturnType<typeof buildBuilderConfigPayload>

export const toSemanticBuilderConfigPayload = (payload: BuilderConfigPayload) => ({
  dimensions: payload.dimensions,
  aggregations: payload.aggregations,
  filters: payload.filters,
  orderBy: payload.orderBy,
  ...(payload.limit === undefined ? {} : { limit: payload.limit }),
  ...(payload.offset === undefined ? {} : { offset: payload.offset }),
})

export const snapshotBuilderConfigPayload = (params: BuildBuilderConfigParams) => (
  JSON.parse(JSON.stringify(buildBuilderConfigPayload(params))) as BuilderConfigPayload
)
