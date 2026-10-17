/**
 * 本体管理共享元数据：状态机、值类型、基数等展示口径。
 * 对象/关系面板与表单组件统一引用，避免各处散落魔法字符串。
 */
import type { OntologyObjectStatus } from '../../types'

export interface OntologyStatusMeta {
  label: string
  tagType: 'info' | 'warning' | 'success' | 'danger'
  hint: string
}

// 状态机口径：draft → experimental → certified；deprecated 终态不可逆
export const ONTOLOGY_STATUS_META: Record<OntologyObjectStatus, OntologyStatusMeta> = {
  draft: { label: '草稿', tagType: 'info', hint: '建模中，不进入 agent 上下文' },
  experimental: { label: '试验', tagType: 'warning', hint: '可试运行验证，尚未认证' },
  certified: { label: '已认证', tagType: 'success', hint: '进入 agent 上下文；主键/标题键冻结' },
  deprecated: { label: '已废弃', tagType: 'danger', hint: '终态不可逆，请使用替代对象' }
}

// 与后端 validator 封闭词表一致
export const ONTOLOGY_VALUE_TYPES = ['String', 'Integer', 'Decimal', 'Boolean', 'Date', 'DateTime', 'enum'] as const

// 渲染提示键（后端 RENDER_HINT_KEYS 同源）
export const ONTOLOGY_RENDER_HINTS = [
  { key: 'searchable', label: '可搜索' },
  { key: 'sortable', label: '可排序' },
  { key: 'low_cardinality', label: '低基数' }
] as const

export const ONTOLOGY_MULTIPLICITY_META: Record<string, string> = {
  many_to_one: '多对一 (N:1)',
  one_to_one: '一对一 (1:1)'
}

// API 名规则与后端 API_NAME_PATTERN 一致
export const ONTOLOGY_NAME_PATTERN = /^[A-Za-z0-9_]{1,128}$/

/** 映射行展示文本（语义模型 ID；模型名由调用方经 useSemanticConfigOptions 解析后另行展示） */
export function mappingIdentityText(mapping: { semantic_config_id?: string }): string {
  return mapping.semantic_config_id || '?'
}

// ---------- 映射比较与属性变更联动（破坏性判定 / 表单校验，纯函数便于单测） ----------

/** 映射行最小结构：对象详情行由服务端附带 object_id/binding_type 等键，比较时必须忽略 */
export interface OntologyMappingComparable {
  semantic_config_id?: string
  column_mappings?: Record<string, string>
}

/**
 * 映射比较规范化：只保留结构键（semantic_config_id + column_mappings），
 * 列映射键值 trim、空键丢弃、键排序稳定；用于判断表单是否产生真实映射变更。
 */
export function normalizeMappingsForCompare(
  mappings: readonly OntologyMappingComparable[] | null | undefined
): string {
  const normalized = (mappings || []).map(mapping => ({
    semantic_config_id: String(mapping.semantic_config_id ?? '').trim(),
    column_mappings: Object.fromEntries(
      Object.entries(mapping.column_mappings || {})
        .map(([key, value]) => [String(key).trim(), String(value ?? '').trim()] as const)
        .filter(([key]) => !!key)
        .sort(([left], [right]) => left.localeCompare(right))
    )
  }))
  normalized.sort((left, right) => JSON.stringify(left).localeCompare(JSON.stringify(right)))
  return JSON.stringify(normalized)
}

/** 映射是否发生实质变更（忽略服务端附加键、空白与顺序差异） */
export function haveMappingsChanged(
  before: readonly OntologyMappingComparable[] | null | undefined,
  after: readonly OntologyMappingComparable[] | null | undefined
): boolean {
  return normalizeMappingsForCompare(before) !== normalizeMappingsForCompare(after)
}

/** 属性改名：同步改名映射里的旧属性键并保留原维度值（后端按属性名校验，不同步会拒绝提交） */
export function renameMappingPropertyKey(
  mappings: readonly OntologyMappingComparable[] | null | undefined,
  oldName: string,
  newName: string
): void {
  const from = oldName.trim()
  const to = newName.trim()
  if (!from || !to || from === to) return
  for (const mapping of mappings || []) {
    const columnMappings = mapping.column_mappings
    if (!columnMappings || !(from in columnMappings)) continue
    const value = columnMappings[from] ?? ''
    delete columnMappings[from]
    columnMappings[to] = value
  }
}

/** 属性删除：同步移除映射里的旧属性键（否则提交时后端会因未知属性拒绝） */
export function removeMappingPropertyKey(
  mappings: readonly OntologyMappingComparable[] | null | undefined,
  propertyName: string
): void {
  const name = propertyName.trim()
  if (!name) return
  for (const mapping of mappings || []) {
    if (mapping.column_mappings && name in mapping.column_mappings) delete mapping.column_mappings[name]
  }
}

