import { describe, expect, it } from 'vitest'

import {
  crossMappingPropertyConflicts,
  defaultRelationStatusTarget,
  haveMappingsChanged,
  normalizeMappingsForCompare,
  ontologyMappingCount,
  relationStatusTargets,
  relationStatusUpperBound,
  removeMappingPropertyKey,
  renameMappingPropertyKey,
  staleMappingPropertyNames,
  type OntologyMappingComparable
} from '../ontology/ontologyMeta'

// 服务端详情行附带的视图键（DAO 固定输出），比较映射时必须忽略
type ServerMappingRow = OntologyMappingComparable & { object_id: number; binding_type: string }

describe('本体映射比较口径', () => {
  it('忽略服务端附加键 object_id/binding_type 与键顺序：对象有映射时不再误判为映射变更', () => {
    const serverRows: ServerMappingRow[] = [
      {
        object_id: 7,
        binding_type: 'semantic',
        semantic_config_id: 'sc_orders',
        column_mappings: { order_no: '订单编号', amount: '金额' }
      }
    ]
    const formRows: OntologyMappingComparable[] = [
      { semantic_config_id: 'sc_orders', column_mappings: { amount: '金额', order_no: '订单编号' } }
    ]

    expect(normalizeMappingsForCompare(serverRows)).toBe(normalizeMappingsForCompare(formRows))
    expect(haveMappingsChanged(serverRows, formRows)).toBe(false)
  })

  it('忽略列映射键值两侧空白与空键', () => {
    const before: OntologyMappingComparable[] = [
      { semantic_config_id: ' sc_1 ', column_mappings: { ' order_no ': ' 订单编号 ', '': '脏数据' } }
    ]
    const after: OntologyMappingComparable[] = [
      { semantic_config_id: 'sc_1', column_mappings: { order_no: '订单编号' } }
    ]
    expect(haveMappingsChanged(before, after)).toBe(false)
  })

  it('维度值/语义模型/映射行数量变化才判定为映射变更', () => {
    const base: OntologyMappingComparable[] = [{ semantic_config_id: 'sc_1', column_mappings: { order_no: '订单编号' } }]
    expect(haveMappingsChanged(base, [{ semantic_config_id: 'sc_1', column_mappings: { order_no: '编号' } }])).toBe(true)
    expect(haveMappingsChanged(base, [{ semantic_config_id: 'sc_2', column_mappings: { order_no: '订单编号' } }])).toBe(true)
    expect(haveMappingsChanged(base, [])).toBe(true)
    expect(haveMappingsChanged(undefined, [])).toBe(false)
  })
})

describe('本体属性变更联动映射键', () => {
  it('属性改名同步映射键并保留原维度值', () => {
    const mappings: OntologyMappingComparable[] = [
      { semantic_config_id: 'sc_1', column_mappings: { order_no: '订单编号', amount: '金额' } }
    ]
    renameMappingPropertyKey(mappings, 'order_no', 'order_id')
    expect(mappings[0]!.column_mappings).toEqual({ order_id: '订单编号', amount: '金额' })
  })

  it('属性删除同步移除映射键（避免后端因未知属性拒绝提交）', () => {
    const mappings: OntologyMappingComparable[] = [
      { semantic_config_id: 'sc_1', column_mappings: { order_no: '订单编号', amount: '金额' } }
    ]
    removeMappingPropertyKey(mappings, 'order_no')
    expect(mappings[0]!.column_mappings).toEqual({ amount: '金额' })
  })

  it('staleMappingPropertyNames 兜底报告未同步的映射键', () => {
    const mappings: OntologyMappingComparable[] = [
      { semantic_config_id: 'sc_1', column_mappings: { order_no: '订单编号', ghost: '脏键' } }
    ]
    expect(staleMappingPropertyNames(mappings, ['order_no', 'amount'])).toEqual(['ghost'])
    expect(staleMappingPropertyNames(mappings, ['order_no', 'ghost'])).toEqual([])
  })
})

