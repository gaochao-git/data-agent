/**
 * 本体目录图谱的数据转换纯函数（配合 OntologyCatalogGraph.vue 使用）。
 * 输入 = 物化产物 _ontology_catalog.yaml 的文本，输出 = echarts graph option。
 * 三要素全上图：对象节点 + 属性节点（环绕各自对象的小节点）+
 * 关系边（本体 relation_types 一比一，箭头=one端→many端、label=名词角色名 to_display_name，
 * 领属读法——对标 Palantir Vertex 默认与官方 Link Type 命名规则，用户拍板）。
 * 布局对齐官方 Vertex 默认体验：力导向（Auto），节点可拖拽重排、画布 roam 缩放；
 * 环形坐标仅作为力的初值传入（收敛快、不乱飞）。
 * 解析失败/结构缺失返回 null，由组件降级为提示文案——此处不抛错。
 */
import { load } from 'js-yaml'
import type { EChartsOption } from 'echarts'

export interface CatalogProperty {
  name: string
  display_name: string
  type: string
  identifier: boolean
  title: boolean
}

export interface CatalogObjectType {
  object_type: string
  display_name: string
  status: string
  description: string
  properties: CatalogProperty[]
  /** 关联语义模型展示名（目录 YAML 的 mappings，辅助信息用于 tooltip） */
  mappings: string[]
}

export interface CatalogRelationType {
  name: string
  from: string
  to: string
  multiplicity: string
  from_display_name: string
  to_display_name: string
  from_verbalize: string
  to_verbalize: string
}

export interface OntologyCatalog {
  object_types: CatalogObjectType[]
  relation_types: CatalogRelationType[]
}

// 对象节点颜色 = 治理状态（绿=certified 权威、蓝=experimental 试用、灰=draft 草稿、红=deprecated 已废弃）
const OBJECT_STATUS_COLORS: Record<string, string> = {
  certified: '#16a34a',
  experimental: '#2563eb',
  draft: '#94a3b8',
  deprecated: '#dc2626',
}

const FALLBACK_STATUS_COLOR = '#64748b'

/** 状态中文名（tooltip 用）：certified=权威、experimental=试用、draft=草稿、deprecated=已废弃 */
const STATUS_LABELS: Record<string, string> = {
  certified: '权威',
  experimental: '试用',
  draft: '草稿',
  deprecated: '已废弃',
}

export function objectTypeStatusLabel(status: string): string {
  return STATUS_LABELS[status] || '未知'
}

export function objectTypeStatusColor(status: string): string {
  return OBJECT_STATUS_COLORS[status] || FALLBACK_STATUS_COLOR
}

const MULTIPLICITY_LABELS: Record<string, string> = {
  many_to_one: '多对一',
  one_to_one: '一对一',
}

/** 属性节点配色：标识=蓝（主键地位）、标题=琥珀、普通=浅灰。 */
export function propertyNodeStyle(property: { identifier: boolean; title: boolean }): { color: string; borderColor: string } {
  if (property.identifier) return { color: '#2563eb', borderColor: '#1e40af' }
  if (property.title) return { color: '#fbbf24', borderColor: '#b45309' }
  return { color: '#e2e8f0', borderColor: '#94a3b8' }
}

/**
 * 把 verbalize 模板里的占位符替换成展示名。
 * 占位符两种写法并存（{from}/{to} 与具体对象 API 名如 {sales_order}），都替换；
 * 认不出的占位符保留原文，不猜。
 */
export function renderVerbalize(
  template: string,
  fromObject: string,
  fromDisplay: string,
  toObject: string,
  toDisplay: string
): string {
  const displayByToken: Record<string, string> = {
    from: fromDisplay,
    to: toDisplay,
    [fromObject]: fromDisplay,
    [toObject]: toDisplay,
  }
  return String(template || '').replace(/\{([^{}]+)\}/g, (raw: string, token: string) => {
    const display = displayByToken[token.trim()]
    return display !== undefined ? display : raw
  })
}

