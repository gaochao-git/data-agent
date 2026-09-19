import { describe, expect, it } from 'vitest'

import {
  formatToolTableCell,
  getCardChartType,
  getCardLayoutText,
  getCardTitle,
  getChartSpecFromTool,
  getDashboardPreviewCards,
  getDashboardPreviewTitle,
  getDashboardPreviewWarningCount,
  getDashboardPreviewWritableCount,
  getDashboardSpecFromTool,
  getPresentedArtifacts,
  getPresentedArtifactKey,
  getPresentedArtifactTools,
  getPresentedDeliveryArtifacts,
  parseChartSpecValue,
  parseDashboardSpecValue,
  parseToolResultPayload,
  replaceVisiblePathsForDisplay,
} from '../messageArtifacts'
import type { Message, ToolCallInfo } from '../../types'

const presentTool = (artifacts: unknown[], extra: Record<string, unknown> = {}): ToolCallInfo => ({
  tool_call_id: 'call-1',
  tool_name: 'present_artifacts',
  args: {},
  status: 'completed',
  result: JSON.stringify({ artifacts, ...extra }),
})

const messageWith = (tool: ToolCallInfo): Message => ({
  id: 'm1',
  role: 'assistant',
  content: '',
  timestamp: new Date(),
  toolCalls: [tool],
} as unknown as Message)

describe('parseToolResultPayload', () => {
  it('prefers the artifact_payload line over the raw tool JSON', () => {
    const payload = parseToolResultPayload('[present_artifacts]\nartifact_payload={"file_path":"outputs/a.md"}')
    expect(payload?.file_path).toBe('outputs/a.md')
  })

  it('falls back to stripping the tool prefix and parsing JSON', () => {
    expect(parseToolResultPayload('[file_diff]\n{"success":true}')?.success).toBe(true)
  })

  it('returns null for empty or non-JSON text', () => {
    expect(parseToolResultPayload('')).toBeNull()
    expect(parseToolResultPayload('不是 JSON')).toBeNull()
  })
})

describe('getPresentedArtifacts / getPresentedArtifactTools', () => {
  it('collects artifacts only from completed present_artifacts calls', () => {
    const ok = presentTool([{ artifact_type: 'document', file_path: 'outputs/报告.docx' }])
    const running = { ...presentTool([{ artifact_type: 'document', file_path: 'outputs/x.docx' }]), status: 'running' as const }
    expect(getPresentedArtifacts(ok)).toHaveLength(1)
    expect(getPresentedArtifacts(running)).toHaveLength(0)
    expect(getPresentedArtifacts({ ...ok, tool_name: 'file_read' })).toHaveLength(0)
  })

  it('builds synthetic artifact tools keyed by type and normalized path', () => {
    const message = messageWith(presentTool([{ artifact_type: 'dashboard_spec', file_path: 'outputs\\dash.json' }]))
    const tools = getPresentedArtifactTools(message, 'dashboard_spec')
    expect(tools).toHaveLength(1)
    expect(tools[0]!.tool_call_id).toBe('call-1:dashboard_spec:outputs/dash.json')
    // 合成工具的 result 顶层已带 file_path / presentation_tool_call_id（getDashboardSpecFromTool 的输入口径）
    const payload = parseToolResultPayload(tools[0]!.result)
    expect(payload?.file_path).toBe('outputs/dash.json')
    expect(payload?.presentation_tool_call_id).toBe('call-1')
  })
})

describe('getPresentedDeliveryArtifacts', () => {
  it('keeps only deliverable types under outputs and dedupes by path', () => {
    const message = messageWith(presentTool([
      { artifact_type: 'document', file_path: '/mnt/user-data/outputs/报告.docx' },
      { artifact_type: 'document', file_path: 'outputs/报告.docx' },
      { artifact_type: 'query_result', file_path: 'outputs/查询.csv' },
      { artifact_type: 'markdown', file_path: 'workspace/笔记.md' },
    ]))
    const artifacts = getPresentedDeliveryArtifacts(message)
    expect(artifacts).toEqual([{ path: 'outputs/报告.docx', name: '报告.docx', artifactType: 'document' }])
  })
})

describe('parseDashboardSpecValue / parseChartSpecValue', () => {
  it('unwraps dashboard_spec; cards 缺失拒绝、空数组在本层放行（由上层拒绝空看板）', () => {
    expect(parseDashboardSpecValue({ dashboard_spec: { dashboard_name: 'x' } })).toBeNull()
    const spec = parseDashboardSpecValue({ dashboard_spec: { cards: [] } })
    expect(Array.isArray(spec?.cards)).toBe(true)
    const fromFence = parseDashboardSpecValue('```json\n{"dashboard_spec":{"cards":[{"title":"A"}]}}\n```')
    expect(fromFence?.cards).toHaveLength(1)
  })

  it('rejects chart specs missing version or encodings', () => {
    expect(parseChartSpecValue({ chart_type: 'line', data: [], encoding: { x: 'a', y: ['b'] } })).toBeNull()
    const ok = parseChartSpecValue({ version: '1.0', chart_type: 'line', data: [{ x: 1 }], encoding: { x: 'x', y: ['v'] } })
    expect(ok?.chart_type).toBe('line')
  })
})

