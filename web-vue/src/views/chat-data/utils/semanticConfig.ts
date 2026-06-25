import type { SemanticConfigItem } from '../types'

export const getSemanticConfigTableCount = (config: SemanticConfigItem): number => {
  const tableKeys = new Set<string>()
  const addTable = (table?: string, alias?: string) => {
    const tableName = String(table || '').trim()
    const tableAlias = String(alias || tableName).trim()
    if (tableAlias) {
      tableKeys.add(tableAlias)
    }
  }

  ;(config.metrics || []).forEach(item => addTable(item.table, item.table_alias))
  ;(config.dimensions || []).forEach(item => addTable(item.table, item.table_alias))
  ;(config.relationships || []).forEach(item => {
    addTable(item.left_table, item.left_alias)
    addTable(item.right_table, item.right_alias)
  })

  return tableKeys.size
}

export const formatSemanticConfigSummary = (config: SemanticConfigItem): string => {
  return `${getSemanticConfigTableCount(config)}张表 · ${(config.relationships || []).length}个关系 · ${(config.metrics || []).length}个指标 · ${(config.dimensions || []).length}个维度`
}

export const formatSemanticConfigOptionLabel = (config: SemanticConfigItem, index?: number): string => {
  const prefix = typeof index === 'number' && index >= 0 ? `${index + 1}. ` : ''
  return `${prefix}${config.name}（${formatSemanticConfigSummary(config)}）`
}