const escapeHtml = (value: unknown): string =>
  String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')

/** 解析本体目录文本。object_types 必须非空且每项有 object 名；relation_types 允许缺省（孤立对象）。 */
export function parseOntologyCatalog(content: string): OntologyCatalog | null {
  let doc: unknown
  try {
    doc = load(content)
  } catch {
    return null
  }
  if (!doc || typeof doc !== 'object') return null
  const rawObjects = (doc as { object_types?: unknown }).object_types
  if (!Array.isArray(rawObjects) || rawObjects.length === 0) return null
  const object_types: CatalogObjectType[] = []
  for (const raw of rawObjects) {
    if (!raw || typeof raw !== 'object') return null
    const item = raw as Record<string, unknown>
    const objectTypeName = typeof item.object_type === 'string' ? item.object_type.trim() : ''
    if (!objectTypeName) return null
    object_types.push({
      object_type: objectTypeName,
      display_name: typeof item.display_name === 'string' ? item.display_name : '',
      status: typeof item.status === 'string' ? item.status : '',
      description: typeof item.description === 'string' ? item.description : '',
      properties: Array.isArray(item.properties) ? (item.properties as CatalogProperty[]) : [],
      mappings: Array.isArray(item.mappings)
        ? (item.mappings as Array<Record<string, unknown>>)
            .map(mapping => (mapping && typeof mapping === 'object' ? String((mapping as Record<string, unknown>).semantic_config_name ?? '') : ''))
            .filter(Boolean)
        : [],
    })
  }
  const rawRelations = (doc as { relation_types?: unknown }).relation_types
  const relation_types: CatalogRelationType[] = Array.isArray(rawRelations)
    ? (rawRelations.filter(item => !!item && typeof item === 'object') as CatalogRelationType[])
    : []
  return { object_types, relation_types }
}

// ---------- 固定布局 ----------

interface LayoutNode {
  id: string
  name: string
  x: number
  y: number
  // echarts 节点拖拽是逐数据项开关（GraphView 读 itemModel.draggable），必须显式开启
  draggable: boolean
  symbolSize: number
  itemStyle: { color: string; borderColor?: string; borderWidth?: number }
  label: { show: boolean; position: 'bottom' | 'right' | 'left' | 'top'; formatter: string; fontSize: number; color: string }
  nodeKind: 'object' | 'property'
  catalogObjectType?: CatalogObjectType
  catalogProperty?: CatalogProperty
  ownerObjectType?: string
}

const propertyLabelPosition = (angleCos: number, angleSin: number): 'bottom' | 'right' | 'left' | 'top' => {
  if (angleCos > 0.35) return 'right'
  if (angleCos < -0.35) return 'left'
  return angleSin > 0 ? 'bottom' : 'top'
}