/** 表单映射键校验：返回已不存在于属性集的映射键（改名/删除后未同步的兜底拦截） */
export function staleMappingPropertyNames(
  mappings: readonly OntologyMappingComparable[] | null | undefined,
  propertyNames: readonly string[]
): string[] {
  const available = new Set(propertyNames.map(name => name.trim()).filter(Boolean))
  const stale = new Set<string>()
  for (const mapping of mappings || []) {
    for (const key of Object.keys(mapping.column_mappings || {})) {
      const name = key.trim()
      if (name && !available.has(name)) stale.add(name)
    }
  }
  return [...stale].sort()
}

/** 多源映射冲突：非标识属性出现在多个映射（后端单源规则会拒绝；标识属性可作跨源主键） */
export interface CrossMappingConflict {
  property_name: string
  config_ids: string[]
}

export function crossMappingPropertyConflicts(
  mappings: readonly OntologyMappingComparable[] | null | undefined,
  identifierNames: readonly string[]
): CrossMappingConflict[] {
  const identifiers = new Set(identifierNames.map(name => name.trim()).filter(Boolean))
  const sources = new Map<string, Set<string>>()
  for (const mapping of mappings || []) {
    const configId = String(mapping.semantic_config_id ?? '').trim()
    for (const key of Object.keys(mapping.column_mappings || {})) {
      const name = key.trim()
      if (!name || identifiers.has(name)) continue
      const configIds = sources.get(name) ?? new Set<string>()
      if (configId) configIds.add(configId)
      sources.set(name, configIds)
    }
  }
  return [...sources.entries()]
    .filter(([, configIds]) => configIds.size > 1)
    .map(([name, configIds]) => ({ property_name: name, config_ids: [...configIds].sort() }))
    .sort((left, right) => left.property_name.localeCompare(right.property_name))
}

// ---------- 关系状态迁移（上限 = 两侧对象状态的较低者） ----------

/** 可手动迁移的状态（deprecated 为终态，只随对象级联） */
export const MANUAL_ONTOLOGY_STATUSES = ['draft', 'experimental', 'certified'] as const
export type ManualOntologyStatus = (typeof MANUAL_ONTOLOGY_STATUSES)[number]

/** 状态等级：draft < experimental < certified；deprecated 不参与排序返回 -1 */
export function ontologyStatusRank(status: OntologyObjectStatus | undefined): number {
  return (MANUAL_ONTOLOGY_STATUSES as readonly string[]).indexOf(status || 'draft')
}

/** 关系状态上限：任一端已废弃则关系只可级联；否则取两端状态较低者 */
export function relationStatusUpperBound(
  fromStatus: OntologyObjectStatus | undefined,
  toStatus: OntologyObjectStatus | undefined
): ManualOntologyStatus | 'deprecated' | null {
  if (fromStatus === 'deprecated' || toStatus === 'deprecated') return 'deprecated'
  const fromRank = ontologyStatusRank(fromStatus)
  const toRank = ontologyStatusRank(toStatus)
  if (fromRank < 0 || toRank < 0) return null
  return MANUAL_ONTOLOGY_STATUSES[Math.min(fromRank, toRank)] ?? null
}

/** 合法目标状态：排除当前状态（避免无操作提交后仍提示“已更新”），且不得高于上限 */
export function relationStatusTargets(
  currentStatus: OntologyObjectStatus | undefined,
  upperBound: ManualOntologyStatus | 'deprecated' | null
): ManualOntologyStatus[] {
  if (!upperBound || upperBound === 'deprecated') return []
  const boundRank = ontologyStatusRank(upperBound)
  return MANUAL_ONTOLOGY_STATUSES.filter(
    status => status !== currentStatus && ontologyStatusRank(status) <= boundRank
  )
}

/**
 * 默认目标状态：优先选择下一档可升级状态（关系升级手动触发，最符合主流程），
 * 无可升级目标时退回到最接近当前状态的降级目标；无合法目标返回 null。
 * 无论如何都不会默认成当前状态（避免无操作提交后仍提示“已更新”）。
 */
export function defaultRelationStatusTarget(
  currentStatus: OntologyObjectStatus | undefined,
  upperBound: ManualOntologyStatus | 'deprecated' | null
): ManualOntologyStatus | null {
  const targets = relationStatusTargets(currentStatus, upperBound)
  if (targets.length === 0) return null
  const currentRank = ontologyStatusRank(currentStatus)
  const upgrade = targets.find(status => ontologyStatusRank(status) > currentRank)
  if (upgrade) return upgrade
  const downgrades = targets.filter(status => ontologyStatusRank(status) < currentRank)
  return downgrades[downgrades.length - 1] ?? targets[0] ?? null
}

/** 对象映射数：列表行只有 mappings_count，详情行才带 mappings 明细；认证前置提示以此为准 */
export function ontologyMappingCount(
  object: { mappings_count?: number; mappings?: readonly unknown[] } | null | undefined
): number {
  if (!object) return 0
  return object.mappings_count ?? object.mappings?.length ?? 0
}
