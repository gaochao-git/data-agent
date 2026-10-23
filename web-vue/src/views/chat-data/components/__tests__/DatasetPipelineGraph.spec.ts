import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import DatasetPipelineGraph from '../ontology/DatasetPipelineGraph.vue'
import type { DatasetPipelineEdge, DatasetPipelineNode } from '../../types'

const nodes: DatasetPipelineNode[] = [
  {
    node_id: 'src',
    node_type: 'source',
    node_name: '源数据',
    config: { dataset_name: 'orders', member_datasource_ids: ['member_1'], limit: 5000 }
  },
  {
    node_id: 'transform_1',
    node_type: 'transform',
    node_name: '过滤',
    config: { transform_type: 'filter', conditions: [{ column: 'status', operator: 'eq', value: 'shipped' }] }
  },
  {
    node_id: 'out',
    node_type: 'output',
    node_name: '输出',
    config: { dataset_name: 'orders_cleaned', layer: 'cleaned' }
  }
]

const edges: DatasetPipelineEdge[] = [
  { edge_id: 'edge_1', from_node_id: 'src', to_node_id: 'transform_1' },
  { edge_id: 'edge_2', from_node_id: 'transform_1', to_node_id: 'out' }
]

describe('DatasetPipelineGraph', () => {
  it('渲染 source / transform / output 节点与连线，并标记未保存节点', () => {
    const wrapper = mount(DatasetPipelineGraph, {
      props: { nodes, edges, selectedNodeId: 'src', dirtyNodeIds: ['transform_1'] }
    })

    expect(wrapper.findAll('[data-node-id]')).toHaveLength(3)
    expect(wrapper.find('[data-node-id="src"]').classes()).toContain('border-blue-500')
    expect(wrapper.find('[data-node-id="transform_1"] [data-testid="pipeline-node-dirty"]').exists()).toBe(true)
    expect(wrapper.find('[data-node-id="src"] [data-testid="pipeline-node-dirty"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('filter · status eq shipped')
    expect(wrapper.findAll('svg > path')).toHaveLength(2)
  })

  it('点击节点回传 node_id 供配置面板选中', async () => {
    const wrapper = mount(DatasetPipelineGraph, { props: { nodes, edges } })

    await wrapper.find('[data-node-id="out"]').trigger('click')

    expect(wrapper.emitted('select')).toEqual([['out']])
  })

  it('空节点时给出空态提示', () => {
    const wrapper = mount(DatasetPipelineGraph, { props: { nodes: [], edges: [] } })

    expect(wrapper.text()).toContain('暂无 Pipeline 节点')
  })
})