const layoutCatalog = (catalog: OntologyCatalog, showProperties: boolean): { nodes: LayoutNode[]; relationLinks: Array<Record<string, unknown>>; propertyLinks: Array<Record<string, unknown>> } => {
  const nodes: LayoutNode[] = []
  const relationLinks: Array<Record<string, unknown>> = []
  const propertyLinks: Array<Record<string, unknown>> = []
  const displayByObjectType = new Map(catalog.object_types.map(item => [item.object_type, item.display_name || item.object_type]))

  // 对象节点环形初值；属性节点初值环绕各自对象（力导向会在此基础上松弛）
  const objectTypeCount = catalog.object_types.length
  const objectRadius = Math.max(260, objectTypeCount * 70)
  const propertyRadius = 104
  catalog.object_types.forEach((item, index) => {
    const angle = (2 * Math.PI * index) / objectTypeCount - Math.PI / 2
    const x = Math.round(objectRadius * Math.cos(angle))
    const y = Math.round(objectRadius * Math.sin(angle))
    nodes.push({
      id: item.object_type,
      name: item.object_type,
      x,
      y,
      draggable: true,
      symbolSize: Math.min(56, 40 + item.properties.length),
      itemStyle: { color: objectTypeStatusColor(item.status) },
      label: {
        show: true,
        position: 'bottom',
        formatter: item.display_name || item.object_type,
        fontSize: 13,
        color: '#1e293b',
      },
      nodeKind: 'object',
      catalogObjectType: item,
    })
    const properties = showProperties ? item.properties : []
    properties.forEach((property, propertyIndex) => {
      const propertyAngle = (2 * Math.PI * propertyIndex) / properties.length - Math.PI / 2
      const angleCos = Math.cos(propertyAngle)
      const angleSin = Math.sin(propertyAngle)
      const style = propertyNodeStyle(property)
      nodes.push({
        id: `${item.object_type}.${property.name}`,
        name: `${item.object_type}.${property.name}`,
        x: x + Math.round(propertyRadius * angleCos),
        y: y + Math.round(propertyRadius * angleSin),
        draggable: true,
        symbolSize: property.identifier ? 16 : 12,
        itemStyle: { color: style.color, borderColor: style.borderColor, borderWidth: 1.5 },
        label: {
          show: true,
          position: propertyLabelPosition(angleCos, angleSin),
          formatter: property.display_name || property.name,
          fontSize: 10,
          color: '#475569',
        },
        nodeKind: 'property',
        catalogProperty: property,
        ownerObjectType: item.object_type,
      })
      propertyLinks.push({
        source: item.object_type,
        target: `${item.object_type}.${property.name}`,
        lineStyle: { color: '#e2e8f0', width: 1, type: 'dashed' },
      })
    })
  })

  for (const relation of catalog.relation_types) {
    const fromDisplay = displayByObjectType.get(relation.from) || relation.from
    const toDisplay = displayByObjectType.get(relation.to) || relation.to
    // Palantir 约定（用户拍板对标）：箭头 one→many（Vertex 默认展示约定），label=名词
    // 角色名 to_display_name——官方命名规则「side 表示通往该侧的链路」，one→many 方向
    // 的 label 命名到达的 many 侧（领属读法：用户 的 名下订单 = 订单）。
    // from_display_name 缺省时回落关系 API 名。
    relationLinks.push({
      source: relation.to,
      target: relation.from,
      label: {
        show: true,
        formatter: relation.to_display_name || relation.from_display_name || relation.name,
        fontSize: 10,
        color: '#64748b',
      },
      lineStyle: { color: '#94a3b8', width: 1.8, curveness: 0.12 },
      relation,
      fromDisplay,
      toDisplay,
    })
  }
  return { nodes, relationLinks, propertyLinks }
}

export interface GraphOptions {
  /** 是否展开属性节点（默认折叠：对象多/属性多时图谱更清爽，悬停对象仍可见属性清单） */
  showProperties?: boolean
}

export function buildGraphOption(catalog: OntologyCatalog, options: GraphOptions = {}): EChartsOption {
  const objectTypeByName = new Map(catalog.object_types.map(item => [item.object_type, item]))
  const { nodes, relationLinks, propertyLinks } = layoutCatalog(catalog, options.showProperties === true)
  // 属性折叠时不参与布局，属性挂接边一并隐藏
  const links = options.showProperties === true ? [...relationLinks, ...propertyLinks] : relationLinks

  return {
    tooltip: {
      show: true,
      confine: true,
      trigger: 'item',
      formatter: params => formatGraphTooltip(params, objectTypeByName),
    },
    series: [
      {
        type: 'graph',
        // 力导向布局（对齐 Palantir Vertex 默认 Auto）：节点可拖拽重排、连线跟随、roam 缩放；
        // 初值坐标已按环形排好，repulsion/edgeLength 调小让属性节点贴住各自对象
        layout: 'force',
        force: { repulsion: 320, edgeLength: [70, 150], gravity: 0.16, layoutAnimation: true },
        roam: true,
        data: nodes as never,
        links: links as never,
        // 边 label 逐条控制：关系边带多重性（label 已挂在 relationLinks），属性边不显示
        edgeSymbol: ['none', 'arrow'],
        edgeLabel: { show: false },
        emphasis: { focus: 'adjacency' },
      },
    ],
  }
}