describe('getDashboardSpecFromTool（生产口径：输入为合成产物工具）', () => {
  const buildTool = (resultExtra: Record<string, unknown> = {}, spec: unknown) => {
    const message = messageWith(presentTool([{ artifact_type: 'dashboard_spec', file_path: 'outputs/dash.json' }], resultExtra))
    const [tool] = getPresentedArtifactTools(message, 'dashboard_spec')
    const contents = { [getPresentedArtifactKey(tool)]: JSON.stringify(spec) }
    return { tool, contents }
  }

  it('derives payload with warnings and context fallbacks', () => {
    const { tool, contents } = buildTool(
      { warnings: ['w1'], valid_count: 2 },
      { dashboard_spec: { dashboard_name: '月报', semantic_config_id: 'sc_spec', cards: [{}, {}, {}] } },
    )
    const payload = getDashboardSpecFromTool(tool, contents, {
      datasourceId: 'ds_a',
      semanticConfigId: 'sc_ctx',
      conversationId: 'conv_1',
    })
    expect(payload?.dashboard_spec?.dashboard_name).toBe('月报')
    // 合成工具不透传 valid_count / warnings → writable 回退卡片数 3、warnings 为空
    expect(payload?.writable_card_count).toBe(3)
    expect(payload?.validation_warnings).toEqual([])
    expect(payload?.datasource_id).toBe('ds_a')
    expect(payload?.semantic_config_id).toBe('sc_spec')
    expect(payload?.conversation_id).toBe('conv_1')
  })

  it('falls back to context semantic config when spec omits it', () => {
    const { tool, contents } = buildTool({}, { dashboard_spec: { cards: [{}] } })
    const payload = getDashboardSpecFromTool(tool, contents, {
      datasourceId: 'ds_a',
      semanticConfigId: 'sc_ctx',
      conversationId: 'conv_1',
    })
    expect(payload?.semantic_config_id).toBe('sc_ctx')
  })

  it('returns null for content-missing or cardless specs', () => {
    const { tool, contents } = buildTool({}, { dashboard_spec: { cards: [{}] } })
    expect(getDashboardSpecFromTool(tool, {})).toBeNull()
    expect(getDashboardSpecFromTool(tool, { [getPresentedArtifactKey(tool)]: JSON.stringify({ dashboard_spec: {} }) })).toBeNull()
  })
})

describe('dashboard preview helpers', () => {
  const buildPayload = () => {
    const message = messageWith(presentTool(
      [{ artifact_type: 'dashboard_spec', file_path: 'outputs/d.json' }],
      { warnings: ['w'], valid_count: 2 },
    ))
    const [tool] = getPresentedArtifactTools(message, 'dashboard_spec')
    const contents = {
      [getPresentedArtifactKey(tool)]: JSON.stringify({
        dashboard_spec: {
          dashboard_name: '看板',
          cards: [
            { title: 'A', chart_type: 'line', layout: { x: 0, y: 0, w: 6, h: 4 } },
            { title: 'B', chart_type: 'pie' },
          ],
        },
      }),
    }
    return getDashboardSpecFromTool(tool, contents, { datasourceId: 'ds_a', semanticConfigId: null, conversationId: 'conv_1' })
  }

  it('derives cards / title / counters', () => {
    const payload = buildPayload()
    expect(getDashboardPreviewCards(payload)).toHaveLength(2)
    expect(getDashboardPreviewTitle(payload)).toBe('看板')
    // 合成工具的 result 只携带 artifact 本体，valid_count 不透传 → 回退卡片数
    expect(getDashboardPreviewWritableCount(payload)).toBe(2)
    expect(getDashboardPreviewWarningCount(payload)).toBe(0)
  })

  it('maps chart type labels and layout text', () => {
    const cards = getDashboardPreviewCards(buildPayload())
    expect(getCardChartType(cards[0]!)).toBe('折线图')
    expect(getCardChartType(cards[1]!)).toBe('饼图')
    expect(getCardTitle(cards[1]!, 1)).toBe('B')
    expect(getCardLayoutText(cards[0]!)).toBe('0,0 / 6x4')
    expect(getCardLayoutText({})).toBe('')
  })
})

describe('replaceVisiblePathsForDisplay / formatToolTableCell', () => {
  it('prefixes bare leading-slash roots without touching existing sandbox paths', () => {
    expect(replaceVisiblePathsForDisplay('写入 /outputs/a.txt 与 /mnt/user-data/outputs/b.txt')).toBe(
      '写入 /mnt/user-data/outputs/a.txt 与 /mnt/user-data/outputs/b.txt',
    )
  })

  it('formats table cells with placeholder and JSON fallback', () => {
    expect(formatToolTableCell(null)).toBe('-')
    expect(formatToolTableCell('文本')).toBe('文本')
    expect(formatToolTableCell(3)).toBe('3')
    expect(formatToolTableCell({ a: 1 })).toBe('{"a":1}')
  })
})

describe('getChartSpecFromTool（合成工具口径）', () => {
  it('parses chart spec from synthetic tool content', () => {
    const message = messageWith(presentTool([{ artifact_type: 'chart_spec', file_path: 'outputs/chart.json' }]))
    const [tool] = getPresentedArtifactTools(message, 'chart_spec')
    const contents = {
      [getPresentedArtifactKey(tool)]: JSON.stringify({ chart_spec: { version: '1.0', chart_type: 'bar', data: [{ x: 1 }], encoding: { x: 'x', y: ['v'] } } }),
    }
    expect(getChartSpecFromTool(tool, contents)?.chart_type).toBe('bar')
    expect(getChartSpecFromTool(tool, {})).toBeNull()
  })
})