describe('本体关系状态上限与默认目标', () => {
  it('上限 = 两侧对象状态的较低者', () => {
    expect(relationStatusUpperBound('certified', 'experimental')).toBe('experimental')
    expect(relationStatusUpperBound('experimental', 'certified')).toBe('experimental')
    expect(relationStatusUpperBound('draft', 'certified')).toBe('draft')
    expect(relationStatusUpperBound('certified', 'certified')).toBe('certified')
  })

  it('任一端对象已废弃时关系只可级联，不可手动迁移', () => {
    expect(relationStatusUpperBound('deprecated', 'certified')).toBe('deprecated')
    expect(relationStatusTargets('experimental', relationStatusUpperBound('deprecated', 'certified'))).toEqual([])
  })

  it('合法目标排除当前状态且不超过上限', () => {
    expect(relationStatusTargets('experimental', 'certified')).toEqual(['draft', 'certified'])
    expect(relationStatusTargets('certified', 'certified')).toEqual(['draft', 'experimental'])
    expect(relationStatusTargets('draft', 'experimental')).toEqual(['experimental'])
    expect(relationStatusTargets('draft', null)).toEqual([])
  })

  it('默认目标永远不等于当前状态：优先下一档升级，无升级时取最接近的降级', () => {
    expect(defaultRelationStatusTarget('experimental', 'certified')).toBe('certified')
    expect(defaultRelationStatusTarget('draft', 'certified')).toBe('experimental')
    expect(defaultRelationStatusTarget('certified', 'experimental')).toBe('experimental')
    // 已达上限但仍可降级：默认取最接近的降级目标；完全无合法目标时才是 null
    expect(defaultRelationStatusTarget('certified', 'certified')).toBe('experimental')
    expect(defaultRelationStatusTarget('draft', 'draft')).toBeNull()
    for (const current of ['draft', 'experimental', 'certified'] as const) {
      const target = defaultRelationStatusTarget(current, 'certified')
      expect(target).not.toBe(current)
    }
  })
})

describe('本体对象映射计数（列表行只有 mappings_count）', () => {
  it('列表行以 mappings_count 判断认证前置提示', () => {
    expect(ontologyMappingCount({ mappings_count: 1 })).toBe(1)
    expect(ontologyMappingCount({ mappings_count: 0, mappings: [{ semantic_config_id: 'sc_1' }] })).toBe(0)
  })

  it('详情行回退到 mappings 明细，空对象为 0', () => {
    expect(ontologyMappingCount({ mappings: [{ semantic_config_id: 'sc_1' }] })).toBe(1)
    expect(ontologyMappingCount(null)).toBe(0)
    expect(ontologyMappingCount(undefined)).toBe(0)
  })
})


describe('多源映射属性单源规则（与后端硬校验同口径）', () => {
  it('非标识属性出现在多个映射时报冲突，标识属性可跨源', () => {
    const mappings: OntologyMappingComparable[] = [
      { semantic_config_id: 'sc_a', column_mappings: { order_no: '订单编号', amount: '金额' } },
      { semantic_config_id: 'sc_b', column_mappings: { order_no: '订单编号', amount: '金额' } }
    ]
    const conflicts = crossMappingPropertyConflicts(mappings, ['order_no'])
    expect(conflicts).toEqual([{ property_name: 'amount', config_ids: ['sc_a', 'sc_b'] }])
  })

  it('非标识属性单源映射、标识属性跨源均不冲突；空/空白配置忽略', () => {
    const mappings: OntologyMappingComparable[] = [
      { semantic_config_id: 'sc_a', column_mappings: { order_no: '订单编号', amount: '金额' } },
      { semantic_config_id: 'sc_b', column_mappings: { order_no: '订单编号' } }
    ]
    expect(crossMappingPropertyConflicts(mappings, ['order_no'])).toEqual([])
    expect(crossMappingPropertyConflicts([], ['order_no'])).toEqual([])
    expect(crossMappingPropertyConflicts(null, [])).toEqual([])
  })
})
