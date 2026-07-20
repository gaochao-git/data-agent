import type { SemanticRelationship } from '../types'

export const getRelationshipConditions = (rel: SemanticRelationship) => {
  if (rel.join_conditions && rel.join_conditions.length > 0) {
    return rel.join_conditions
  }
  return [{ left_column: rel.left_column || '', right_column: rel.right_column || '' }]
}

export const syncRelationshipColumnsFromConditions = (rel: SemanticRelationship) => {
  const first = (rel.join_conditions || [])[0]
  rel.left_column = first?.left_column || ''
  rel.right_column = first?.right_column || ''
}