const formatGraphTooltip = (params: unknown, objectTypeByName: Map<string, CatalogObjectType>): string => {
  const data = (params as { data?: Record<string, unknown> }).data
  if (!data) return ''
  // 关系边
  if ('relation' in data) {
    const link = data as unknown as { relation: CatalogRelationType; fromDisplay: string; toDisplay: string }
    const relation = link.relation
    const lines = [
      `<b>${escapeHtml(relation.name)}</b>`,
      // 方向行与画布箭头一致（one 端 → many 端 = toDisplay → fromDisplay）
      `${escapeHtml(link.toDisplay)} → ${escapeHtml(link.fromDisplay)}（${escapeHtml(MULTIPLICITY_LABELS[relation.multiplicity] || relation.multiplicity)}）`,
    ]
    const fromVerbalize = renderVerbalize(relation.from_verbalize || '', relation.from, link.fromDisplay, relation.to, link.toDisplay)
    const toVerbalize = renderVerbalize(relation.to_verbalize || '', relation.from, link.fromDisplay, relation.to, link.toDisplay)
    if (fromVerbalize) lines.push(escapeHtml(fromVerbalize))
    if (toVerbalize) lines.push(escapeHtml(toVerbalize))
    return lines.join('<br/>')
  }
  // 属性节点
  if ('catalogProperty' in data) {
    const node = data as unknown as { catalogProperty: CatalogProperty; ownerObjectType?: string }
    const property = node.catalogProperty
    const owner = node.ownerObjectType ? objectTypeByName.get(node.ownerObjectType) : undefined
    const tags = [property.identifier ? '标识' : '', property.title ? '标题' : ''].filter(Boolean).join('，')
    const lines = [
      `<b>${escapeHtml(property.display_name || property.name)}</b>`,
      `类型：${escapeHtml(property.type)}`,
      tags ? `角色：${escapeHtml(tags)}` : '',
    ]
    if (owner) lines.push(`所属对象：${escapeHtml(owner.display_name || owner.object_type)}`)
    return lines.filter(Boolean).join('<br/>')
  }
  // 对象节点 tooltip 分层：①名称+API名 ②状态徽章（色块） ③描述/关联模型（带标签前缀） ④属性分区
  const catalogObjectType = objectTypeByName.get(String(data.name ?? ''))
  if (!catalogObjectType) return ''
  const statusColor = objectTypeStatusColor(catalogObjectType.status)
  const titleText = escapeHtml(catalogObjectType.display_name || catalogObjectType.object_type)
  const lines = [
    `<b style="font-size:13px;">${titleText}</b>&nbsp;<span style="color:#94a3b8;font-size:11px;">${escapeHtml(catalogObjectType.object_type)}</span>`,
    `<span style="color:#64748b;">对象状态：</span><span style="display:inline-block;background:${statusColor};color:#fff;border-radius:8px;padding:0 7px;font-size:11px;line-height:16px;">${escapeHtml(objectTypeStatusLabel(catalogObjectType.status))}</span>`,
  ]
  if (catalogObjectType.description) lines.push(`<span style="color:#64748b;">描述：${escapeHtml(catalogObjectType.description)}</span>`)
  if (catalogObjectType.mappings.length) lines.push(`<span style="color:#64748b;">关联模型：${escapeHtml(catalogObjectType.mappings.join('、'))}</span>`)
  lines.push(
    `<span style="color:#94a3b8;border-bottom:1px solid #e2e8f0;display:inline-block;width:100%;">属性（${catalogObjectType.properties.length}）</span>`
  )
  for (const property of catalogObjectType.properties) {
    const tags = [property.identifier ? '标识' : '', property.title ? '标题' : ''].filter(Boolean).join('，')
    const tagSuffix = tags ? `（${tags}）` : ''
    lines.push(`${escapeHtml(property.display_name || property.name)}：${escapeHtml(property.type)}${tagSuffix}`)
  }
  return lines.join('<br/>')
}
