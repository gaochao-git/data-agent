import type {
  AdvancedFilterGroup,
  AdvancedFilterLeaf,
  AdvancedFilterScope,
  FilterCondition,
} from '../types'

export const createNodeId = () => `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`

export const cloneFilterCondition = (filter: FilterCondition): FilterCondition => ({ ...filter })

export const cloneAdvancedFilterNode = (
  node: AdvancedFilterLeaf | AdvancedFilterGroup
): AdvancedFilterLeaf | AdvancedFilterGroup => {
  if (node.kind === 'leaf') {
    return { kind: 'leaf', id: node.id, filter: cloneFilterCondition(node.filter) }
  }
  return {
    kind: 'group',
    id: node.id,
    logic: node.logic,
    children: node.children.map(child => cloneAdvancedFilterNode(child))
  }
}

export const collectAdvancedLeafFilters = (node: AdvancedFilterGroup): FilterCondition[] => {
  const result: FilterCondition[] = []
  node.children.forEach(child => {
    if (child.kind === 'leaf') {
      result.push(child.filter)
      return
    }
    result.push(...collectAdvancedLeafFilters(child))
  })
  return result
}

export const getFilterScope = (f: FilterCondition): AdvancedFilterScope => (f.aggFunc ? 'aggregate' : 'detail')

export const detectAdvancedScopeFromTree = (tree: AdvancedFilterGroup | null): AdvancedFilterScope | null => {
  if (!tree) return null
  const leaves = collectAdvancedLeafFilters(tree).filter(f => !!f.table && !!f.column)
  if (leaves.length === 0) return null
  return getFilterScope(leaves[0]!)
}

export const getAdvancedScopeLabel = (scope: AdvancedFilterScope) => (scope === 'detail' ? '明细过滤' : '结果过滤')

export const getFirstConfiguredLeaf = (tree: AdvancedFilterGroup | null): FilterCondition | null => {
  if (!tree) return null
  const leaves = collectAdvancedLeafFilters(tree)
  return leaves.find(f => !!f.table && !!f.column) || null
}

export const getAdvancedLeafIdSet = (tree: AdvancedFilterGroup | null) => {
  if (!tree) return new Set<string>()
  return new Set(collectAdvancedLeafFilters(tree).map(f => f.id))
}

export const getConfiguredScopeExcludingFilter = (
  tree: AdvancedFilterGroup | null,
  excludeFilterId?: string
): AdvancedFilterScope | null => {
  if (!tree) return null
  const leaves = collectAdvancedLeafFilters(tree)
  const target = leaves.find(f => f.id !== excludeFilterId && !!f.table && !!f.column)
  return target ? getFilterScope(target) : null
}

export const findAdvancedGroup = (node: AdvancedFilterGroup | null, groupId: string): AdvancedFilterGroup | null => {
  if (!node) return null
  if (node.id === groupId) return node
  for (const child of node.children) {
    if (child.kind === 'group') {
      const matched = findAdvancedGroup(child, groupId)
      if (matched) return matched
    }
  }
  return null
}

export const removeAdvancedNode = (node: AdvancedFilterGroup, nodeId: string): boolean => {
  const idx = node.children.findIndex(child => child.id === nodeId)
  if (idx >= 0) {
    node.children.splice(idx, 1)
    return true
  }
  for (const child of node.children) {
    if (child.kind !== 'group') continue
    if (removeAdvancedNode(child, nodeId)) return true
  }
  return false
}

export const findAdvancedLeafByFilterId = (
  node: AdvancedFilterGroup | null,
  filterId: string
): AdvancedFilterLeaf | null => {
  if (!node) return null
  for (const child of node.children) {
    if (child.kind === 'leaf' && child.filter.id === filterId) return child
    if (child.kind === 'group') {
      const leaf = findAdvancedLeafByFilterId(child, filterId)
      if (leaf) return leaf
    }
  }
  return null
}

export const appendFilterToAdvancedTree = (
  tree: AdvancedFilterGroup,
  filter: FilterCondition
): void => {
  const scope = detectAdvancedScopeFromTree(tree)
  if (!scope) return
  if (getFilterScope(filter) !== scope) return
  tree.children.push({ kind: 'leaf', id: createNodeId(), filter: cloneFilterCondition(filter) })
}

export const removeFilterFromAdvancedTree = (
  tree: AdvancedFilterGroup | null,
  filterId: string
): AdvancedFilterGroup | null => {
  if (!tree) return null
  const leaf = findAdvancedLeafByFilterId(tree, filterId)
  if (!leaf) return tree
  removeAdvancedNode(tree, leaf.id)
  if (collectAdvancedLeafFilters(tree).length === 0) {
    return null
  }
  return tree
}

export const syncAdvancedTreeLeafByFilter = (
  tree: AdvancedFilterGroup | null,
  filter: FilterCondition
): void => {
  if (!tree) return
  const leaf = findAdvancedLeafByFilterId(tree, filter.id)
  if (!leaf) return
  leaf.filter = cloneFilterCondition(filter)
}
