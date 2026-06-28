import type { DatasourceColumnDictInfo, SemanticMetric, TableInfo } from '../types'

export interface FieldTooltipSection {
  title: string
  lines: string[]
}

export const hasFieldDictContent = (dict?: DatasourceColumnDictInfo | null) => {
  if (!dict) return false
  return Boolean(
    (dict.enum_values || []).length > 0 ||
    (dict.storage_format || '').trim() ||
    (dict.unit || '').trim()
  )
}

export const buildFieldDictLines = (dict?: DatasourceColumnDictInfo | null) => {
  if (!hasFieldDictContent(dict)) return []
  const lines: string[] = []
  const enumValues = (dict?.enum_values || [])
    .map(item => {
      const value = String(item.value || '').trim()
      const label = String(item.label || '').trim()
      if (!value) return ''
      return label ? `${value}=${label}` : value
    })
    .filter(Boolean)
  if (enumValues.length) {
    lines.push(`枚举值：${enumValues.join('；')}`)
  }
  const storageFormat = String(dict?.storage_format || '').trim()
  if (storageFormat) {
    lines.push(`存储格式：${storageFormat}`)
  }
  const unit = String(dict?.unit || '').trim()
  if (unit) {
    lines.push(`单位：${unit}`)
  }
  return lines
}

export const createTooltipSection = (
  title: string,
  lines: Array<string | undefined | null | false>
): FieldTooltipSection | null => {
  const normalizedLines = lines
    .map(line => String(line || '').trim())
    .filter(Boolean)
  return normalizedLines.length ? { title, lines: normalizedLines } : null
}

export const buildFieldDictSection = (dict?: DatasourceColumnDictInfo | null) => {
  const lines = buildFieldDictLines(dict)
  return lines.length ? createTooltipSection('数据字典', lines) : null
}

export const findColumnDict = (
  tables: TableInfo[],
  tableName?: string,
  columnName?: string,
) => {
  const normalizedTable = String(tableName || '').trim()
  const normalizedColumn = String(columnName || '').trim()
  if (!normalizedTable || !normalizedColumn) return undefined
  const table = tables.find(item => item.table_name === normalizedTable)
  return table?.columns?.find(item => item.name === normalizedColumn)?.column_dict
}

export const extractMetricFieldName = (metric: Pick<SemanticMetric, 'field_name' | 'expression'>) => {
  const fieldName = String(metric.field_name || '').trim()
  if (fieldName) return fieldName.split('.').pop()?.replace(/`/g, '').trim() || fieldName

  const expr = String(metric.expression || '').trim()
  if (!expr) return ''
  const inner = expr.match(/\((.*)\)/)?.[1] || expr
  const cleaned = inner.replace(/DISTINCT/ig, '').replace(/`/g, '').trim()
  if (!cleaned) return ''
  const first = cleaned.split(',')[0]?.trim() || ''
  return first.split('.').pop()?.trim() || ''
}
