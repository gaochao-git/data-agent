/**
 * 智能体消息里「产物类」工具结果的解析与派生（从 conversation/Conversation.vue 平移）。
 *
 * 覆盖三类：
 * - present_artifacts 的通用 payload 解析（artifact_payload= 行或工具结果 JSON）；
 * - 交付产物（document/spreadsheet/presentation/markdown/html/yaml）清单；
 * - 看板 spec / 图表 spec 预览卡片的派生信息。
 *
 * 全部为纯函数：涉及会话上下文的（数据源/语义模型/会话 ID 兜底、产物内容缓存）
 * 通过参数显式传入，组件层负责响应式装配。
 */
import type { Message, ToolCallInfo } from '../types'
import { normalizeChartType } from '../chartTypes'
import { normalizeVisiblePath } from './consoleFileTree'

/** 解析工具结果文本：优先 `artifact_payload=` 行，其次剥掉 `[工具名]` 前缀后的 JSON。 */
export const parseToolResultPayload = (result?: string): Record<string, unknown> | null => {
  const text = String(result || '').trim()
  if (!text) return null
  const artifactLine = text.match(/^artifact_payload=(.+)$/m)?.[1]
  if (artifactLine) {
    try {
      const parsed = JSON.parse(artifactLine)
      return parsed && typeof parsed === 'object' ? parsed as Record<string, unknown> : null
    } catch {
      // fall through to normal tool JSON parsing
    }
  }
  const jsonText = text.replace(/^\[[^\]]+\]\s*/, '')
  try {
    const parsed = JSON.parse(jsonText)
    return parsed && typeof parsed === 'object' ? parsed as Record<string, unknown> : null
  } catch {
    return null
  }
}

export const getPresentedArtifacts = (tool?: ToolCallInfo | null): Array<Record<string, unknown>> => {
  if (!tool || tool.tool_name !== 'present_artifacts' || tool.status !== 'completed') return []
  const payload = parseToolResultPayload(tool.result)
  const artifacts = payload?.artifacts
  if (!Array.isArray(artifacts)) return []
  return artifacts.filter((item): item is Record<string, unknown> => item !== null && typeof item === 'object' && !Array.isArray(item))
}

export const getPresentedArtifactTools = (message: Message, artifactType: string): ToolCallInfo[] => {
  const tools: ToolCallInfo[] = []
  for (const tool of message.toolCalls || []) {
    if (tool.tool_name !== 'present_artifacts') continue
    getPresentedArtifacts(tool).forEach((artifact, index) => {
      if (String(artifact.artifact_type || '').trim() !== artifactType) return
      const filePath = normalizeVisiblePath(artifact.file_path)
      if (!filePath) return
      tools.push({
        ...tool,
        tool_call_id: `${tool.tool_call_id}:${artifactType}:${filePath || index}`,
        result: JSON.stringify({
          ...artifact,
          file_path: filePath,
          presentation_tool_call_id: tool.tool_call_id,
        }),
      })
    })
  }
  return tools
}

export type PresentedDeliveryArtifactType = 'document' | 'spreadsheet' | 'presentation' | 'markdown' | 'html' | 'yaml'

export interface PresentedDeliveryArtifact {
  path: string
  name: string
  artifactType: PresentedDeliveryArtifactType
}

export const PRESENTED_DELIVERY_ARTIFACT_TYPES = new Set<PresentedDeliveryArtifactType>([
  'document',
  'spreadsheet',
  'presentation',
  'markdown',
  'html',
  'yaml',
])

