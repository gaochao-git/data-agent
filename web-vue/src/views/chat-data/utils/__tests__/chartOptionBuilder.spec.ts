import { describe, expect, it } from 'vitest'
import { buildEChartsOption, buildGraphSeriesData } from '../chartOptionBuilder'

const toNumber = (value: unknown) => {
  const n = Number(value)
  return Number.isFinite(n) ? n : null
}

const rows = [
  { city: '北京', sales: 100, cost: 60 },
  { city: '上海', sales: 200, cost: 90 },
]
const encoding = { xField: 'city', yFields: ['sales', 'cost'] }

describe('buildEChartsOption', () => {
  it('table/kpi 返回空对象（由调用方非 ECharts 分支渲染）', () => {
    expect(buildEChartsOption({ type: 'table', rows, encoding, toNumber })).toEqual({})
    expect(buildEChartsOption({ type: 'kpi', rows, encoding, toNumber })).toEqual({})
  })

  it('bar 生成柱状 series 且 borderRadius 为顶部圆角', () => {
    const option = buildEChartsOption({ type: 'bar', rows, encoding, toNumber })
    const series = option.series as Array<Record<string, unknown>>
    expect(series).toHaveLength(2)
    expect(series[0].type).toBe('bar')
    expect(series[0].data).toEqual([100, 200])
    expect((series[0].itemStyle as Record<string, unknown>).borderRadius).toEqual([4, 4, 0, 0])
    expect((option.xAxis as Record<string, unknown>).type).toBe('category')
  })

  it('horizontal_bar 交换轴且圆角为右侧', () => {
    const option = buildEChartsOption({ type: 'horizontal_bar', rows, encoding, toNumber })
    const series = option.series as Array<Record<string, unknown>>
    expect(series[0].type).toBe('bar')
    expect((series[0].itemStyle as Record<string, unknown>).borderRadius).toEqual([0, 4, 4, 0])
    expect((option.xAxis as Record<string, unknown>).type).toBe('value')
    expect((option.yAxis as Record<string, unknown>).type).toBe('category')
  })

  it('line/area 使用 line series，area 带 areaStyle', () => {
    const line = buildEChartsOption({ type: 'line', rows, encoding, toNumber })
    expect((line.series as Array<Record<string, unknown>>)[0].type).toBe('line')
    const area = buildEChartsOption({ type: 'area', rows, encoding, toNumber })
    expect((area.series as Array<Record<string, unknown>>)[0].areaStyle).toBeDefined()
  })

  it('pie 只取第一个数值列', () => {
    const option = buildEChartsOption({ type: 'pie', rows, encoding, toNumber })
    const series = option.series as Array<Record<string, unknown>>
    expect(series).toHaveLength(1)
    expect(series[0].type).toBe('pie')
    expect(series[0].data).toEqual([
      { name: '北京', value: 100 },
      { name: '上海', value: 200 },
    ])
  })

  it('combo 最多取两个数值列并启用双轴', () => {
    const option = buildEChartsOption({
      type: 'combo',
      rows,
      encoding: { xField: 'city', yFields: ['sales', 'cost', 'extra'] },
      toNumber,
    })
    const series = option.series as Array<Record<string, unknown>>
    expect(series).toHaveLength(2)
    expect(series[0].type).toBe('bar')
    expect(series[1].type).toBe('line')
    expect(series[0].yAxisIndex).toBe(0)
    expect(series[1].yAxisIndex).toBe(1)
    expect(option.yAxis).toHaveLength(2)
  })

  it('funnel 无图例且降序数据', () => {
    const option = buildEChartsOption({ type: 'funnel', rows, encoding, toNumber })
    const series = option.series as Array<Record<string, unknown>>
    expect((option.legend as Record<string, unknown>).show).toBe(false)
    expect(series[0].type).toBe('funnel')
  })

  it('map 未就绪时返回空对象', () => {
    expect(buildEChartsOption({ type: 'map', rows, encoding, toNumber })).toEqual({})
  })

  it('graph 使用预构建数据', () => {
    const graphData = {
      nodes: [{ name: 'A', value: 3 }, { name: 'B', value: 1 }],
      links: [{ source: 'A', target: 'B', value: 2 }],
    }
    const option = buildEChartsOption({ type: 'graph', rows, encoding, graphData, toNumber })
    const series = option.series as Array<Record<string, unknown>>
    expect(series[0].type).toBe('graph')
    expect(series[0].data).toEqual(graphData.nodes)
  })
})

describe('buildGraphSeriesData', () => {
  it('从 source/target 行数据聚合节点与链接', () => {
    const data = buildGraphSeriesData(
      [
        { user: '张三', product: '商品A', amount: 5 },
        { user: '张三', product: '商品B', amount: 3 },
      ],
      { xField: 'user', yFields: ['product'] },
      'amount',
      toNumber,
    )
    expect(data.nodes.map(node => node.name)).toEqual(['张三', '商品A', '商品B'])
    expect(data.links).toHaveLength(2)
  })
})
