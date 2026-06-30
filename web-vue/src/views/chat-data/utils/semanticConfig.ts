import type { SemanticConfigItem } from '../types'

const addSemanticConfigTableKey = (tableKeys: Set<string>, table?: string, alias?: string) => {
  const tableName = String(table || '').trim()
  const tableAlias = String(alias || tableName).trim()
  const tableKey = tableName || tableAlias
  if (tableKey) {
    tableKeys.add(tableKey)
  }
}

const collectSemanticConfigTableKeys = (config: SemanticConfigItem, tableKeys: Set<string>) => {
  ;(config.metrics || []).forEach(item => addSemanticConfigTableKey(tableKeys, item.table, item.table_alias))
  ;(config.dimensions || []).forEach(item => addSemanticConfigTableKey(tableKeys, item.table, item.table_alias))
  ;(config.relationships || []).forEach(item => {
    addSemanticConfigTableKey(tableKeys, item.left_table, item.left_alias)
    addSemanticConfigTableKey(tableKeys, item.right_table, item.right_alias)
  })
}

export const getSemanticConfigTableCount = (config: SemanticConfigItem): number => {
  const tableKeys = new Set<string>()
  collectSemanticConfigTableKeys(config, tableKeys)
  return tableKeys.size
}

export const getSemanticConfigsTableCount = (configs: SemanticConfigItem[]): number => {
  const tableKeys = new Set<string>()
  configs.forEach(config => collectSemanticConfigTableKeys(config, tableKeys))
  return tableKeys.size
}

export const formatSemanticConfigSummary = (config: SemanticConfigItem): string => {
  return `${getSemanticConfigTableCount(config)}张表 · ${(config.relationships || []).length}个关系 · ${(config.metrics || []).length}个指标 · ${(config.dimensions || []).length}个维度`
}

export const formatSemanticConfigOptionLabel = (config: SemanticConfigItem, index?: number): string => {
  const prefix = typeof index === 'number' && index >= 0 ? `${index + 1}. ` : ''
  return `${prefix}${config.name}（${formatSemanticConfigSummary(config)}）`
}
