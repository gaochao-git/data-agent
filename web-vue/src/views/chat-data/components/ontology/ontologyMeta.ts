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

/** 绑定行唯一键（用于列表渲染 key 与归属展示） */
export function bindingIdentityText(binding: { semantic_config_id?: string; table_alias?: string }): string {
  return `${binding.semantic_config_id || '?'} / ${binding.table_alias || '?'}`
}
