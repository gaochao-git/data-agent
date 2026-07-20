import { describe, expect, it } from 'vitest'
import { computed, ref } from 'vue'
import { useTableSchemaPanel } from '../useTableSchemaPanel'

interface TestTable {
  table_name: string
  description?: string
  business_desc?: string
}

const tables: TestTable[] = [
  { table_name: 'orders', description: '订单表', business_desc: '订单核心业务' },
  { table_name: 'users', description: '用户表' },
  { table_name: 'order_items' },
]

describe('useTableSchemaPanel', () => {
  it('默认返回全部表', () => {
    const panel = useTableSchemaPanel(ref(tables))
    expect(panel.filteredTables.value).toHaveLength(3)
  })

  it('按表名/备注/业务说明过滤，大小写不敏感', () => {
    const panel = useTableSchemaPanel(ref(tables))
    panel.searchText.value = 'ORDER'
    expect(panel.filteredTables.value.map(t => t.table_name)).toEqual(['orders', 'order_items'])
    panel.searchText.value = '用户'
    expect(panel.filteredTables.value.map(t => t.table_name)).toEqual(['users'])
    panel.searchText.value = '核心业务'
    expect(panel.filteredTables.value.map(t => t.table_name)).toEqual(['orders'])
    panel.searchText.value = '不存在'
    expect(panel.filteredTables.value).toHaveLength(0)
  })

  it('空白搜索词返回全部表', () => {
    const panel = useTableSchemaPanel(ref(tables))
    panel.searchText.value = '   '
    expect(panel.filteredTables.value).toHaveLength(3)
  })

  it('toggleTable 展开/收起切换', () => {
    const panel = useTableSchemaPanel(ref(tables))
    expect(panel.expandedTables.value.has('orders')).toBe(false)
    panel.toggleTable('orders')
    expect(panel.expandedTables.value.has('orders')).toBe(true)
    panel.toggleTable('users')
    expect(panel.expandedTables.value.has('users')).toBe(true)
    panel.toggleTable('orders')
    expect(panel.expandedTables.value.has('orders')).toBe(false)
    expect(panel.expandedTables.value.has('users')).toBe(true)
  })

  it('buildTableTitle 拼装悬浮标题', () => {
    const panel = useTableSchemaPanel(ref(tables))
    expect(panel.buildTableTitle(tables[0]!)).toBe('表名：orders\n备注：订单表\n表业务说明：订单核心业务')
    expect(panel.buildTableTitle(tables[1]!)).toBe('表名：users\n备注：用户表')
    expect(panel.buildTableTitle(tables[2]!)).toBe('表名：order_items\n备注：-')
  })

  it('支持 computed 数据源并跟随变化', () => {
    const source = ref<TestTable[]>(tables)
    const panel = useTableSchemaPanel(computed(() => source.value))
    expect(panel.filteredTables.value).toHaveLength(3)
    source.value = tables.slice(0, 1)
    expect(panel.filteredTables.value).toHaveLength(1)
  })
})
