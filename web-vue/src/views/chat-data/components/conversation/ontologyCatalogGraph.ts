/**
 * 本体目录图谱的数据转换纯函数（配合 OntologyCatalogGraph.vue 使用）。
 * 输入 = 物化产物 _ontology_catalog.yaml 的文本，输出 = echarts graph option。
 * 三要素全上图：对象节点 + 属性节点（环绕各自对象的小节点）+
 * 关系边（本体 relations 一比一，箭头=one端→many端，对齐 Palantir Vertex 默认：
 * many_to_one 链接的箭头从 one 侧指向 many 侧）。
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

export interface CatalogObject {
  object: string
  display_name: string
  status: string
  description: string
  properties: CatalogProperty[]
  /** 关联语义模型展示名（目录 YAML 的 mappings，辅助信息用于 tooltip） */
  mappings: string[]
}

export interface CatalogRelation {
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
  objects: CatalogObject[]
  relations: CatalogRelation[]
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

export function objectStatusLabel(status: string): string {
  return STATUS_LABELS[status] || '未知'
}

export function objectStatusColor(status: string): string {
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

/** 解析本体目录文本。objects 必须非空且每项有 object 名；relations 允许缺省（孤立对象）。 */
export function parseOntologyCatalog(content: string): OntologyCatalog | null {
  let doc: unknown
  try {
    doc = load(content)
  } catch {
    return null
  }
  if (!doc || typeof doc !== 'object') return null
  const rawObjects = (doc as { objects?: unknown }).objects
  if (!Array.isArray(rawObjects) || rawObjects.length === 0) return null
  const objects: CatalogObject[] = []
  for (const raw of rawObjects) {
    if (!raw || typeof raw !== 'object') return null
    const item = raw as Record<string, unknown>
    const objectName = typeof item.object === 'string' ? item.object.trim() : ''
    if (!objectName) return null
    objects.push({
      object: objectName,
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
  const rawRelations = (doc as { relations?: unknown }).relations
  const relations: CatalogRelation[] = Array.isArray(rawRelations)
    ? (rawRelations.filter(item => !!item && typeof item === 'object') as CatalogRelation[])
    : []
  return { objects, relations }
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
  catalogObject?: CatalogObject
  catalogProperty?: CatalogProperty
  ownerObject?: string
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
  const displayByObject = new Map(catalog.objects.map(item => [item.object, item.display_name || item.object]))

  // 对象节点环形初值；属性节点初值环绕各自对象（力导向会在此基础上松弛）
  const objectCount = catalog.objects.length
  const objectRadius = Math.max(260, objectCount * 70)
  const propertyRadius = 104
  catalog.objects.forEach((item, index) => {
    const angle = (2 * Math.PI * index) / objectCount - Math.PI / 2
    const x = Math.round(objectRadius * Math.cos(angle))
    const y = Math.round(objectRadius * Math.sin(angle))
    nodes.push({
      id: item.object,
      name: item.object,
      x,
      y,
      draggable: true,
      symbolSize: Math.min(56, 40 + item.properties.length),
      itemStyle: { color: objectStatusColor(item.status) },
      label: {
        show: true,
        position: 'bottom',
        formatter: item.display_name || item.object,
        fontSize: 13,
        color: '#1e293b',
      },
      nodeKind: 'object',
      catalogObject: item,
    })
    const properties = showProperties ? item.properties : []
    properties.forEach((property, propertyIndex) => {
      const propertyAngle = (2 * Math.PI * propertyIndex) / properties.length - Math.PI / 2
      const angleCos = Math.cos(propertyAngle)
      const angleSin = Math.sin(propertyAngle)
      const style = propertyNodeStyle(property)
      nodes.push({
        id: `${item.object}.${property.name}`,
        name: `${item.object}.${property.name}`,
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
        ownerObject: item.object,
      })
      propertyLinks.push({
        source: item.object,
        target: `${item.object}.${property.name}`,
        lineStyle: { color: '#e2e8f0', width: 1, type: 'dashed' },
      })
    })
  })

  for (const relation of catalog.relations) {
    const fromDisplay = displayByObject.get(relation.from) || relation.from
    const toDisplay = displayByObject.get(relation.to) || relation.to
    // 边 label 与箭头同向（one→many，对齐 Palantir Vertex 默认）：取 to_display_name——
    // 官方约定某侧 display name 描述「沿链路到达该侧」的称呼（link-types-overview），
    // 箭头从 one 走向 many，label 应描述到达的 many 侧（即反向展示名 to_display_name）。
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
  const objectByName = new Map(catalog.objects.map(item => [item.object, item]))
  const { nodes, relationLinks, propertyLinks } = layoutCatalog(catalog, options.showProperties === true)
  // 属性折叠时不参与布局，属性挂接边一并隐藏
  const links = options.showProperties === true ? [...relationLinks, ...propertyLinks] : relationLinks

  return {
    tooltip: {
      show: true,
      confine: true,
      trigger: 'item',
      formatter: params => formatGraphTooltip(params, objectByName),
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

const formatGraphTooltip = (params: unknown, objectByName: Map<string, CatalogObject>): string => {
  const data = (params as { data?: Record<string, unknown> }).data
  if (!data) return ''
  // 关系边
  if ('relation' in data) {
    const link = data as unknown as { relation: CatalogRelation; fromDisplay: string; toDisplay: string }
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
    const node = data as unknown as { catalogProperty: CatalogProperty; ownerObject?: string }
    const property = node.catalogProperty
    const owner = node.ownerObject ? objectByName.get(node.ownerObject) : undefined
    const tags = [property.identifier ? '标识' : '', property.title ? '标题' : ''].filter(Boolean).join('，')
    const lines = [
      `<b>${escapeHtml(property.display_name || property.name)}</b>`,
      `类型：${escapeHtml(property.type)}`,
      tags ? `角色：${escapeHtml(tags)}` : '',
    ]
    if (owner) lines.push(`所属对象：${escapeHtml(owner.display_name || owner.object)}`)
    return lines.filter(Boolean).join('<br/>')
  }
  // 对象节点 tooltip 分层：①名称+API名 ②状态徽章（色块） ③描述/关联模型（带标签前缀） ④属性分区
  const catalogObject = objectByName.get(String(data.name ?? ''))
  if (!catalogObject) return ''
  const statusColor = objectStatusColor(catalogObject.status)
  const titleText = escapeHtml(catalogObject.display_name || catalogObject.object)
  const lines = [
    `<b style="font-size:13px;">${titleText}</b>&nbsp;<span style="color:#94a3b8;font-size:11px;">${escapeHtml(catalogObject.object)}</span>`,
    `<span style="color:#64748b;">对象状态：</span><span style="display:inline-block;background:${statusColor};color:#fff;border-radius:8px;padding:0 7px;font-size:11px;line-height:16px;">${escapeHtml(objectStatusLabel(catalogObject.status))}</span>`,
  ]
  if (catalogObject.description) lines.push(`<span style="color:#64748b;">描述：${escapeHtml(catalogObject.description)}</span>`)
  if (catalogObject.mappings.length) lines.push(`<span style="color:#64748b;">关联模型：${escapeHtml(catalogObject.mappings.join('、'))}</span>`)
  lines.push(
    `<span style="color:#94a3b8;border-bottom:1px solid #e2e8f0;display:inline-block;width:100%;">属性（${catalogObject.properties.length}）</span>`
  )
  for (const property of catalogObject.properties) {
    const tags = [property.identifier ? '标识' : '', property.title ? '标题' : ''].filter(Boolean).join('，')
    const tagSuffix = tags ? `（${tags}）` : ''
    lines.push(`${escapeHtml(property.display_name || property.name)}：${escapeHtml(property.type)}${tagSuffix}`)
  }
  return lines.join('<br/>')
}
