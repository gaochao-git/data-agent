import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ToolCallCard from '../ToolCallCard.vue'

describe('ToolCallCard', () => {
  it('shows the normalized full path for compact file tools', () => {
    const wrapper = mount(ToolCallCard, {
      props: {
        compact: true,
        toolCall: {
          tool_call_id: 'call-1',
          tool_name: 'file_read',
          args: { file_path: '/mnt/user-data/workspace/reports/word.md' },
          status: 'completed',
        },
      },
    })

    expect(wrapper.text()).toContain('/mnt/user-data/workspace/reports/word.md')
    expect(wrapper.find('[title="/mnt/user-data/workspace/reports/word.md"]').exists()).toBe(true)
  })

  it('shows the search query after the mapped dataset-search tool name', () => {
    const wrapper = mount(ToolCallCard, {
      props: {
        compact: true,
        toolCall: {
          tool_call_id: 'call-search',
          tool_name: 'search_dataset',
          args: { query: '产品分类模型' },
          status: 'completed',
        },
      },
    })

    expect(wrapper.text()).toContain('检索语义模型')
    expect(wrapper.text()).toContain('产品分类模型')
  })

  it('shows the full file path after the write-file tool name', () => {
    const filePath = '/mnt/user-data/outputs/语义模型分析报告.md'
    const wrapper = mount(ToolCallCard, {
      props: {
        compact: true,
        toolCall: {
          tool_call_id: 'call-write',
          tool_name: 'file_write',
          args: { file_path: filePath, content: '# 报告' },
          status: 'completed',
        },
      },
    })

    expect(wrapper.text()).toContain('写入文件')
    expect(wrapper.text()).toContain(filePath)
    expect(wrapper.find(`[title="${filePath}"]`).exists()).toBe(true)
  })

  it('shows presented file paths after the artifact tool name', () => {
    const filePath = '/mnt/user-data/outputs/财付通数据分析报告.md'
    const wrapper = mount(ToolCallCard, {
      props: {
        compact: true,
        toolCall: {
          tool_call_id: 'call-present',
          tool_name: 'present_artifacts',
          args: { files: [filePath] },
          status: 'completed',
        },
      },
    })

    expect(wrapper.text()).toContain('展示文件')
    expect(wrapper.text()).toContain(filePath)
    expect(wrapper.find(`[title="${filePath}"]`).exists()).toBe(true)
  })

  it('shows the deleted file path after the delete-file tool name', () => {
    const filePath = '/mnt/user-data/workspace/临时文件.md'
    const wrapper = mount(ToolCallCard, {
      props: {
        compact: true,
        toolCall: {
          tool_call_id: 'call-delete',
          tool_name: 'file_delete',
          args: { file_path: filePath },
          status: 'completed',
        },
      },
    })

    expect(wrapper.text()).toContain('删除文件')
    expect(wrapper.text()).toContain(filePath)
    expect(wrapper.find(`[title="${filePath}"]`).exists()).toBe(true)
  })

  it('renders write_todos steps in the expanded area and hides the raw todos param', () => {
    const wrapper = mount(ToolCallCard, {
      props: {
        isExpanded: true,
        toolCall: {
          tool_call_id: 'call-todos',
          tool_name: 'write_todos',
          args: {
            todos: [
              { content: '读取数据', status: 'completed' },
              { content: '生成图表', status: 'in_progress' },
              { content: '输出报告', status: 'pending' },
            ],
          },
          status: 'completed',
        },
      },
    })

    expect(wrapper.text()).toContain('更新任务计划')
    expect(wrapper.text()).toContain('读取数据')
    expect(wrapper.text()).toContain('输出报告')
    expect(wrapper.find('.fa-circle-check.text-emerald-500').exists()).toBe(true)
    expect(wrapper.find('.fa-spinner.fa-spin').exists()).toBe(true)
    expect(wrapper.find('.border-dashed').exists()).toBe(true)
    // 原始 todos 参数不重复展示
    expect(wrapper.text()).not.toContain('todos')
  })

  it('does not render subagent chain inside the card (chain is rendered by ToolCallList)', () => {
    const wrapper = mount(ToolCallCard, {
      props: {
        isExpanded: true,
        toolCall: {
          tool_call_id: 'call-task',
          tool_name: 'task',
          args: { description: '任务书', subagent_type: 'deep-analysis' },
          status: 'running',
          subagentTrail: [
            {
              kind: 'tool',
              id: 'inner-1',
              tool_name: 'calculator_tool',
              args: { expression: '1 + 1' },
              status: 'completed',
            },
          ],
        },
      },
    })

    expect(wrapper.find('.subagent-chain').exists()).toBe(false)
    expect(wrapper.findAll('.sub-tool-chip').length).toBe(0)
  })
})
