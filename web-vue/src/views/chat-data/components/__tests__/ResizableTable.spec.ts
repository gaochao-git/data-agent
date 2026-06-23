import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ResizableTable from '../ResizableTable.vue'

const columns = [
  { key: 'id', title: 'ID', width: 80 },
  { key: 'name', title: '名称', width: 160 }
]

const buildRows = (count: number) => Array.from({ length: count }, (_, index) => ({
  id: index + 1,
  name: `第 ${index + 1} 行`
}))

describe('ResizableTable', () => {
  it('renders only visible rows when virtual table is enabled for large data', async () => {
    const wrapper = mount(ResizableTable, {
      props: {
        columns,
        data: buildRows(10000),
        virtualThreshold: 200,
        rowHeight: 42,
        overscan: 8
      },
      attachTo: document.body
    })

    await wrapper.vm.$nextTick()

    expect(wrapper.text()).toContain('第 1 行')
    expect(wrapper.text()).not.toContain('第 10000 行')
    expect(wrapper.findAll('.absolute.left-0.flex').length).toBeLessThan(80)

    wrapper.unmount()
  })

  it('keeps full rendering when virtual table is disabled', () => {
    const wrapper = mount(ResizableTable, {
      props: {
        columns,
        data: buildRows(20),
        virtual: false,
        virtualThreshold: 1
      }
    })

    expect(wrapper.text()).toContain('第 20 行')
    expect(wrapper.findAll('.absolute.left-0.flex').length).toBe(0)
  })

  it('does not virtualize auto-wrapped rows because row height is variable', () => {
    const wrapper = mount(ResizableTable, {
      props: {
        columns,
        data: buildRows(20),
        autoWrap: true,
        virtualThreshold: 1
      }
    })

    expect(wrapper.text()).toContain('第 20 行')
    expect(wrapper.findAll('.absolute.left-0.flex').length).toBe(0)
  })
})
