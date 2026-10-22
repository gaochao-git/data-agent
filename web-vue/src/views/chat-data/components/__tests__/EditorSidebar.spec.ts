import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import EditorSidebar from '../sidebars/EditorSidebar.vue'

const datasource = {
  id: 'ds_1',
  group_name: 'hive-localhost',
  ds_tables: [
    { table_name: 'orders', description: '订单', columns: [{ name: 'id', type: 'int' }] },
  ],
}

const groupedDatasource = {
  id: 'ds_2',
  group_name: 'hive-localhost',
  ds_tables: [
    { table_name: 'orders', description: '订单', columns: [], groups: [{ table_group_name: '交易域' }] },
    { table_name: 'products', description: '商品', columns: [], groups: [{ table_group_name: '交易域' }] },
  ],
}

const twoGroupDatasource = {
  id: 'ds_3',
  group_name: 'hive-localhost',
  ds_tables: [
    { table_name: 'orders', description: '订单', columns: [], groups: [{ table_group_name: '交易域' }] },
    { table_name: 'products', description: '商品', columns: [], groups: [{ table_group_name: '商品域' }] },
  ],
}

const mountSidebar = (props: Record<string, unknown> = {}) => {
  return mount(EditorSidebar, {
    props: { datasource, ...props },
    // tooltip 经 <Teleport to="body"> 挂到 body，stub 后内容留在组件内便于断言
    global: { stubs: { teleport: true } },
  })
}

describe('EditorSidebar 表列表刷新', () => {
  it('点击刷新按钮向父组件发出 refresh 事件', async () => {
    const wrapper = mountSidebar()

    await wrapper.get('[data-testid="editor-tables-refresh"]').trigger('click')

    expect(wrapper.emitted('refresh')).toHaveLength(1)
  })

  it('未选择数据源时刷新按钮禁用且不触发事件', async () => {
    const wrapper = mountSidebar({ datasource: null })
    const button = wrapper.get('[data-testid="editor-tables-refresh"]')

    expect(button.attributes('disabled')).toBeDefined()
    await button.trigger('click')
    expect(wrapper.emitted('refresh')).toBeUndefined()
  })

  it('刷新中按钮禁用并显示旋转图标', async () => {
    const wrapper = mountSidebar({ refreshing: true })
    const button = wrapper.get('[data-testid="editor-tables-refresh"]')

    expect(button.attributes('disabled')).toBeDefined()
    expect(button.get('i').classes()).toContain('fa-spin')
    await button.trigger('click')
    expect(wrapper.emitted('refresh')).toBeUndefined()
  })
})

describe('EditorSidebar 分组折叠', () => {
  it('分组默认折叠，点击分组后才显示表', async () => {
    const wrapper = mountSidebar({ datasource: groupedDatasource })

    expect(wrapper.text()).toContain('交易域')
    expect(wrapper.text()).not.toContain('orders')

    await wrapper.get('[data-testid="editor-group-交易域"]').trigger('click')
    expect(wrapper.text()).toContain('orders')
  })

  it('搜索时自动展开分组，清空搜索后恢复折叠', async () => {
    const wrapper = mountSidebar({ datasource: groupedDatasource })
    const search = wrapper.get('input[placeholder="支持中英文搜索表名/字段"]')

    await search.setValue('products')
    expect(wrapper.text()).toContain('products')

    await search.setValue('')
    expect(wrapper.text()).not.toContain('products')
  })
})

describe('EditorSidebar 一键展开/折叠分组', () => {
  it('默认折叠时点按钮展开全部分组', async () => {
    const wrapper = mountSidebar({ datasource: twoGroupDatasource })
    expect(wrapper.text()).toContain('交易域')
    expect(wrapper.text()).not.toContain('orders')

    await wrapper.get('[data-testid="editor-groups-toggle-all"]').trigger('click')

    expect(wrapper.text()).toContain('orders')
    expect(wrapper.text()).toContain('products')
  })

  it('全部展开时点按钮收起全部分组', async () => {
    const wrapper = mountSidebar({ datasource: twoGroupDatasource })
    const button = wrapper.get('[data-testid="editor-groups-toggle-all"]')

    await button.trigger('click')
    expect(wrapper.text()).toContain('orders')

    await button.trigger('click')
    expect(wrapper.text()).not.toContain('orders')
    expect(wrapper.text()).not.toContain('products')
  })

  it('无分组时不显示一键按钮', () => {
    const wrapper = mountSidebar()

    expect(wrapper.find('[data-testid="editor-groups-toggle-all"]').exists()).toBe(false)
  })

  it('搜索中按钮禁用（分组已被自动展开，避免点击无反馈）', async () => {
    const wrapper = mountSidebar({ datasource: twoGroupDatasource })
    const search = wrapper.get('input[placeholder="支持中英文搜索表名/字段"]')

    await search.setValue('orders')

    expect(wrapper.get('[data-testid="editor-groups-toggle-all"]').attributes('disabled')).toBeDefined()
  })
})
