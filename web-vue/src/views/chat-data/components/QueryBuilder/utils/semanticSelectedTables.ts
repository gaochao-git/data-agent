import type { SemanticConfigItem, SemanticRelationship } from '../../../types'
import type { SelectedTable, TableInfo } from '../types'

const getTableAlias = (item: { table?: string; tableAlias?: string; alias?: string }) => (
  String(item.tableAlias || item.alias || item.table || '').trim()
)

const getRelationshipLeftAlias = (relationship: SemanticRelationship) => (
  String(relationship.left_alias || relationship.left_table || '').trim()
)

const getRelationshipRightAlias = (relationship: SemanticRelationship) => (
  String(relationship.right_alias || relationship.right_table || '').trim()
)

const getTableRoleLabel = (table: string, tableAlias?: string, fallbackLabel?: string) => {
  const alias = String(tableAlias || table || '').trim()
  if (!table) return fallbackLabel || alias
  if (!alias || alias === table) return fallbackLabel || table
  return `${fallbackLabel || alias}（${table}）`
}

/** Builds the minimal join chain needed by the fields selected from a semantic config. */
export const buildSemanticSelectedTables = (
  semanticConfig: SemanticConfigItem | null | undefined,
  tables: TableInfo[],
  usedAliases: Set<string>
): SelectedTable[] => {
  if (!semanticConfig) return []
  const orderedAliases = Array.from(usedAliases).filter(Boolean)
  if (orderedAliases.length === 0) return []

  const getRelConditions = (relationship: SemanticRelationship) => relationship.join_conditions || []
  const mapJoinType = (joinType?: SemanticRelationship['join_type']) => (
    (joinType || 'LEFT').toUpperCase() as 'LEFT' | 'RIGHT' | 'INNER' | 'FULL'
  )
  const getAliasPhysicalTable = (alias: string) => {
    const dimension = semanticConfig.dimensions.find(item => getTableAlias({ table: item.table, tableAlias: item.table_alias }) === alias)
    if (dimension) return dimension.table
    const metric = semanticConfig.metrics.find(item => getTableAlias({ table: item.table, tableAlias: item.table_alias }) === alias)
    if (metric) return metric.table
    const relationship = semanticConfig.relationships.find(item => (
      getRelationshipLeftAlias(item) === alias || getRelationshipRightAlias(item) === alias
    ))
    if (!relationship) return alias
    return getRelationshipLeftAlias(relationship) === alias ? relationship.left_table : relationship.right_table
  }

  const root = orderedAliases[0] || ''
  const adjacency = new Map<string, Array<{ relationship: SemanticRelationship; next: string }>>()
  semanticConfig.relationships.forEach(relationship => {
    const leftAlias = getRelationshipLeftAlias(relationship)
    const rightAlias = getRelationshipRightAlias(relationship)
    if (!adjacency.has(leftAlias)) adjacency.set(leftAlias, [])
    if (!adjacency.has(rightAlias)) adjacency.set(rightAlias, [])
    adjacency.get(leftAlias)?.push({ relationship, next: rightAlias })
    adjacency.get(rightAlias)?.push({ relationship, next: leftAlias })
  })

  const previous = new Map<string, { parent: string; relationship: SemanticRelationship }>()
  const queue = [root]
  const visited = new Set<string>([root])
  while (queue.length > 0) {
    const current = queue.shift() as string
    ;(adjacency.get(current) || []).forEach(({ relationship, next }) => {
      if (visited.has(next)) return
      visited.add(next)
      previous.set(next, { parent: current, relationship })
      queue.push(next)
    })
  }

  const requiredNodes = new Set<string>([root])
  const requiredRelationships: SemanticRelationship[] = []
  const relationshipIds = new Set<string>()
  orderedAliases.slice(1).forEach(target => {
    let node = target
    while (node !== root && previous.has(node)) {
      requiredNodes.add(node)
      const { parent, relationship } = previous.get(node) as { parent: string; relationship: SemanticRelationship }
      const id = `${getRelationshipLeftAlias(relationship)}|${getRelationshipRightAlias(relationship)}|${relationship.join_type}|${getRelConditions(relationship).map(item => `${item.left_column}=${item.right_column}`).join('&')}`
      if (!relationshipIds.has(id)) {
        relationshipIds.add(id)
        requiredRelationships.push(relationship)
      }
      node = parent
    }
  })

  const rootPhysicalTable = getAliasPhysicalTable(root)
  const rootInfo = tables.find(table => table.table_name === rootPhysicalTable)
  const result: SelectedTable[] = [{
    name: rootPhysicalTable,
    alias: root,
    label: getTableRoleLabel(rootPhysicalTable, root, rootInfo?.description || root),
  }]
  const added = new Set<string>([root])
  while (added.size < requiredNodes.size) {
    const relationship = requiredRelationships.find(item => (
      (added.has(getRelationshipLeftAlias(item)) && requiredNodes.has(getRelationshipRightAlias(item)) && !added.has(getRelationshipRightAlias(item))) ||
      (added.has(getRelationshipRightAlias(item)) && requiredNodes.has(getRelationshipLeftAlias(item)) && !added.has(getRelationshipLeftAlias(item)))
    ))
    if (!relationship) break
    const leftAlias = getRelationshipLeftAlias(relationship)
    const rightAlias = getRelationshipRightAlias(relationship)
    if (added.has(leftAlias) && !added.has(rightAlias)) {
      const info = tables.find(table => table.table_name === relationship.right_table)
      result.push({
        name: relationship.right_table,
        alias: rightAlias,
        label: getTableRoleLabel(relationship.right_table, rightAlias, info?.description || rightAlias),
        joinType: mapJoinType(relationship.join_type),
        joinOn: {
          leftTable: relationship.left_table,
          leftAlias,
          conditions: getRelConditions(relationship).map(item => ({ leftCol: item.left_column, rightCol: item.right_column })),
        },
      })
      added.add(rightAlias)
    } else if (added.has(rightAlias) && !added.has(leftAlias)) {
      const info = tables.find(table => table.table_name === relationship.left_table)
      result.push({
        name: relationship.left_table,
        alias: leftAlias,
        label: getTableRoleLabel(relationship.left_table, leftAlias, info?.description || leftAlias),
        joinType: mapJoinType(relationship.join_type),
        joinOn: {
          leftTable: relationship.right_table,
          leftAlias: rightAlias,
          conditions: getRelConditions(relationship).map(item => ({ leftCol: item.right_column, rightCol: item.left_column })),
        },
      })
      added.add(leftAlias)
    }
  }
  return result
}
