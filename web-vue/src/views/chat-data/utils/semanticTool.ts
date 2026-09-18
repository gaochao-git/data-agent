import type { SemanticParams, ToolCallInfo } from '../types'

const SEMANTIC_DSL_KEYS = ['metrics', 'dimensions', 'filters', 'having', 'order_by', 'limit', 'offset'] as const

type QueryToolPayload = {
  sql?: unknown
  dsl?: unknown
}

export type QueryToolResultEnvelope = {
  type: 'query_result'
  version?: number
  status?: string
  data?: Record<string, unknown> | null
  error?: string | null
  meta?: Record<string, unknown>
}

const isRecord = (value: unknown): value is Record<string, unknown> => (
  value !== null && typeof value === 'object' && !Array.isArray(value)
)

export const parseJsonObject = (value: string): Record<string, unknown> | null => {
  try {
    const parsed = JSON.parse(value)
    return isRecord(parsed) ? parsed : null
  } catch {
    return null
  }
}

/** 查询结果文本前缀。 */
const QUERY_RESULT_PREFIX = /^\[data_analyzing_tool\]\s*/

export const extractQueryResultPayload = (result?: string): QueryToolPayload | null => {
  const text = String(result || '').trim()
  if (!text) return null

  const semanticToolText = text.replace(QUERY_RESULT_PREFIX, '').trim()
  const parsed = parseJsonObject(semanticToolText)
  if (!parsed) return null
  if (parsed.type !== 'query_result') return parsed
  return isRecord(parsed.data) ? parsed.data : parsed
}

export const parseQueryResultEnvelope = (result?: string): QueryToolResultEnvelope | null => {
  const text = String(result || '').replace(QUERY_RESULT_PREFIX, '').trim()
  if (!text) return null
  const parsed = parseJsonObject(text)
  if (!parsed || parsed.type !== 'query_result') return null
  return parsed as QueryToolResultEnvelope
}

export const getQueryResultError = (result?: string): string => {
  const envelope = parseQueryResultEnvelope(result)
  if (!envelope || !['failed', 'cancelled'].includes(String(envelope.status || '').toLowerCase())) return ''
  return String(envelope.error || '查询失败').trim()
}

export const getRenderableQueryResultData = (result?: string): string => {
  const envelope = parseQueryResultEnvelope(result)
  if (!envelope) return ''
  const meta = isRecord(envelope.meta) ? envelope.meta : {}
  if (!isRecord(envelope.data) && !meta.result_id) return ''
  if (isRecord(envelope.data)) return JSON.stringify(envelope.data)
  return JSON.stringify(envelope)
}

export const extractGeneratedSqlFromToolCall = (toolCall?: Pick<ToolCallInfo, 'result'> | null): string => {
  const sql = extractQueryResultPayload(toolCall?.result)?.sql
  return typeof sql === 'string' ? sql.trim() : ''
}

export const extractSemanticDslFromArgs = (args?: Record<string, unknown>): Record<string, unknown> | null => {
  if (!args) return null
  // 合并工具的 DSL 在 action_payload 里
  if (isRecord(args.action_payload)) args = args.action_payload
  if (isRecord(args.dsl)) return args.dsl

  const hasDslField = SEMANTIC_DSL_KEYS.some(key => Object.prototype.hasOwnProperty.call(args, key))
  if (!hasDslField) return null

  return {
    metrics: args.metrics || [],
    dimensions: args.dimensions || [],
    filters: args.filters || [],
    having: args.having || [],
    order_by: args.order_by || [],
    limit: Object.prototype.hasOwnProperty.call(args, 'limit') ? args.limit : null,
    offset: Object.prototype.hasOwnProperty.call(args, 'offset') ? args.offset : null,
  }
}

export const buildSemanticParamsFromDsl = (dsl: unknown): SemanticParams | undefined => {
  if (!isRecord(dsl)) return undefined
  return {
    metrics: Array.isArray(dsl.metrics) ? dsl.metrics as string[] : undefined,
    dimensions: Array.isArray(dsl.dimensions) ? dsl.dimensions as SemanticParams['dimensions'] : undefined,
    filters: Array.isArray(dsl.filters) ? dsl.filters as SemanticParams['filters'] : undefined,
    having: Array.isArray(dsl.having) ? dsl.having as SemanticParams['having'] : undefined,
    order_by: Array.isArray(dsl.order_by) ? dsl.order_by as SemanticParams['order_by'] : undefined,
    limit: typeof dsl.limit === 'number' ? dsl.limit : undefined,
    offset: typeof dsl.offset === 'number' ? dsl.offset : undefined,
  }
}

export const extractSemanticParamsFromArgs = (args?: Record<string, unknown>): SemanticParams | undefined => (
  buildSemanticParamsFromDsl(extractSemanticDslFromArgs(args))
)
