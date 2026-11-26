import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { cloneVNode, defineComponent, h, isVNode } from 'vue'
import type { PropType } from 'vue'

const apiMocks = vi.hoisted(() => ({
  downloadMcpServerTemplate: vi.fn(),
  downloadPluginTemplate: vi.fn(),
  getAdminPlugins: vi.fn(),
  installPlugin: vi.fn(),
  probeAdminPluginMcpServerTools: vi.fn(),
  setAdminPluginEnabled: vi.fn(),
  setAdminPluginPermissions: vi.fn(),
  uninstallPlugin: vi.fn()
}))

const elMocks = vi.hoisted(() => ({
  error: vi.fn(),
  success: vi.fn(),
  warning: vi.fn(),
  confirm: vi.fn()
}))

vi.mock('../../api', () => apiMocks)
vi.mock('element-plus', () => ({
  ElMessage: {
    error: elMocks.error,
    success: elMocks.success,
    warning: elMocks.warning
  },
  ElMessageBox: { confirm: elMocks.confirm }
}))

import PluginAdminPanel from '../PluginAdminPanel.vue'
import type { PluginInfo } from '../../types'

type TableRow = Record<string, unknown>

const TableStub = defineComponent({
  props: {
    data: {
      type: Array as PropType<TableRow[]>,
      default: () => []
    }
  },
  setup(props, { slots }) {
    return () => {
      const columns = (slots.default?.() ?? []).map((child) =>
        isVNode(child) ? cloneVNode(child, { rows: props.data }) : child
      )
      return h('div', { class: 'table-stub' }, columns)
    }
  }
})

const TableColumnStub = defineComponent({
  props: {
    rows: {
      type: Array as PropType<TableRow[]>,
      default: () => []
    }
  },
  setup(props, { slots }) {
    return () =>
      h(
        'div',
        { class: 'table-column-stub' },
        props.rows.flatMap((row, index) => slots.default?.({ row, $index: index }) ?? [])
      )
  }
})

const DialogStub = defineComponent({
  props: {
    modelValue: Boolean,
    title: {
      type: String,
      default: ''
    }
  },
  setup(props, { slots }) {
    return () =>
      props.modelValue
        ? h('section', { 'data-dialog': props.title }, [
            h('h2', props.title),
            slots.default?.(),
            slots.footer?.()
          ])
        : null
  }
})

const PassThroughStub = defineComponent({
  setup(_, { slots }) {
    return () => h('div', slots.default?.())
  }
})

const ButtonStub = defineComponent({
  setup(_, { slots }) {
    return () => h('button', { type: 'button' }, slots.default?.())
  }
})

const TooltipStub = defineComponent({
  props: {
    effect: String,
    placement: String,
    popperClass: String,
    showAfter: Number
  },
  setup(props, { slots }) {
    return () =>
      h(
        'div',
        {
          'data-tooltip-effect': props.effect,
          'data-tooltip-placement': props.placement,
          'data-tooltip-popper-class': props.popperClass,
          'data-tooltip-show-after': props.showAfter
        },
        [
          slots.default?.(),
          h('div', { 'data-tooltip-content': '' }, slots.content?.())
        ]
      )
  }
})

const LabelStub = defineComponent({
  props: {
    title: String
  },
  setup(props, { slots }) {
    return () => h('span', props.title || slots.default?.())
  }
})

const plugin: PluginInfo = {
  name: 'ops-readonly',
  version: '1.0.0',
  display_name: '运维只读',
  description: '生产只读运维能力',
  skills: [],
  mcp_servers: [
    {
      id: 'server-a',
      transport: 'streamable_http',
      url: 'http://mcp.example.test',
      admission: { ok: true, errors: [], warnings: [] }
    }
  ],
  enabled: true,
  scope_type: 'selected',
  user_names: ['alice'],
  create_by_name: '张三',
  create_time: '2026-11-26 10:00:00'
}

