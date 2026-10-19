/**
 * 数据源表结构规范化：接口返回的表/字段统一补齐字符串默认值，
 * 供语义模型面板、SQL 编辑器等消费方拿到稳定形状。
 */
import type { TableInfo } from '../types'

export const normalizeTableInfoList = (tables: unknown): TableInfo[] => {
  if (!Array.isArray(tables)) return []
  return tables.map((item) => {
    const table = item as Partial<TableInfo>
    const columns = Array.isArray(table.columns) ? table.columns : []
    return {
      table_name: String(table.table_name || ''),
      description: String(table.description || ''),
      business_desc: table.business_desc,
      groups: table.groups,
      columns: columns.map((column) => ({
        ...column,
        name: String(column.name || ''),
        type: String(column.type || ''),
        description: String(column.description || ''),
      })),
    }
  }).filter(table => table.table_name)
}

/** 数据源类型解析：优先组上的 source_type，缺省时成员源类型唯一才取，否则空。 */
export const resolveDatasourceType = (datasource: { source_type?: string; member_sources?: Array<{ source_type?: string }> } | null | undefined) => {
  const sourceType = String(datasource?.source_type || '').trim().toLowerCase()
  if (sourceType) return sourceType
  const memberTypes = Array.from(new Set((datasource?.member_sources || [])
    .map(member => String(member.source_type || '').trim().toLowerCase())
    .filter(Boolean)))
  return memberTypes.length === 1 ? memberTypes[0] : ''
}
