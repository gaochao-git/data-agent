/**
 * QueryBuilder 的 @ 引用下拉（维度/指标/筛选/排序行输入 @ 唤起）状态与处理逻辑。
 * 自 index.vue 抽出，代码保持原实现；组件上下文以 deps 显式注入。
 */
import { computed, reactive, ref, type Ref } from 'vue'
import { ElMessage } from 'element-plus'
import { getFieldType } from '../utils/builderExpressions'
import type { AggregationField, DimensionField, FilterCondition, OrderByField, SelectedTable, TableInfo } from '../types'

export type AtRowType = 'dimension' | 'aggregation' | 'filter' | 'order'
export interface AtItem {
  name: string
  label: string
  table?: string
  tableAlias?: string
  tableLabel?: string
  type?: string
  fieldType: 'table' | 'string' | 'number' | 'date'
}

export interface UseQueryBuilderAtMentionDeps {
  selectedTables: Ref<SelectedTable[]>
  dimensions: Ref<DimensionField[]>
  aggregations: Ref<AggregationField[]>
  filters: Ref<FilterCondition[]>
  orderBy: Ref<OrderByField[]>
  getTableColumns: (tableName: string) => NonNullable<TableInfo['columns']>
  getSelectedTableAlias: (table: SelectedTable) => string
  buildFieldKey: (tableAlias: string | undefined, table: string | undefined, column: string | undefined) => string
  getDefaultAggregationFunc: (field: { table?: string; column?: string; type?: string }) => AggregationField['func']
  canUseAggregationFunc: (func: string, excludeAggId?: string) => boolean
  showAggregationModeConflictMessage: (nextFunc: string) => void
}