const mountPanel = () =>
  mount(PluginAdminPanel, {
    global: {
      directives: {
        loading: () => undefined
      },
      stubs: {
        'el-alert': LabelStub,
        'el-button': ButtonStub,
        'el-dialog': DialogStub,
        'el-empty': LabelStub,
        'el-pagination': PassThroughStub,
        'el-radio-button': PassThroughStub,
        'el-radio-group': PassThroughStub,
        'el-select': PassThroughStub,
        'el-switch': PassThroughStub,
        'el-table': TableStub,
        'el-table-column': TableColumnStub,
        'el-tag': PassThroughStub,
        'el-tooltip': TooltipStub,
        'el-upload': PassThroughStub
      }
    }
  })

const buttonByText = (wrapper: ReturnType<typeof mountPanel>, text: string) => {
  const button = wrapper.findAll('button').find((item) => item.text().includes(text))
  if (!button) throw new Error(`按钮不存在: ${text}`)
  return button
}

describe('PluginAdminPanel 授权与 MCP 探测', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    apiMocks.getAdminPlugins.mockResolvedValue({
      status: 'success',
      data: { plugins: [plugin], total: 1 }
    })
    apiMocks.probeAdminPluginMcpServerTools.mockResolvedValue({
      status: 'success',
      data: {
        server_id: 'server-a',
        count: 1,
        tools: [
          {
            name: 'query_city',
            label: '查询城市',
            description: '按名称查询城市',
            approval: 'ask',
            parameters: [{ name: 'city', type: 'string', description: '城市名', required: true }]
          }
        ]
      }
    })
  })

  it('指定人员授权使用人员图标，MCP server 仅展示数量并在主弹窗内探测', async () => {
    const wrapper = mountPanel()
    await (wrapper.vm as unknown as { ensureLoaded: () => Promise<void> }).ensureLoaded()
    await flushPromises()

    const permissionButton = buttonByText(wrapper, '授权')
    expect(permissionButton.find('.fa-user-lock').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('alice')
    expect(wrapper.text()).toContain('1 个')
    expect(wrapper.text()).not.toContain('server-a')

    await buttonByText(wrapper, '1 个').trigger('click')
    await flushPromises()
    expect(wrapper.findAll('[data-dialog]')).toHaveLength(1)
    expect(wrapper.text()).toContain('server-a')

    await buttonByText(wrapper, '探测工具').trigger('click')
    await flushPromises()

    expect(apiMocks.probeAdminPluginMcpServerTools).toHaveBeenCalledWith('ops-readonly', 'server-a')
    expect(wrapper.findAll('[data-dialog]')).toHaveLength(1)
    expect(wrapper.text()).toContain('查询城市')
    const description = wrapper.find('[data-qa="mcp-tool-description"]')
    expect(description.text()).toBe('按名称查询城市')
    const descriptionTooltip = description.element.parentElement
    expect(descriptionTooltip?.getAttribute('data-tooltip-effect')).toBe('dark')
    expect(descriptionTooltip?.getAttribute('data-tooltip-popper-class')).toBe('mcp-tool-description-tooltip')
    expect(descriptionTooltip?.getAttribute('data-tooltip-show-after')).toBe('300')
    expect(wrapper.find('[data-qa="mcp-probe-breadcrumb"]').text()).toBe('运维只读 / server-a / 工具列表')

    await buttonByText(wrapper, '查看参数').trigger('click')
    await flushPromises()
    expect(wrapper.findAll('[data-dialog]')).toHaveLength(1)
    const parameters = wrapper.find('[data-qa="mcp-probe-parameters"]')
    expect(parameters.exists()).toBe(true)
    expect(parameters.text()).toContain('city')
    expect(parameters.text()).toContain('string')
    expect(parameters.text()).toContain('城市名')
    expect(parameters.text()).not.toContain('参数 JSON')
    expect(parameters.text()).not.toContain('查询城市')
    expect(parameters.text()).not.toContain('ask')
    expect(wrapper.find('[data-qa="mcp-probe-back"]').exists()).toBe(true)
    expect(wrapper.find('[data-qa="mcp-probe-breadcrumb"]').text()).toBe('运维只读 / server-a / 查询城市 / 参数')
  })
})
