/**
 * QueryBuilder 筛选编辑器 + 高级（组合）筛选弹窗的状态与处理逻辑。
 * 自 index.vue 抽出，代码保持原实现；需要组件上下文的部分以 deps 显式注入。
 */
import { computed, ref, type ComputedRef, type Ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  appendFilterToAdvancedTree as appendFilterToAdvancedTreeUtil,
  cloneAdvancedFilterNode,
  cloneFilterCondition,
  collectAdvancedLeafFilters,
  createNodeId,
  detectAdvancedScopeFromTree,
  findAdvancedGroup,
  findAdvancedLeafByFilterId,
  getAdvancedLeafIdSet,
  getAdvancedScopeLabel,
  getConfiguredScopeExcludingFilter,
  getFilterScope,
  getFirstConfiguredLeaf,
  removeAdvancedNode,
  removeFilterFromAdvancedTree as removeFilterFromAdvancedTreeUtil,
  syncAdvancedTreeLeafByFilter as syncAdvancedTreeLeafByFilterUtil,
} from '../utils/advancedFilterTree'
import { ensureArrayValues, parseManualValues } from '../utils/filterValues'
import {
  dateQuickPresets,
  fieldRefOperators,
  getDimensionDisplayName,
  getFieldType,
  getMetricDisplayName,
  getOperatorLabel,
  isListOperator,
  listOperators,
  operators,
  type DateQuickPreset,
} from '../utils/builderExpressions'
import type {
  AdvancedFilterGroup,
  AdvancedFilterLeaf,
  AdvancedFilterScope,
  AggregationField,
  DimensionField,
  FieldPickerGroup,
  FilterCondition,
  TableInfo,
} from '../types'

export interface QueryBuilderFilterParseResult {
  table: string
  tableAlias?: string
  column: string
  semanticName?: string
  aggFunc?: string
  aggId?: string
}

export interface UseQueryBuilderFiltersDeps {
  filters: Ref<FilterCondition[]>
  dimensions: Ref<DimensionField[]>
  aggregations: Ref<AggregationField[]>
  tables: Ref<TableInfo[]>
  dimensionFieldRefOptions: ComputedRef<Array<{ value: string; label: string }>>
  advancedDimensionFieldsGrouped: ComputedRef<FieldPickerGroup[]>
  advancedMetricFieldsGrouped: ComputedRef<FieldPickerGroup[]>
  parseFieldValue: (value: string) => QueryBuilderFilterParseResult
  getTableAlias: (item: { table?: string; tableAlias?: string; alias?: string }) => string
  buildFieldKey: (tableAlias: string | undefined, table: string | undefined, column: string | undefined) => string
  getDefaultAggregationFunc: (field: { table?: string; column?: string; type?: string }) => AggregationField['func']
  getFilterFieldLabel: (f: FilterCondition) => string
  getFilterValueLabel: (f: FilterCondition) => string
  firstTableName: ComputedRef<string>
  firstTableAlias: ComputedRef<string>
}

