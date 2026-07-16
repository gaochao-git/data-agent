export type QueryStatus = 'pending' | 'running' | 'succeeded' | 'failed' | 'cancelled' | string

export interface QueryResultLike {
  status?: QueryStatus | null
  success?: boolean
  error?: string | null
}

export interface QueryMemberResultLike extends QueryResultLike {
  member_datasource_id?: string | null
  member_name?: string | null
  columns?: string[] | null
  data?: Record<string, unknown>[] | null
  total_count?: number | null
  execution_time_ms?: number | null
}

export interface QueryPayloadLike extends QueryResultLike {
  columns?: string[] | null
  data?: Record<string, unknown>[] | null
  total_count?: number | null
  execution_time_ms?: number | null
  results?: QueryMemberResultLike[] | null
}

export interface DisplayQueryResult {
  columns: string[]
  data: Record<string, unknown>[]
  total_count: number
}

export const QUERY_MEMBER_NAME_COLUMN = '执行对象'
export const QUERY_MEMBER_ID_COLUMN = '__member_datasource_id'

export const normalizeQueryStatus = (result?: QueryResultLike | null): QueryStatus => {
  const status = String(result?.status || '').trim().toLowerCase()
  if (status) return status
  if (result?.success === true) return 'succeeded'
  if (result?.success === false) return 'failed'
  return 'failed'
}

export const isQuerySucceeded = (result?: QueryResultLike | null): boolean => (
  normalizeQueryStatus(result) === 'succeeded'
)

export const isQueryFailed = (result?: QueryResultLike | null): boolean => (
  ['failed', 'cancelled'].includes(normalizeQueryStatus(result))
)

const normalizeColumns = (columns?: string[] | null): string[] => {
  const result: string[] = []
  ;(columns || []).forEach(column => {
    const text = String(column || '').trim()
    if (text && !result.includes(text)) {
      result.push(text)
    }
  })
  return result
}

const buildRowColumns = (rows: Record<string, unknown>[]): string[] => {
  const columns: string[] = []
  rows.forEach(row => {
    Object.keys(row || {}).forEach(column => {
      if (!columns.includes(column)) {
        columns.push(column)
      }
    })
  })
  return columns
}

export const flattenQueryResultForDisplay = (
  result?: QueryPayloadLike | null,
  options: { includeMemberColumn?: boolean } = {}
): DisplayQueryResult => {
  if (!result) {
    return { columns: [], data: [], total_count: 0 }
  }

  const memberResults = Array.isArray(result.results) ? result.results : []
  if (!memberResults.length) {
    const data = Array.isArray(result.data) ? result.data : []
    const normalizedColumns = normalizeColumns(result.columns)
    const columns = normalizedColumns.length ? normalizedColumns : buildRowColumns(data)
    return {
      columns,
      data,
      total_count: Number(result.total_count ?? data.length) || 0,
    }
  }

  const successfulMembers = memberResults.filter(item => isQuerySucceeded(item))
  const visibleMembers = successfulMembers.length ? successfulMembers : memberResults
  const memberColumnEnabled = options.includeMemberColumn === true && visibleMembers.length > 1
  const businessColumns = normalizeColumns(
    visibleMembers.flatMap(item => item.columns || [])
  )
  const data: Record<string, unknown>[] = []

  visibleMembers.forEach((member, memberIndex) => {
    const rows = Array.isArray(member.data) ? member.data : []
    const memberId = String(member.member_datasource_id || '').trim()
    const memberName = String(member.member_name || memberId || `执行对象${memberIndex + 1}`).trim()
    rows.forEach(row => {
      const nextRow = { ...(row || {}) }
      if (memberColumnEnabled) {
        nextRow[QUERY_MEMBER_NAME_COLUMN] = memberName
        nextRow[QUERY_MEMBER_ID_COLUMN] = memberId
      }
      data.push(nextRow)
    })
  })

  const rowColumns = buildRowColumns(data)
  const columns = memberColumnEnabled
    ? [QUERY_MEMBER_NAME_COLUMN, ...businessColumns.filter(column => column !== QUERY_MEMBER_NAME_COLUMN && column !== QUERY_MEMBER_ID_COLUMN)]
    : (businessColumns.length ? businessColumns : rowColumns)
  return {
    columns,
    data,
    total_count: visibleMembers.reduce((sum, item) => sum + (Number(item.total_count ?? 0) || 0), 0),
  }
}
