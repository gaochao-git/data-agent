/**
 * 执行对象选项共享工具
 * 统一 member_sources → 执行对象选项的映射口径，以及执行对象选中 ID 的归一化/比较逻辑，
 * 供智能问数会话、辅助查询、SQL 编辑器、看板面板等入口复用，避免各入口各自拼装相似映射。
 */
import type { ExecutionTargetOption } from '../types'

// 统一的执行对象选项类型；权威定义在 types/index.ts，这里收敛统一出口
export type { ExecutionTargetOption }

/** 构建执行对象选项时真正依赖的数据源字段（各入口的 DataSource 结构略有差异，按最小结构约束） */
export interface ExecutionTargetDatasource {
  id?: string
  group_name?: string
  source_type?: string
  template_datasource_id?: string
  source_config?: Record<string, unknown>
  member_sources?: Array<{
    member_datasource_id?: string
    org_name?: string
    org_code?: string
    source_type?: string
    host?: string
    port?: number
    database?: string
  }>
}

/** buildExecutionTargets 入参 */
export interface BuildExecutionTargetsInput {
  /** 当前数据源；为空时仅依赖 fallbackId 生成兜底执行对象 */
  datasource?: ExecutionTargetDatasource | null
  /** 数据源无 member_sources 时的兜底执行对象 ID（数据源 ID 或面板已保存的成员 ID），同时作为选项 label 的兜底文案 */
  fallbackId?: string
}

/**
 * 把数据源 member_sources 映射为执行对象选项；
 * 无成员列表时回退到模板数据源/兜底 ID 构造单条选项。
 */
export const buildExecutionTargets = ({ datasource, fallbackId }: BuildExecutionTargetsInput): ExecutionTargetOption[] => {
  const members = datasource?.member_sources || []
  if (members.length) {
    return members
      .filter(member => member.member_datasource_id)
      .map(member => ({
        member_datasource_id: String(member.member_datasource_id),
        org_name: member.org_name || '',
        org_code: String(member.org_code || '').trim(),
        source_type: member.source_type || datasource?.source_type || '',
        host: member.host,
        port: member.port,
        database: member.database,
        label: member.org_name || `${member.host}:${member.port}/${member.database}`,
      }))
  }
  if (!fallbackId) return []
  const sourceConfig = datasource?.source_config || {}
  return [{
    member_datasource_id: datasource?.template_datasource_id || fallbackId,
    org_code: String(sourceConfig.org_code || '').trim(),
    source_type: datasource?.source_type || '',
    host: sourceConfig.host as string | undefined,
    port: sourceConfig.port as number | undefined,
    database: sourceConfig.database as string | undefined,
    label: datasource?.group_name || fallbackId,
  }]
}

/** 按是否允许多选归一化执行对象选中 ID：单选时只保留第一个 */
export const normalizeSelectedMemberDatasourceIds = (value: string[], multiple: boolean) => (
  multiple ? value : value.slice(0, 1)
)

/** 按顺序比较两个字符串数组是否完全一致 */
export const isSameStringArray = (left: string[], right: string[]) => {
  if (left.length !== right.length) return false
  return left.every((item, index) => item === right[index])
}