export function useQueryBuilderFilters(deps: UseQueryBuilderFiltersDeps) {
  const { filters, dimensions, aggregations, tables } = deps

  // ===== 筛选编辑器状态 =====
  const showFilterEditor = ref(false)
  const editingFilter = ref<FilterCondition | null>(null)
  const editingFilterFromAdvanced = ref(false)
  const filterEditorCommitted = ref(false)
  const editingFilterSnapshot = ref<FilterCondition | null>(null)
  const manualInputText = ref('')
  const manualDelimiter = ref<',' | ';' | ' ' | '\n'>(',')
  const inInputMode = ref<'list' | 'subquery'>('list')
  const subquerySql = ref('')
  const subqueryExclude = ref(false)
  const showAsChartFilter = ref(false)
  const showAdvancedFieldPicker = ref(false)
  const advancedFieldKeyword = ref('')
  const pickingAdvancedFilter = ref<FilterCondition | null>(null)
  const showAdvancedFilterDialog = ref(false)
  const advancedFilterTree = ref<AdvancedFilterGroup | null>(null)
  const advancedFilterDraft = ref<AdvancedFilterGroup | null>(null)
  const advancedFilterDraftScope = ref<AdvancedFilterScope>('detail')
  const dateFilterMode = ref<'dynamic' | 'fixed' | 'single'>('dynamic')
  const fixedDateRange = ref<string[]>([])
  const singleDateValue = ref('')

  const isDatePresetActive = (preset: DateQuickPreset) => {
    const f = editingFilter.value
    if (!f) return false
    if ((f.operator || '').toUpperCase() !== preset.operator.toUpperCase()) return false
    if (f.valueType !== 'expression') return false
    return (f.rightExpression || '').trim() === preset.expression
  }

  const currentFilterOperatorOptions = computed(() => {
    const f = editingFilter.value
    if (!f) return [...operators, ...listOperators]
    return f.valueType === 'field_ref' ? fieldRefOperators : [...operators, ...listOperators]
  })

  const getFilterColumnType = (f: FilterCondition | null) => {
    if (!f?.table || !f?.column) return ''
    const table = tables.value.find(t => t.table_name === f.table)
    const col = table?.columns?.find(c => c.name === f.column)
    return `${col?.mysql_type || col?.type || ''}`.toLowerCase()
  }

  const isDateLikeType = (rawType: string) => /(date|time|timestamp|datetime)/i.test(rawType || '')
  const isDateTimeType = (rawType: string) => /(time|timestamp|datetime)/i.test(rawType || '')

  const isEditingDateField = computed(() => {
    if (!editingFilter.value) return false
    if (editingFilter.value.aggFunc) return false
    return isDateLikeType(getFilterColumnType(editingFilter.value))
  })

  const isEditingDateTimeField = computed(() => {
    if (!editingFilter.value) return false
    if (editingFilter.value.aggFunc) return false
    return isDateTimeType(getFilterColumnType(editingFilter.value))
  })

  const currentFixedDatePickerType = computed(() => (isEditingDateTimeField.value ? 'datetimerange' : 'daterange'))
  const currentSingleDatePickerType = computed(() => (isEditingDateTimeField.value ? 'datetime' : 'date'))
  const currentDateValueFormat = computed(() => (isEditingDateTimeField.value ? 'YYYY-MM-DD HH:mm:ss' : 'YYYY-MM-DD'))
  const currentStartPlaceholder = computed(() => (isEditingDateTimeField.value ? '开始时间' : '开始日期'))
  const currentEndPlaceholder = computed(() => (isEditingDateTimeField.value ? '结束时间' : '结束日期'))
  const currentSinglePlaceholder = computed(() => (isEditingDateTimeField.value ? '请选择日期时间' : '请选择日期'))

  const advancedOutsideFilters = computed(() => {
    const leafIds = getAdvancedLeafIdSet(advancedFilterTree.value)
    if (leafIds.size === 0) return []
    return filters.value.filter(f => !leafIds.has(f.id))
  })

  const appendFilterToAdvancedTree = (filter: FilterCondition) => {
    if (!advancedFilterTree.value) return
    appendFilterToAdvancedTreeUtil(advancedFilterTree.value, filter)
  }

  const removeFilterFromAdvancedTree = (filterId: string) => {
    advancedFilterTree.value = removeFilterFromAdvancedTreeUtil(advancedFilterTree.value, filterId)
  }

  const syncAdvancedTreeLeafByFilter = (filter: FilterCondition) => {
    syncAdvancedTreeLeafByFilterUtil(advancedFilterTree.value, filter)
  }

  const createDefaultDetailFilter = (): FilterCondition => {
    const fromDimension = dimensions.value[0]
    if (fromDimension) {
      return {
        id: createNodeId(),
        table: fromDimension.table,
        tableAlias: deps.getTableAlias(fromDimension),
        column: fromDimension.column,
        semanticName: fromDimension.semanticName,
        displayName: fromDimension.displayName || fromDimension.column,
        expression: fromDimension.expression,
        operator: '=',
        value: '',
        valueType: 'literal',
        logic: 'AND'
      }
    }
    const firstTableName = deps.firstTableName.value
    const firstTableAlias = deps.firstTableAlias.value
    const tableInfo = tables.value.find(t => t.table_name === firstTableName)
    const firstCol = tableInfo?.columns?.[0]
    if (!firstTableName || !firstCol) {
      return {
        id: createNodeId(),
        table: '',
        column: '',
        operator: '=',
        value: '',
        valueType: 'literal',
        logic: 'AND'
      }
    }
    return {
      id: createNodeId(),
      table: firstTableName,
      tableAlias: firstTableAlias,
      column: firstCol.name,
      displayName: firstCol.description || firstCol.name,
      operator: '=',
      value: '',
      valueType: 'literal',
      logic: 'AND'
    }
  }

  const createEmptyFilter = (): FilterCondition => ({
    id: createNodeId(),
    table: '',
    column: '',
    displayName: '',
    operator: '=',
    value: '',
    valueType: 'literal',
    logic: 'AND'
  })

  const createDefaultAggregateFilter = (): FilterCondition => {
    const fromAgg = aggregations.value[0]
    if (fromAgg) {
      return {
        id: createNodeId(),
        table: fromAgg.table,
        tableAlias: deps.getTableAlias(fromAgg),
        column: fromAgg.column,
        semanticName: fromAgg.semanticName,
        displayName: fromAgg.displayName || fromAgg.column,
        expression: fromAgg.expression,
        operator: '=',
        value: '',
        valueType: 'literal',
        logic: 'AND',
        aggFunc: fromAgg.func,
        aggId: fromAgg.id
      }
    }
    return createEmptyFilter()
  }

  const filterFieldRefOptions = computed(() => {
    if (editingFilter.value?.aggFunc) return []
    return deps.dimensionFieldRefOptions.value
  })

  const handleFilterOperatorChange = (f: FilterCondition, operator: string) => {
    f.operator = operator
    if (isListOperator(operator)) {
      f.valueType = 'literal'
      inInputMode.value = 'list'
      subqueryExclude.value = operator === 'NOT IN'
      return
    }
    if (Array.isArray(f.value)) {
      f.value = ''
    }
    if (operator !== 'BETWEEN') {
      fixedDateRange.value = []
    }
    if (operator !== '=') {
      singleDateValue.value = ''
    }
  }

  const applyDatePreset = (preset: DateQuickPreset) => {
    const f = editingFilter.value
    if (!f) return
    f.operator = preset.operator
    f.valueType = 'expression'
    f.rightExpression = preset.expression
    f.value = ''
    dateFilterMode.value = 'dynamic'
  }

  const openFilterEditor = (f: FilterCondition) => {
    filterEditorCommitted.value = false
    editingFilterSnapshot.value = cloneFilterCondition(f)
    editingFilter.value = f
    if (!f.valueType) f.valueType = 'literal'
    if (f.aggFunc && f.valueType === 'field_ref') {
      f.valueType = 'literal'
    }
    if (f.valueType === 'field_ref' && (!f.rightTable || !f.rightColumn)) {
      const first = filterFieldRefOptions.value[0]
      if (first) {
        const parsed = deps.parseFieldValue(first.value)
        f.rightTable = parsed.table
        f.rightTableAlias = parsed.tableAlias || parsed.table
        f.rightColumn = parsed.column
      }
    }
    showAsChartFilter.value = Boolean(f.asChartFilter)
    const op = (f.operator || '').toUpperCase()
    if (isListOperator(op) && f.valueType === 'expression' && /select/i.test(f.rightExpression || '')) {
      inInputMode.value = 'subquery'
      subquerySql.value = f.rightExpression || ''
      subqueryExclude.value = op === 'NOT IN'
      manualInputText.value = ''
    } else if (isListOperator(op)) {
      inInputMode.value = 'list'
      const arr = ensureArrayValues(f.value)
      manualInputText.value = arr.join('\n')
      subquerySql.value = ''
      subqueryExclude.value = op === 'NOT IN'
    } else {
      inInputMode.value = 'list'
      manualInputText.value = ''
      subquerySql.value = ''
      subqueryExclude.value = false
    }
    if (isDateLikeType(getFilterColumnType(f))) {
      const op = (f.operator || '').toUpperCase()
      if (op === 'BETWEEN' && Array.isArray(f.value) && f.value.length === 2) {
        dateFilterMode.value = 'fixed'
        fixedDateRange.value = [String(f.value[0] || ''), String(f.value[1] || '')]
      } else if (op === 'BETWEEN' && f.valueType === 'expression' && (f.rightExpression || '').includes('AND')) {
        dateFilterMode.value = 'dynamic'
        fixedDateRange.value = []
      } else if (op === '=' && !Array.isArray(f.value) && String(f.value || '').trim()) {
        dateFilterMode.value = 'single'
        singleDateValue.value = String(f.value || '').trim()
        fixedDateRange.value = []
      } else {
        dateFilterMode.value = 'dynamic'
        fixedDateRange.value = []
        singleDateValue.value = ''
      }
    }
    showFilterEditor.value = true
  }

  const openFilterEditorFromAdvanced = (f: FilterCondition) => {
    editingFilterFromAdvanced.value = true
    openFilterEditor(f)
  }

  const closeFilterEditor = () => {
    if (!filterEditorCommitted.value && editingFilter.value && editingFilterSnapshot.value) {
      Object.assign(editingFilter.value, editingFilterSnapshot.value)
    }
    showFilterEditor.value = false
    editingFilter.value = null
    editingFilterFromAdvanced.value = false
    editingFilterSnapshot.value = null
    manualInputText.value = ''
    inInputMode.value = 'list'
    subquerySql.value = ''
    subqueryExclude.value = false
    fixedDateRange.value = []
    singleDateValue.value = ''
    dateFilterMode.value = 'dynamic'
    filterEditorCommitted.value = false
  }

  const handleFilterEditorConfirm = () => {
    if (!editingFilter.value) return
    const f = editingFilter.value
    if (isEditingDateField.value && !isListOperator(f.operator) && !['IS NULL', 'IS NOT NULL'].includes(f.operator)) {
      if (dateFilterMode.value === 'fixed') {
        if (fixedDateRange.value.length !== 2 || !fixedDateRange.value[0] || !fixedDateRange.value[1]) {
          ElMessage.warning('请选择开始和结束日期')
          return
        }
        f.operator = 'BETWEEN'
        f.valueType = 'literal'
        f.value = [fixedDateRange.value[0] || '', fixedDateRange.value[1] || '']
        f.rightExpression = undefined
      } else if (dateFilterMode.value === 'single') {
        if (!singleDateValue.value) {
          ElMessage.warning('请选择日期')
          return
        }
        if ((f.operator || '').toUpperCase() === 'BETWEEN') {
          f.operator = '='
        }
        f.valueType = 'literal'
        f.value = singleDateValue.value
        f.rightExpression = undefined
      } else {
        const expr = (f.rightExpression || '').trim()
        if (!(f.valueType === 'expression' && expr)) {
          applyDatePreset(dateQuickPresets[1]!)
        }
      }
    }
    if (isListOperator(f.operator)) {
      f.operator = subqueryExclude.value ? 'NOT IN' : 'IN'
      if (inInputMode.value === 'subquery') {
        const subSql = subquerySql.value.trim()
        if (!subSql) {
          ElMessage.warning('请输入子查询语句')
          return
        }
        f.valueType = 'expression'
        f.rightExpression = subSql
        f.value = ''
      } else {
        const values = parseManualValues(manualInputText.value, manualDelimiter.value)
        if (values.length === 0) {
          ElMessage.warning('请至少输入一个值')
          return
        }
        f.value = values
        f.valueType = 'literal'
        f.rightExpression = undefined
      }
    }
    f.asChartFilter = showAsChartFilter.value
    filterEditorCommitted.value = true
    showFilterEditor.value = false
  }

  const handleFilterRightFieldChange = (f: FilterCondition, value: string) => {
    const parsed = deps.parseFieldValue(value)
    f.rightTable = parsed.table
    f.rightTableAlias = parsed.tableAlias || parsed.table
    f.rightColumn = parsed.column
  }

  const getFilterRightFieldValue = (f: FilterCondition) => {
    if (!f.rightTable || !f.rightColumn) return ''
    return deps.buildFieldKey(f.rightTableAlias, f.rightTable, f.rightColumn)
  }

  const handleFilterValueTypeChange = (f: FilterCondition, valueType: 'literal' | 'field_ref' | 'expression') => {
    if (f.aggFunc && valueType === 'field_ref') {
      ElMessage.warning('聚合过滤暂不支持字段对字段比较')
      f.valueType = 'literal'
      return
    }
    if (valueType === 'field_ref' && filterFieldRefOptions.value.length === 0) {
      ElMessage.warning('暂无可比较字段，请先添加维度')
      f.valueType = 'literal'
      return
    }
    f.valueType = valueType
    if (valueType === 'field_ref' && !fieldRefOperators.some(op => op.value === f.operator)) {
      f.operator = '='
    }
    if (valueType !== 'field_ref' && ![...operators, ...listOperators].some(op => op.value === f.operator)) {
      f.operator = '='
    }
    if (valueType === 'field_ref' && (!f.rightTable || !f.rightColumn)) {
      const first = filterFieldRefOptions.value[0]
      if (first) {
        handleFilterRightFieldChange(f, first.value)
      }
    }
  }

  // ===== 高级（组合）筛选弹窗 =====

  const getAdvancedLeafLabel = (leaf: AdvancedFilterLeaf) => {
    const f = leaf.filter
    if (!f.table || !f.column) return ''
    return `${deps.getFilterFieldLabel(f)} ${getOperatorLabel(f.operator)} ${deps.getFilterValueLabel(f)}`
  }

  const advancedFirstConfiguredLeaf = computed(() => getFirstConfiguredLeaf(advancedFilterDraft.value))
  const advancedEffectiveScope = computed<AdvancedFilterScope | null>(() => {
    const firstLeaf = advancedFirstConfiguredLeaf.value
    return firstLeaf ? getFilterScope(firstLeaf) : null
  })
  const advancedActiveScope = computed<AdvancedFilterScope>(() => advancedEffectiveScope.value || advancedFilterDraftScope.value)

  const advancedAllFieldGroups = computed(() => {
    return [...deps.advancedDimensionFieldsGrouped.value, ...deps.advancedMetricFieldsGrouped.value]
  })

  const appendCurrentEditingFilterOption = (
    groups: FieldPickerGroup[]
  ) => {
    const f = editingFilter.value
    if (!f?.table || !f.column) return groups
    const optionValue = f.aggFunc
      ? (f.aggId ? `AGGID:${f.aggId}` : `AGG:${f.aggFunc}:${deps.getTableAlias(f)}.${f.column}`)
      : deps.buildFieldKey(f.tableAlias, f.table, f.column)
    const exists = groups.some(group => group.fields.some(field => field.value === optionValue))
    if (exists) return groups

    const option = { value: optionValue, label: deps.getFilterFieldLabel(f) }
    const targetLabel = f.aggFunc ? '指标' : '维度'
    const copied = groups.map(group => ({ groupLabel: group.groupLabel, fields: [...group.fields] }))
    const target = copied.find(group => group.groupLabel === targetLabel)
    if (target) {
      target.fields.unshift(option)
      return copied
    }
    return [{ groupLabel: targetLabel, fields: [option] }, ...copied]
  }

  const advancedEditingFieldGroups = computed(() => {
    let baseGroups: FieldPickerGroup[]
    if (!editingFilterFromAdvanced.value) {
      baseGroups = advancedAllFieldGroups.value
    } else if (!advancedEffectiveScope.value) {
      baseGroups = advancedAllFieldGroups.value
    } else {
      baseGroups = advancedEffectiveScope.value === 'aggregate' ? deps.advancedMetricFieldsGrouped.value : deps.advancedDimensionFieldsGrouped.value
    }
    return appendCurrentEditingFilterOption(baseGroups)
  })

  const advancedFieldPickerGroups = computed(() => {
    const keyword = advancedFieldKeyword.value.trim().toLowerCase()
    const sourceGroups = advancedEffectiveScope.value
      ? (advancedEffectiveScope.value === 'aggregate' ? deps.advancedMetricFieldsGrouped.value : deps.advancedDimensionFieldsGrouped.value)
      : advancedAllFieldGroups.value
    const groups = sourceGroups.map(group => ({
      groupLabel: group.groupLabel,
      fields: group.fields.filter(field => {
        if (!keyword) return true
        return field.label.toLowerCase().includes(keyword) || field.value.toLowerCase().includes(keyword)
      })
    })).filter(group => group.fields.length > 0)
    return groups
  })

  const getAdvancedPickerFieldType = (value: string, groupLabel?: string): 'string' | 'number' | 'date' => {
    if (groupLabel === '指标' || value.startsWith('AGGID:') || value.startsWith('AGG:')) return 'number'
    const parsed = deps.parseFieldValue(value)
    if (!parsed.table || !parsed.column) return 'string'
    const tableInfo = tables.value.find(t => t.table_name === parsed.table)
    const colInfo = tableInfo?.columns?.find(c => c.name === parsed.column)
    if (!colInfo) return 'string'
    return getFieldType(colInfo)
  }

  const openAdvancedFieldPicker = (f: FilterCondition) => {
    pickingAdvancedFilter.value = f
    advancedFieldKeyword.value = ''
    showAdvancedFieldPicker.value = true
  }

  const closeAdvancedFieldPicker = () => {
    showAdvancedFieldPicker.value = false
    advancedFieldKeyword.value = ''
    pickingAdvancedFilter.value = null
  }

  const handleAdvancedFieldPick = (value: string) => {
    if (!pickingAdvancedFilter.value) return
    const parsed = deps.parseFieldValue(value)
    if (!parsed.table || !parsed.column) {
      ElMessage.warning('请选择有效字段')
      return
    }
    const nextScope = parsed.aggFunc ? 'aggregate' : 'detail'
    const establishedScope = getConfiguredScopeExcludingFilter(advancedFilterDraft.value, pickingAdvancedFilter.value.id)
    if (establishedScope && nextScope !== establishedScope) {
      ElMessage.warning(`当前组合筛选仅支持${getAdvancedScopeLabel(establishedScope)}`)
      return
    }
    if (!establishedScope) advancedFilterDraftScope.value = nextScope
    pickingAdvancedFilter.value.table = parsed.table
    pickingAdvancedFilter.value.tableAlias = parsed.tableAlias || parsed.table
    pickingAdvancedFilter.value.column = parsed.column
    pickingAdvancedFilter.value.semanticName = parsed.semanticName
    pickingAdvancedFilter.value.aggFunc = parsed.aggFunc
    pickingAdvancedFilter.value.aggId = parsed.aggId
    if (parsed.aggId) {
      const matchedAgg = aggregations.value.find(a => a.id === parsed.aggId)
      if (matchedAgg) {
        pickingAdvancedFilter.value.displayName = getMetricDisplayName(matchedAgg)
      }
    } else if (parsed.aggFunc) {
      pickingAdvancedFilter.value.displayName = `${parsed.aggFunc}(${parsed.column})`
    } else {
      const matchedDim = dimensions.value.find(d => deps.buildFieldKey(d.tableAlias, d.table, d.column) === deps.buildFieldKey(parsed.tableAlias, parsed.table, parsed.column))
      pickingAdvancedFilter.value.displayName = matchedDim ? getDimensionDisplayName(matchedDim) : parsed.column
    }
    if (nextScope === 'aggregate' && pickingAdvancedFilter.value.valueType === 'field_ref') {
      pickingAdvancedFilter.value.valueType = 'literal'
    }
    if (!pickingAdvancedFilter.value.operator) pickingAdvancedFilter.value.operator = '='
    if (!pickingAdvancedFilter.value.valueType) pickingAdvancedFilter.value.valueType = 'literal'
    closeAdvancedFieldPicker()
  }

  const openAdvancedFilterDialog = () => {
    const whereFilters = filters.value.filter(f => !f.aggFunc)
    const havingFilters = filters.value.filter(f => !!f.aggFunc)
    const baseTree: AdvancedFilterGroup = advancedFilterTree.value
      ? cloneAdvancedFilterNode(advancedFilterTree.value) as AdvancedFilterGroup
      : { kind: 'group', id: createNodeId(), logic: 'AND', children: [] }
    advancedFilterDraftScope.value = detectAdvancedScopeFromTree(baseTree) || (havingFilters.length > 0 && whereFilters.length === 0 ? 'aggregate' : 'detail')
    advancedFilterDraft.value = baseTree
    showAdvancedFilterDialog.value = true
  }

  const clearAdvancedFilter = () => {
    const treeLeafIds = getAdvancedLeafIdSet(advancedFilterTree.value)
    advancedFilterTree.value = null
    if (treeLeafIds.size === 0) return
    filters.value = filters.value.filter(f => !treeLeafIds.has(f.id))
  }

  const closeAdvancedFilterDialog = () => {
    showAdvancedFilterDialog.value = false
    advancedFilterDraft.value = null
  }

  const handleAdvancedToggleLogic = (groupId: string) => {
    const group = findAdvancedGroup(advancedFilterDraft.value, groupId)
    if (!group) return
    group.logic = group.logic === 'AND' ? 'OR' : 'AND'
  }

  const handleAdvancedAddLeaf = (groupId: string) => {
    const group = findAdvancedGroup(advancedFilterDraft.value, groupId)
    if (!group) return
    if (advancedActiveScope.value === 'aggregate' && aggregations.value.length === 0) {
      ElMessage.warning('请先添加指标，再配置结果过滤')
      return
    }
    const next = advancedEffectiveScope.value
      ? (advancedEffectiveScope.value === 'aggregate' ? createDefaultAggregateFilter() : createDefaultDetailFilter())
      : createEmptyFilter()
    group.children.push({
      kind: 'leaf',
      id: createNodeId(),
      filter: next
    })
  }

  const handleAdvancedAddGroup = (groupId: string) => {
    const group = findAdvancedGroup(advancedFilterDraft.value, groupId)
    if (!group) return
    if (advancedActiveScope.value === 'aggregate' && aggregations.value.length === 0) {
      ElMessage.warning('请先添加指标，再配置结果过滤')
      return
    }
    const next = advancedEffectiveScope.value
      ? (advancedEffectiveScope.value === 'aggregate' ? createDefaultAggregateFilter() : createDefaultDetailFilter())
      : createEmptyFilter()
    group.children.push({
      kind: 'group',
      id: createNodeId(),
      logic: 'AND',
      children: [{ kind: 'leaf', id: createNodeId(), filter: next }]
    })
  }

  const handleAdvancedRemoveNode = (nodeId: string) => {
    if (!advancedFilterDraft.value) return
    if (advancedFilterDraft.value.id === nodeId) {
      advancedFilterDraft.value = null
      return
    }
    removeAdvancedNode(advancedFilterDraft.value, nodeId)
  }

  const handleAdvancedEditLeaf = (filterId: string) => {
    const leaf = findAdvancedLeafByFilterId(advancedFilterDraft.value, filterId)
    if (!leaf) return
    if (!leaf.filter.table || !leaf.filter.column) {
      openAdvancedFieldPicker(leaf.filter)
      return
    }
    openFilterEditorFromAdvanced(leaf.filter)
  }

  const confirmAdvancedFilterDialog = () => {
    const scope = advancedEffectiveScope.value || advancedFilterDraftScope.value
    const previousTreeLeafIds = getAdvancedLeafIdSet(advancedFilterTree.value)
    if (!advancedFilterDraft.value) {
      advancedFilterTree.value = null
      if (previousTreeLeafIds.size > 0) {
        filters.value = filters.value.filter(f => !previousTreeLeafIds.has(f.id))
      }
      closeAdvancedFilterDialog()
      return
    }
    const nextTree = cloneAdvancedFilterNode(advancedFilterDraft.value) as AdvancedFilterGroup
    const scopedLeaves = collectAdvancedLeafFilters(nextTree)
    const invalidLeaf = scopedLeaves.find(f => !f.table || !f.column)
    if (invalidLeaf) {
      ElMessage.warning('请先为筛选项选择字段')
      return
    }
    if (scope === 'detail' && scopedLeaves.some(f => !!f.aggFunc)) {
      ElMessage.warning('当前组合筛选仅支持明细过滤')
      return
    }
    if (scope === 'aggregate' && scopedLeaves.some(f => !f.aggFunc)) {
      ElMessage.warning('当前组合筛选仅支持结果过滤')
      return
    }
    const nextScopedFilters = scopedLeaves.map((f, idx) => {
      const cloned = cloneFilterCondition(f)
      if (scope === 'detail') {
        cloned.aggFunc = undefined
        cloned.aggId = undefined
      }
      cloned.logic = (idx === 0 ? 'AND' : 'AND') as typeof cloned.logic
      return cloned
    })
    const sameScopeOutsideFilters = filters.value
      .filter(f => (scope === 'detail' ? !f.aggFunc : !!f.aggFunc))
      .filter(f => !previousTreeLeafIds.has(f.id))
      .map(f => {
        const cloned = cloneFilterCondition(f)
        cloned.logic = 'AND'
        return cloned
      })
    const oppositeFilters = filters.value
      .filter(f => (scope === 'detail' ? !!f.aggFunc : !f.aggFunc))
      .map(f => {
        const cloned = cloneFilterCondition(f)
        cloned.logic = 'AND'
        return cloned
      })
    filters.value = scope === 'detail'
      ? [...nextScopedFilters, ...sameScopeOutsideFilters, ...oppositeFilters]
      : [...oppositeFilters, ...nextScopedFilters, ...sameScopeOutsideFilters]
    advancedFilterTree.value = nextTree
    closeAdvancedFilterDialog()
  }

  return {
    // 筛选编辑器
    showFilterEditor,
    editingFilter,
    editingFilterFromAdvanced,
    editingFilterSnapshot,
    manualInputText,
    manualDelimiter,
    inInputMode,
    subquerySql,
    subqueryExclude,
    showAsChartFilter,
    dateFilterMode,
    fixedDateRange,
    singleDateValue,
    isDatePresetActive,
    currentFilterOperatorOptions,
    getFilterColumnType,
    isDateLikeType,
    isDateTimeType,
    isEditingDateField,
    isEditingDateTimeField,
    currentFixedDatePickerType,
    currentSingleDatePickerType,
    currentDateValueFormat,
    currentStartPlaceholder,
    currentEndPlaceholder,
    currentSinglePlaceholder,
    filterFieldRefOptions,
    handleFilterOperatorChange,
    applyDatePreset,
    openFilterEditor,
    openFilterEditorFromAdvanced,
    closeFilterEditor,
    handleFilterEditorConfirm,
    handleFilterRightFieldChange,
    getFilterRightFieldValue,
    handleFilterValueTypeChange,
    // 筛选默认工厂
    createDefaultDetailFilter,
    createEmptyFilter,
    createDefaultAggregateFilter,
    // 高级筛选
    showAdvancedFieldPicker,
    advancedFieldKeyword,
    pickingAdvancedFilter,
    showAdvancedFilterDialog,
    advancedFilterTree,
    advancedFilterDraft,
    advancedFilterDraftScope,
    advancedOutsideFilters,
    appendFilterToAdvancedTree,
    removeFilterFromAdvancedTree,
    syncAdvancedTreeLeafByFilter,
    getAdvancedLeafLabel,
    advancedFirstConfiguredLeaf,
    advancedEffectiveScope,
    advancedActiveScope,
    advancedAllFieldGroups,
    advancedEditingFieldGroups,
    advancedFieldPickerGroups,
    getAdvancedPickerFieldType,
    openAdvancedFieldPicker,
    closeAdvancedFieldPicker,
    handleAdvancedFieldPick,
    openAdvancedFilterDialog,
    clearAdvancedFilter,
    closeAdvancedFilterDialog,
    handleAdvancedToggleLogic,
    handleAdvancedAddLeaf,
    handleAdvancedAddGroup,
    handleAdvancedRemoveNode,
    handleAdvancedEditLeaf,
    confirmAdvancedFilterDialog,
  }
}
