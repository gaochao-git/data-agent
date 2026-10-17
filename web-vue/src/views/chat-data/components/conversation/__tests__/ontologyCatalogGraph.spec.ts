/**
 * 本体目录图谱纯函数单测：YAML 解析（成功/降级）、verbalize 占位替换、图 option 组装。
 * echarts 渲染本身不在覆盖范围（组件薄壳，逻辑全部在纯函数层）。
 */
import { describe, expect, it } from 'vitest'
import {
  buildGraphOption,
  objectStatusColor,
  objectStatusLabel,
  parseOntologyCatalog,
  renderVerbalize,
} from '../ontologyCatalogGraph'

const SAMPLE_YAML = `comment: 本体目录（系统生成，只读）
objects:
- object: sales_order
  display_name: 订单
  status: certified
  description: 销售订单主表
  mappings:
  - semantic_config_id: sc_1
    semantic_config_name: 订单模型
    column_mappings:
      order_no: 订单编号
  properties:
  - name: order_no
    display_name: 订单编号
    type: String
    identifier: true
    title: true
  - name: amount
    display_name: 订单金额
    type: Decimal
    identifier: false
    title: false
- object: customer
  display_name: 用户
  status: experimental
  description: 下单客户
  properties:
  - name: user_id
    display_name: 用户ID
    type: String
    identifier: true
    title: true
relations:
- name: order_belongs_to_customer
  from: sales_order
  to: customer
  multiplicity: many_to_one
  from_display_name: 下单用户
  to_display_name: 名下订单
  from_verbalize: '{sales_order} 归属于 {customer}'
  to_verbalize: '{customer} 名下有 {sales_order}'
`

describe('parseOntologyCatalog', () => {
  it('解析正常目录：objects 与 relations 逐字段落位', () => {
    const catalog = parseOntologyCatalog(SAMPLE_YAML)
    expect(catalog).not.toBeNull()
    expect(catalog!.objects.map(item => item.object)).toEqual(['sales_order', 'customer'])
    expect(catalog!.objects[0]!.display_name).toBe('订单')
    expect(catalog!.objects[0]!.properties).toHaveLength(2)
    // mappings 提取语义模型展示名（tooltip 辅助信息）
    expect(catalog!.objects[0]!.mappings).toEqual(['订单模型'])
    expect(catalog!.objects[1]!.mappings).toEqual([])
    expect(catalog!.relations).toHaveLength(1)
    expect(catalog!.relations[0]).toMatchObject({
      name: 'order_belongs_to_customer',
      from: 'sales_order',
      to: 'customer',
      multiplicity: 'many_to_one',
    })
  })

  it('非法 YAML 返回 null（降级，不抛错）', () => {
    expect(parseOntologyCatalog('objects: [unclosed')).toBeNull()
    expect(parseOntologyCatalog('\t- 非法缩进: {')).toBeNull()
  })

  it('缺 objects 或 objects 为空返回 null', () => {
    expect(parseOntologyCatalog('comment: 只有注释')).toBeNull()
    expect(parseOntologyCatalog('objects: []\nrelations: []')).toBeNull()
  })

  it('对象缺 object 名返回 null（结构性损坏不硬渲染）', () => {
    const broken = 'objects:\n- display_name: 无名对象\n'
    expect(parseOntologyCatalog(broken)).toBeNull()
  })

  it('relations 缺省允许通过（孤立对象场景）', () => {
    const solo = 'objects:\n- object: lone\n  display_name: 孤立对象\n'
    const catalog = parseOntologyCatalog(solo)
    expect(catalog).not.toBeNull()
    expect(catalog!.relations).toEqual([])
  })
})

describe('renderVerbalize', () => {
  const args = ['sales_order', '订单', 'customer', '用户'] as const

  it('替换 {from}/{to} 与具体对象名两种占位写法', () => {
    expect(renderVerbalize('{from} 归属于 {to}', ...args)).toBe('订单 归属于 用户')
    expect(renderVerbalize('{sales_order} 归属于 {customer}', ...args)).toBe('订单 归属于 用户')
  })

  it('未知占位符保留原文（不猜修饰词）', () => {
    expect(renderVerbalize('{order} 引用 {to}', ...args)).toBe('{order} 引用 用户')
  })

  it('空模板返回空串', () => {
    expect(renderVerbalize('', ...args)).toBe('')
  })
})

describe('objectStatusColor', () => {
  it('四态各有语义色，未知状态回落灰色', () => {
    expect(objectStatusColor('certified')).toBe('#16a34a')
    expect(objectStatusColor('experimental')).toBe('#2563eb')
    expect(objectStatusColor('draft')).toBe('#94a3b8')
    expect(objectStatusColor('deprecated')).toBe('#dc2626')
    expect(objectStatusColor('whatever')).toBe('#64748b')
  })
})

describe('objectStatusLabel', () => {
  it('四态中文名，未知状态回落「未知」', () => {
    expect(objectStatusLabel('certified')).toBe('权威')
    expect(objectStatusLabel('experimental')).toBe('试用')
    expect(objectStatusLabel('draft')).toBe('草稿')
    expect(objectStatusLabel('deprecated')).toBe('已废弃')
    expect(objectStatusLabel('whatever')).toBe('未知')
  })
})

