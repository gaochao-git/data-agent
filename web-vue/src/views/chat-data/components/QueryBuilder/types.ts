import type { BuilderConfig } from '../QuickQueries.vue'
import type {
  ChatDataSqlChangePayload,
  SemanticConfigItem,
  ExecutionTargetOption,
} from '../../types'

export interface ColumnInfo {
  name: string
  type: string
  mysql_type?: string
  description?: string
  primary_key?: boolean
}

export type QueryResultPanelItem = {
  key?: string | number
  title?: string
  resultData: string
  sql?: string
  memberDatasourceId?: string
  memberName?: string
  status?: 'success' | 'error' | 'none'
}

export interface TableInfo {
  table_name: string
  description?: string
  business_desc?: string
  columns?: ColumnInfo[]
}

/** 已选数据源（支持多表关联） */
export interface SelectedTable {
  name: string
  alias: string
  label: string
  joinType?: 'LEFT' | 'RIGHT' | 'INNER' | 'FULL'
  joinOn?: {
    leftTable: string
    leftAlias: string
    conditions: Array<{ leftCol: string; rightCol: string }>
  }
}

export interface DimensionField {
  id: string
  table: string
  tableAlias?: string
  column: string
  semanticName?: string
  expression?: string
  displayName?: string
  label: string
  transform?: 'raw' | 'year' | 'quarter' | 'month' | 'week' | 'day'
}

export interface AggregationField {
  id: string
  table: string
  tableAlias?: string
  column: string
  semanticName?: string
  displayName?: string
  expression?: string
  label: string
  func: 'RAW' | 'COUNT' | 'SUM' | 'AVG' | 'MAX' | 'MIN' | 'COUNT_DISTINCT'
}

export interface FilterCondition {
  id: string
  table: string
  tableAlias?: string
  column: string
  semanticName?: string
  displayName?: string
  expression?: string
  operator: string
  value: string | string[]
  valueType?: 'literal' | 'field_ref' | 'expression'
  rightTable?: string
  rightTableAlias?: string
  rightColumn?: string
  rightExpression?: string
  logic: 'AND' | 'OR'
  /** 如果设置，则为 HAVING 条件 */
  aggFunc?: string
  aggId?: string
  asChartFilter?: boolean
}

export type FilterLogic = 'AND' | 'OR'

export interface AdvancedFilterLeaf {
  kind: 'leaf'
  id: string
  filter: FilterCondition
}

export interface AdvancedFilterGroup {
  kind: 'group'
  id: string
  logic: FilterLogic
  children: Array<AdvancedFilterLeaf | AdvancedFilterGroup>
}

export type AdvancedFilterScope = 'detail' | 'aggregate'

export interface OrderByField {
  id: string
  table: string
  tableAlias?: string
  column: string
  semanticName?: string
  direction: 'ASC' | 'DESC'
  /** 如果设置，排序使用聚合表达式 */
  aggFunc?: string
  aggId?: string
}

export interface FieldPickerOption {
  value: string
  label: string
  semanticName?: string
  expression?: string
}

export interface FieldPickerGroup {
  groupLabel: string
  fields: FieldPickerOption[]
}

export interface DataSource {
  id: string
  group_name: string
  ds_tables?: TableInfo[]
  is_default?: boolean
  source_type?: string
  template_datasource_id?: string
  source_config?: {
    host?: string
    port?: number
    database?: string
  }
  member_sources?: Array<{
    member_datasource_id?: string
    org_name?: string
    org_code?: string
    source_type?: string
    host?: string
    port?: number
    database: string
  }>
}

export interface BuilderQueryResultPayload {
  status?: string | null
  error?: string
  sql?: string
  columns?: string[]
  data?: Record<string, unknown>[]
  total_count?: number
  execution_time_ms?: number
}

export type AnalysisType = 'sort' | 'topn' | 'total' | 'percent' | 'compare' | 'yoy'

export type AnalysisResult = {
  payload: BuilderQueryResultPayload
  warning: string | null
}

export interface QueryBuilderProps {
  datasourceId: string
  datasource?: DataSource | null
  datasources?: DataSource[]
  semanticConfigId?: string | null
  semanticConfig?: SemanticConfigItem | null
  semanticConfigs?: SemanticConfigItem[]
  initialBuilderConfig?: BuilderConfig | null
  hideSidebar?: boolean
  hideChartPanel?: boolean
  executionTargets?: ExecutionTargetOption[]
  executionTargetMultiple?: boolean
  selectedMemberDatasourceIds?: string[]
  hideExecutionTargetSelector?: boolean
  onSqlChange?: (payload: ChatDataSqlChangePayload) => void
}
