import type { SemanticRelationship } from '../types'

export const SEMANTIC_RELATIONSHIP_TYPES = [
  'one_to_one',
  'one_to_many',
  'many_to_one',
] as const

export const DEFAULT_SEMANTIC_RELATIONSHIP_TYPE: SemanticRelationship['relationship_type'] = 'one_to_one'

export const normalizeSemanticRelationship = (rel: SemanticRelationship): SemanticRelationship => {
  const relationshipType = rel.relationship_type
  const normalizedType = SEMANTIC_RELATIONSHIP_TYPES.includes(relationshipType as typeof SEMANTIC_RELATIONSHIP_TYPES[number])
    ? relationshipType
    : DEFAULT_SEMANTIC_RELATIONSHIP_TYPE
  return { ...rel, relationship_type: normalizedType }
}

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