describe('buildGraphOption', () => {
  const getGraph = (option: ReturnType<typeof buildGraphOption>) =>
    (option.series as Array<Record<string, unknown>>).find(item => item.type === 'graph')!

  it('三要素全上图：节点=对象+属性，边=本体关系+属性挂接', () => {
    const catalog = parseOntologyCatalog(SAMPLE_YAML)!
    const option = buildGraphOption(catalog)
    const graph = getGraph(option)
    const nodes = graph.data as Array<Record<string, unknown>>
    const links = graph.links as Array<Record<string, unknown>>
    // 2 对象 + 3 属性（sales_order 2 个 + customer 1 个）
    expect(nodes).toHaveLength(5)
    const objectNodes = nodes.filter(node => node.catalogObject)
    const propertyNodes = nodes.filter(node => node.catalogProperty)
    expect(objectNodes).toHaveLength(2)
    expect(propertyNodes).toHaveLength(3)
    // 1 条本体关系 + 3 条属性挂接
    const relationLinks = links.filter(link => 'relation' in link)
    expect(relationLinks).toHaveLength(1)
    expect(links).toHaveLength(4)
    // 关系边来自本体 relations；箭头方向对齐 Palantir Vertex 默认（many_to_one 从
    // one 端指向 many 端）：source=to 对象（one 侧），target=from 对象（many 侧）
    expect(relationLinks[0]).toMatchObject({ source: 'customer', target: 'sales_order' })
    expect(graph.edgeSymbol).toEqual(['none', 'arrow'])
    // 力导向布局（对齐官方 Vertex 默认 Auto）：可拖拽缩放；环形坐标仅作初值
    expect(graph.layout).toBe('force')
    expect(graph.force).toMatchObject({ repulsion: 320, gravity: 0.16, layoutAnimation: true })
    expect(graph.roam).toBe(true)
    expect(objectNodes[0]).toHaveProperty('x')
    expect(objectNodes[0]).toHaveProperty('y')
  })

  it('属性节点样式按角色区分：标识=蓝、标题=琥珀、普通=浅灰', () => {
    const catalog = parseOntologyCatalog(SAMPLE_YAML)!
    const option = buildGraphOption(catalog)
    const nodes = (getGraph(option).data as Array<Record<string, unknown>>).filter(node => node.catalogProperty)
    const byId = new Map(nodes.map(node => [node.name, node]))
    const identifierNode = byId.get('sales_order.order_no') as Record<string, unknown>
    const amountNode = byId.get('sales_order.amount') as Record<string, unknown>
    expect((identifierNode.itemStyle as Record<string, string>).color).toBe('#2563eb')
    expect(identifierNode.symbolSize).toBe(16)
    expect((amountNode.itemStyle as Record<string, string>).color).toBe('#e2e8f0')
    expect(amountNode.symbolSize).toBe(12)
  })

  it('关系边 label 与箭头同向（取 to_display_name），多重性只进 tooltip；初值坐标成环不重叠', () => {
    const catalog = parseOntologyCatalog(SAMPLE_YAML)!
    const option = buildGraphOption(catalog)
    const graph = getGraph(option)
    const relationLink = (graph.links as Array<Record<string, unknown>>).find(link => 'relation' in link)!
    // 箭头 customer→sales_order（one→many），label 描述到达的 many 侧（订单）=「名下订单」
    expect((relationLink.label as Record<string, unknown>).formatter).toBe('名下订单')
    // 两个对象节点在圆周上不重合（两节点时上下对称：x 相同、y 相反，比较坐标对）
    const nodes = (graph.data as Array<Record<string, unknown>>).filter(node => node.catalogObject)
    expect([nodes[0]!.x, nodes[0]!.y]).not.toEqual([nodes[1]!.x, nodes[1]!.y])
  })

  it('对象 tooltip：状态中文+色点、关联模型、属性单独分区标题', () => {
    const catalog = parseOntologyCatalog(SAMPLE_YAML)!
    const option = buildGraphOption(catalog)
    const formatter = (option.tooltip as Record<string, unknown>).formatter as (params: unknown) => string
    const html = formatter({ data: { name: 'sales_order' } })
    expect(html).toContain('订单')
    expect(html).toContain('权威')
    expect(html).toContain('关联模型：订单模型')
    expect(html).toContain('属性（2）')
    expect(html).toContain('订单编号：String（标识，标题）')
  })

  it('属性 tooltip：类型/角色 + 所属对象', () => {
    const catalog = parseOntologyCatalog(SAMPLE_YAML)!
    const option = buildGraphOption(catalog)
    const formatter = (option.tooltip as Record<string, unknown>).formatter as (params: unknown) => string
    const html = formatter({ data: { catalogProperty: { name: 'user_id', display_name: '用户ID', type: 'String', identifier: true, title: true }, ownerObject: 'customer' } })
    expect(html).toContain('类型：String')
    expect(html).toContain('角色：标识，标题')
    expect(html).toContain('所属对象：用户')
  })
})