/** 消息里 outputs/ 下的可交付产物清单（同名去重，保留最后一次）。 */
export const getPresentedDeliveryArtifacts = (message: Message): PresentedDeliveryArtifact[] => {
  const artifacts = new Map<string, PresentedDeliveryArtifact>()
  for (const tool of message.toolCalls || []) {
    for (const artifact of getPresentedArtifacts(tool)) {
      const artifactType = String(artifact.artifact_type || '').trim() as PresentedDeliveryArtifactType
      if (!PRESENTED_DELIVERY_ARTIFACT_TYPES.has(artifactType)) continue
      const path = normalizeVisiblePath(artifact.file_path)
      if (!path.startsWith('outputs/')) continue
      const name = path.split('/').pop() || ''
      if (!name) continue
      artifacts.set(path, { path, name, artifactType })
    }
  }
  return Array.from(artifacts.values())
}

export const parseDashboardSpecValue = (value: unknown): Record<string, unknown> | null => {
  let parsed = value
  if (typeof parsed === 'string') {
    const text = parsed.trim()
    if (!text) return null
    try {
      parsed = JSON.parse(text.replace(/^```(?:json)?\s*/i, '').replace(/```\s*$/i, '').trim())
    } catch {
      const match = text.match(/\{[\s\S]*\}/)
      if (!match) return null
      try {
        parsed = JSON.parse(match[0])
      } catch {
        return null
      }
    }
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null
  const payload = parsed as Record<string, unknown>
  const spec = payload.dashboard_spec && typeof payload.dashboard_spec === 'object' && !Array.isArray(payload.dashboard_spec)
    ? payload.dashboard_spec as Record<string, unknown>
    : payload
  return Array.isArray(spec.cards) ? spec : null
}

export const parseJsonLikeValue = (value: unknown): unknown => {
  if (typeof value !== 'string') return value
  const text = value.trim()
  if (!text) return null
  try {
    return JSON.parse(text.replace(/^```(?:json)?\s*/i, '').replace(/```\s*$/i, '').trim())
  } catch {
    return value
  }
}

export const parseChartSpecValue = (value: unknown): Record<string, unknown> | null => {
  const parsed = parseJsonLikeValue(value)
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null
  const payload = parsed as Record<string, unknown>
  const spec = payload.chart_spec && typeof payload.chart_spec === 'object' && !Array.isArray(payload.chart_spec)
    ? payload.chart_spec as Record<string, unknown>
    : payload
  const data = spec.data
  const encoding = spec.encoding
  const chartType = normalizeChartType(spec.chart_type)
  const yFields = Array.isArray((encoding as Record<string, unknown> | undefined)?.y)
    ? ((encoding as Record<string, unknown>).y as unknown[]).map(item => String(item || '').trim()).filter(Boolean)
    : []
  const graphPayload = data && typeof data === 'object' && !Array.isArray(data) ? data as Record<string, unknown> : spec
  const nodes = Array.isArray(graphPayload.nodes) ? graphPayload.nodes : []
  const links = Array.isArray(graphPayload.links) ? graphPayload.links : Array.isArray(graphPayload.edges) ? graphPayload.edges : []
  if (String(spec.version || '').trim() !== '1.0') return null
  if (chartType === 'graph' && nodes.length > 0 && links.length > 0) return spec
  if (!Array.isArray(data)) return null
  if (!encoding || typeof encoding !== 'object' || Array.isArray(encoding)) return null
  if (['line', 'bar', 'horizontal_bar', 'area', 'pie', 'map', 'combo', 'kpi', 'funnel', 'graph'].includes(chartType) && yFields.length === 0) return null
  if (['line', 'bar', 'horizontal_bar', 'area', 'pie', 'map', 'combo', 'funnel', 'graph'].includes(chartType) && !String((encoding as Record<string, unknown>).x || '').trim()) return null
  return spec
}

/** 把 workspace/outputs/uploads 相对路径补全成模型可见的 /mnt/user-data 形式（仅展示用）。 */
export const replaceVisiblePathsForDisplay = (text: string): string => {
  return String(text || '')
    .replace(/(?<!\/mnt\/user-data)\/(workspace|outputs|uploads)(?=\/|["'\s,}\]]|$)/g, '/mnt/user-data/$1')
}

export const isDashboardSpecArtifactTool = (tool: ToolCallInfo): boolean => {
  if (tool.tool_name !== 'present_artifacts') return false
  return getArtifactType(tool) === 'dashboard_spec'
}

export const isChartSpecArtifactTool = (tool: ToolCallInfo): boolean => {
  if (tool.tool_name !== 'present_artifacts') return false
  return getArtifactType(tool) === 'chart_spec'
}

export const getArtifactType = (tool?: ToolCallInfo | null): string => {
  const payload = parseToolResultPayload(tool?.result)
  return String(payload?.artifact_type || '').trim()
}

export const getPresentedArtifactPath = (tool?: ToolCallInfo | null): string => {
  const payload = parseToolResultPayload(tool?.result)
  return normalizeVisiblePath(payload?.file_path)
}

export const getPresentedArtifactKey = (tool?: ToolCallInfo | null): string => {
  const filePath = getPresentedArtifactPath(tool)
  const payload = parseToolResultPayload(tool?.result)
  const presentationToolCallId = String(payload?.presentation_tool_call_id || tool?.tool_call_id || '').trim()
  return presentationToolCallId && filePath ? `${presentationToolCallId}:${filePath}` : ''
}

/** 产物全文缓存命中：内容由组件层按 key 拉取后传入（保持纯函数口径）。 */
export const getPresentedArtifactContent = (
  tool?: ToolCallInfo | null,
  contents: Record<string, string> = {},
): string | null => {
  const key = getPresentedArtifactKey(tool)
  return key ? contents[key] ?? null : null
}

/** 会话上下文兜底值：看板 spec 未显式携带数据源/语义模型/会话 ID 时依次回退。 */
export interface DashboardSpecContext {
  datasourceId: string
  semanticConfigId: string | null
  conversationId: string | null
}

export const getDashboardSpecFromTool = (
  tool?: ToolCallInfo | null,
  contents: Record<string, string> = {},
  ctx: DashboardSpecContext = { datasourceId: '', semanticConfigId: null, conversationId: null },
): import('../types').DashboardSpecPayload | null => {
  if (!tool) return null
  if (!isDashboardSpecArtifactTool(tool)) return null
  const resultPayload = parseToolResultPayload(tool.result)
  const content = getPresentedArtifactContent(tool, contents)
  const spec = parseDashboardSpecValue(content)
  if (!spec) return null
  const cards = Array.isArray(spec.cards) ? spec.cards : []
  if (cards.length === 0) return null

  const warnings = Array.isArray(resultPayload?.warnings) ? resultPayload.warnings.map(item => String(item)) : []
  const validCount = typeof resultPayload?.valid_count === 'number'
    ? resultPayload.valid_count
    : cards.length
  const specSemanticConfigId = typeof spec.semantic_config_id === 'string' ? spec.semantic_config_id.trim() : ''
  const resultSemanticConfigId = typeof resultPayload?.semantic_config_id === 'string' ? resultPayload.semantic_config_id.trim() : ''
  const resultSemanticConfigIds = Array.isArray(resultPayload?.semantic_config_ids)
    ? resultPayload.semantic_config_ids.map(item => String(item || '').trim()).filter(Boolean)
    : []

  return {
    dashboard_spec: spec,
    validation_warnings: warnings,
    writable_card_count: Math.max(0, validCount),
    datasource_id: typeof resultPayload?.datasource_id === 'string' ? resultPayload.datasource_id : ctx.datasourceId || null,
    semantic_config_id: resultSemanticConfigId || specSemanticConfigId || ctx.semanticConfigId || null,
    semantic_config_ids: resultSemanticConfigIds,
    conversation_id: ctx.conversationId,
  }
}

export const getChartSpecFromTool = (
  tool?: ToolCallInfo | null,
  contents: Record<string, string> = {},
): Record<string, unknown> | null => {
  if (!tool || !isChartSpecArtifactTool(tool)) return null
  return parseChartSpecValue(getPresentedArtifactContent(tool, contents))
}

export const getDashboardPreviewTools = (
  message: Message,
  contents: Record<string, string> = {},
  ctx: DashboardSpecContext = { datasourceId: '', semanticConfigId: null, conversationId: null },
): ToolCallInfo[] => {
  const previewTools: ToolCallInfo[] = []
  const indexByKey = new Map<string, number>()
  const candidates = getPresentedArtifactTools(message, 'dashboard_spec')
  for (const tool of candidates) {
    const payload = getDashboardSpecFromTool(tool, contents, ctx)
    if (!payload) continue
    const title = getDashboardPreviewTitle(payload).trim()
    const cards = getDashboardPreviewCards(payload)
    const fallbackKey = cards.map((card, index) => `${getCardTitle(card, index)}:${getCardChartType(card)}`).join('|')
    const key = title || fallbackKey || tool.tool_call_id
    const existingIndex = indexByKey.get(key)
    if (existingIndex === undefined) {
      indexByKey.set(key, previewTools.length)
      previewTools.push(tool)
    } else {
      previewTools[existingIndex] = tool
    }
  }
  return previewTools
}

export const getChartPreviewTools = (
  message: Message,
  contents: Record<string, string> = {},
): ToolCallInfo[] => {
  const previewTools: ToolCallInfo[] = []
  for (const tool of getPresentedArtifactTools(message, 'chart_spec')) {
    const spec = getChartSpecFromTool(tool, contents)
    if (!spec) continue
    previewTools.push(tool)
  }
  return previewTools
}

export const getDashboardPreviewCards = (payload: import('../types').DashboardSpecPayload | null): Array<Record<string, unknown>> => {
  const cards = payload?.dashboard_spec?.cards
  return Array.isArray(cards) ? cards.filter(item => item && typeof item === 'object') as Array<Record<string, unknown>> : []
}

export const getDashboardPreviewTitle = (payload: import('../types').DashboardSpecPayload | null): string => {
  return String(payload?.dashboard_spec?.dashboard_name || payload?.dashboard_spec?.title || '看板预览')
}

export const getDashboardPreviewWritableCount = (payload: import('../types').DashboardSpecPayload | null): number => {
  if (!payload) return 0
  if (typeof payload.writable_card_count === 'number') return payload.writable_card_count
  return getDashboardPreviewCards(payload).length
}

export const getDashboardPreviewWarningCount = (payload: import('../types').DashboardSpecPayload | null): number => {
  return payload?.validation_warnings?.length || 0
}

export const getCardTitle = (card: Record<string, unknown>, index: number): string => {
  return String(card.title || card.name || `卡片 ${index + 1}`)
}

export const getCardChartType = (card: Record<string, unknown>): string => {
  const labels: Record<string, string> = {
    kpi: '指标卡',
    line: '折线图',
    bar: '柱状图',
    horizontal_bar: '横向柱状图',
    pie: '饼图',
    table: '明细表',
    map: '地图',
    combo: '柱线组合图',
    area: '面积图',
    funnel: '漏斗图',
    graph: '关系图',
  }
  const type = normalizeChartType(card.chart_type || card.type)
  return labels[type] || type
}

export const getCardLayoutText = (card: Record<string, unknown>): string => {
  const layout = card.layout
  if (!layout || typeof layout !== 'object' || Array.isArray(layout)) return ''
  const item = layout as Record<string, unknown>
  const x = Number(item.x ?? 0)
  const y = Number(item.y ?? 0)
  const w = Number(item.w ?? 0)
  const h = Number(item.h ?? 0)
  if ([x, y, w, h].some(value => Number.isNaN(value))) return ''
  return `${x},${y} / ${w}x${h}`
}

/** 表格预览单元格的文本化：空值显示占位符，对象兜底 JSON 序列化。 */
export const formatToolTableCell = (value: unknown): string => {
  if (value === null || value === undefined || value === '') return '-'
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean' || typeof value === 'bigint') return String(value)
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}
