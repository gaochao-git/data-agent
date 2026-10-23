/**
 * 本体管理共享元数据：状态机、值类型、基数等展示口径。
 * 对象类型/关系类型面板与表单组件统一引用，避免各处散落魔法字符串。
 */
import type { OntologyObjectTypeStatus } from '../../types'

export interface OntologyStatusMeta {
  label: string
  tagType: 'info' | 'warning' | 'success' | 'danger'
  hint: string
}

// 状态机口径：draft → experimental → certified；deprecated 终态不可逆
export const ONTOLOGY_STATUS_META: Record<OntologyObjectTypeStatus, OntologyStatusMeta> = {
  draft: { label: '草稿', tagType: 'info', hint: '建模中，不进入 agent 上下文' },
  experimental: { label: '试验', tagType: 'warning', hint: '可试运行验证，尚未认证' },
  certified: { label: '已认证', tagType: 'success', hint: '进入 agent 上下文；主键/标题键冻结' },
  deprecated: { label: '已废弃', tagType: 'danger', hint: '终态不可逆，请使用替代对象类型' }
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

// ---------- 关系状态迁移（上限 = 两侧对象状态的较低者） ----------

/** 可手动迁移的状态（deprecated 为终态，只随对象级联） */
export const MANUAL_ONTOLOGY_STATUSES = ['draft', 'experimental', 'certified'] as const
export type ManualOntologyStatus = (typeof MANUAL_ONTOLOGY_STATUSES)[number]

/** 状态等级：draft < experimental < certified；deprecated 不参与排序返回 -1 */
export function ontologyStatusRank(status: OntologyObjectTypeStatus | undefined): number {
  return (MANUAL_ONTOLOGY_STATUSES as readonly string[]).indexOf(status || 'draft')
}

/** 关系状态上限：任一端已废弃则关系只可级联；否则取两端状态较低者 */
export function relationStatusUpperBound(
  fromStatus: OntologyObjectTypeStatus | undefined,
  toStatus: OntologyObjectTypeStatus | undefined
): ManualOntologyStatus | 'deprecated' | null {
  if (fromStatus === 'deprecated' || toStatus === 'deprecated') return 'deprecated'
  const fromRank = ontologyStatusRank(fromStatus)
  const toRank = ontologyStatusRank(toStatus)
  if (fromRank < 0 || toRank < 0) return null
  return MANUAL_ONTOLOGY_STATUSES[Math.min(fromRank, toRank)] ?? null
}

/** 合法目标状态：排除当前状态（避免无操作提交后仍提示“已更新”），且不得高于上限 */
export function relationStatusTargets(
  currentStatus: OntologyObjectTypeStatus | undefined,
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
  currentStatus: OntologyObjectTypeStatus | undefined,
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