export function useQueryBuilderAtMention(deps: UseQueryBuilderAtMentionDeps) {
  const { selectedTables, dimensions, aggregations, filters, orderBy } = deps

  const atState = reactive({
    activeRow: '' as AtRowType | '',
    inputText: '',
    dropdownVisible: false,
    selectedIndex: 0,
  })
  const atInputRef = ref<HTMLInputElement | null>(null)

  // 计算 @mention 下拉列表项
  const atFilteredItems = computed<AtItem[]>(() => {
    const keyword = atState.inputText.replace(/^@/, '').toLowerCase()

    // 维度/指标/筛选行：显示已选表的字段（按表分组）
    const items: AtItem[] = []
    for (const t of selectedTables.value) {
      const cols = deps.getTableColumns(t.name)
      // 多表时插入分组标题
      if (selectedTables.value.length > 1) {
        const groupLabel = t.label || t.name
        if (!keyword || cols.some(col => (col.name.toLowerCase().includes(keyword) || (col.description || '').toLowerCase().includes(keyword)))) {
          items.push({
            name: `__group__${t.name}`,
            label: groupLabel,
            table: t.name,
            tableAlias: deps.getSelectedTableAlias(t),
            tableLabel: t.label,
            fieldType: 'table',
          })
        }
      }
      for (const col of cols) {
        if (!keyword || col.name.toLowerCase().includes(keyword) || (col.description || '').toLowerCase().includes(keyword)) {
          items.push({
            name: col.name,
            label: col.description || col.name,
            table: t.name,
            tableAlias: deps.getSelectedTableAlias(t),
            tableLabel: t.label,
            type: col.mysql_type || col.type || '',
            fieldType: getFieldType(col as never),
          })
        }
      }
    }
    return items
  })

  // 输入变化 — 检测 @ 触发下拉
  const handleAtInputChange = (row: AtRowType) => {
    const v = atState.inputText
    if (v.includes('@')) {
      if (selectedTables.value.length === 0) {
        ElMessage.warning('请先添加数据表')
        atState.inputText = ''
        return
      }
      atState.activeRow = row
      atState.dropdownVisible = true
      // 跳过分组标题，选中第一个可选项
      const firstSelectable = atFilteredItems.value.findIndex(i => !i.name.startsWith('__group__'))
      atState.selectedIndex = firstSelectable >= 0 ? firstSelectable : 0
    } else {
      atState.dropdownVisible = false
    }
  }

  // 键盘导航（跳过分组标题）
  const handleAtKeydown = (e: KeyboardEvent) => {
    if (!atState.dropdownVisible) return
    const items = atFilteredItems.value
    const len = items.length
    const isGroup = (idx: number) => items[idx]?.name.startsWith('__group__')

    if (e.key === 'ArrowDown') {
      e.preventDefault()
      let next = atState.selectedIndex + 1
      while (next < len && isGroup(next)) next++
      if (next < len) atState.selectedIndex = next
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      let prev = atState.selectedIndex - 1
      while (prev >= 0 && isGroup(prev)) prev--
      if (prev >= 0) atState.selectedIndex = prev
    } else if (e.key === 'Enter') {
      e.preventDefault()
      const item = items[atState.selectedIndex]
      if (item && !item.name.startsWith('__group__')) handleAtSelect(item)
    } else if (e.key === 'Escape') {
      closeAtDropdown()
    }
  }

  // 选择项
  const handleAtSelect = (item: AtItem) => {
    const row = atState.activeRow
    const tableAlias = item.tableAlias || item.table
    if (row === 'dimension') {
      if (!dimensions.value.find(d => deps.buildFieldKey(d.tableAlias, d.table, d.column) === deps.buildFieldKey(tableAlias, item.table, item.name))) {
        dimensions.value.push({
          id: `${Date.now()}`,
          table: item.table!,
          tableAlias,
          column: item.name,
          semanticName: item.label,
          displayName: item.label,
          label: `${item.tableLabel}.${item.label}`,
          transform: 'raw'
        })
      }
    } else if (row === 'aggregation') {
      const targetFunc = deps.getDefaultAggregationFunc({ table: item.table, column: item.name, type: item.type })
      if (!deps.canUseAggregationFunc(targetFunc)) {
        deps.showAggregationModeConflictMessage(targetFunc)
        closeAtDropdown()
        return
      }
      if (!aggregations.value.find(a => deps.buildFieldKey(a.tableAlias, a.table, a.column) === deps.buildFieldKey(tableAlias, item.table, item.name) && a.func === targetFunc)) {
        aggregations.value.push({
          id: `${Date.now()}`,
          table: item.table!,
          tableAlias,
          column: item.name,
          semanticName: item.label,
          displayName: item.label,
          label: `${item.tableLabel}.${item.label}`,
          func: targetFunc
        })
      }
    } else if (row === 'filter') {
      const nextFilter: FilterCondition = {
        id: `${Date.now()}`,
        table: item.table!,
        tableAlias,
        column: item.name,
        semanticName: item.label,
        operator: '=',
        value: '',
        valueType: 'literal',
        logic: 'AND'
      }
      filters.value.push(nextFilter)
    } else if (row === 'order') {
      const existed = orderBy.value.find(o => deps.buildFieldKey(o.tableAlias, o.table, o.column) === deps.buildFieldKey(tableAlias, item.table, item.name) && !o.aggFunc)
      if (!existed) {
        orderBy.value.push({
          id: `${Date.now()}`,
          table: item.table!,
          tableAlias,
          column: item.name,
          semanticName: item.label,
          direction: 'ASC'
        })
      }
    }
    closeAtDropdown()
  }

  // 关闭下拉
  const closeAtDropdown = () => {
    atState.activeRow = ''
    atState.inputText = ''
    atState.dropdownVisible = false
  }

  const handleAtInputBlur = () => {
    window.setTimeout(() => closeAtDropdown(), 150)
  }

  return {
    atState,
    atInputRef,
    atFilteredItems,
    handleAtInputChange,
    handleAtKeydown,
    handleAtSelect,
    closeAtDropdown,
    handleAtInputBlur,
  }
}
