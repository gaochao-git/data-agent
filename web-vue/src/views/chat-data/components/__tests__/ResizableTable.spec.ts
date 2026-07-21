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

  it('displays query values without changing numeric formatting', () => {
    const wrapper = mount(ResizableTable, {
      props: {
        columns: [{ key: 'value', title: '结果' }],
        data: [{ value: 10130.089787 }, { value: '12345678901234567890' }],
        virtual: false,
      },
    })

    expect(wrapper.text()).toContain('10130.089787')
    expect(wrapper.text()).toContain('12345678901234567890')
    expect(wrapper.text()).not.toContain('10,130.09')
  })

  it('renders a visible resize indicator with an expanded drag target for each column', () => {
    const wrapper = mount(ResizableTable, {
      props: {
        columns,
        data: buildRows(1),
        virtual: false,
      },
    })

    const handles = wrapper.findAll('[data-testid="column-resize-handle"]')
    expect(handles).toHaveLength(columns.length)
    expect(handles[0]!.classes()).toContain('w-3')
    expect(handles[0]!.classes()).toContain('justify-end')
    expect(handles[0]!.find('span').classes()).toContain('bg-slate-300')
  })

  it('highlights the resize indicator while its column is being dragged', async () => {
    const wrapper = mount(ResizableTable, {
      props: {
        columns,
        data: buildRows(1),
        virtual: false,
      },
    })

    const handle = wrapper.find('[data-testid="column-resize-handle"]')
    await handle.trigger('mousedown', { clientX: 100 })

    expect(handle.find('span').classes()).toContain('!bg-[#1677FF]')

    document.dispatchEvent(new MouseEvent('mouseup'))
    await wrapper.vm.$nextTick()

    expect(handle.find('span').classes()).not.toContain('!bg-[#1677FF]')
  })
})
