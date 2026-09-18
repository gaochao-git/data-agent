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

  it('shows the compact description for the merged data-analyzing tool', () => {
    const wrapper = mount(ToolCallCard, {
      props: {
        compact: true,
        toolCall: {
          tool_call_id: 'call-search',
          tool_name: 'data_analyzing_tool',
          args: { action: 'validate_dsl', action_payload: { semantic_config_id: 'sc_a' } },
          status: 'completed',
        },
      },
    })

    expect(wrapper.text()).toContain('数据分析')
    expect(wrapper.text()).toContain('sc_a')
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
          args: { description: '任务书', subagent_type: 'data-explore' },
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

  it('keeps file_diff raw json as result and offers a separate diff viewer button', () => {
    const result = '[file_diff]\n' + JSON.stringify({
      file_a: '/mnt/user-data/workspace/semantic/orders__a1.yaml',
      file_b: '/mnt/user-data/outputs/orders__a1.yaml',
      status: 'changed',
      added_lines: 2,
      removed_lines: 1,
      diff: '--- /mnt/user-data/workspace/semantic/orders__a1.yaml\n+++ /mnt/user-data/outputs/orders__a1.yaml\n@@ -1,3 +1,4 @@\n version: \'1\'\n-name: orders\n+name: orders_v2\n+metrics: []\n',
    })
    const wrapper = mount(ToolCallCard, {
      props: {
        isExpanded: true,
        toolCall: {
          tool_call_id: 'call-diff',
          tool_name: 'file_diff',
          args: { file_a: '/mnt/user-data/workspace/semantic/orders__a1.yaml', file_b: '/mnt/user-data/outputs/orders__a1.yaml' },
          status: 'completed',
          result,
        },
      },
    })

    // 结果区保持与其他工具一致的原始 JSON 展示
    expect(wrapper.text()).toContain('added_lines')
    // 卡片标题栏显示 +N -M 统计，并提供独立「查看 Diff」入口
    expect(wrapper.text()).toContain('+2')
    expect(wrapper.text()).toContain('-1')
    const diffButton = wrapper.findAll('button').find(b => b.text().includes('查看 Diff'))
    expect(diffButton).toBeTruthy()
  })

  it('offers diff viewer for identical file_diff without line stats', () => {
    const result = '[file_diff]\n' + JSON.stringify({
      file_a: '/mnt/user-data/workspace/a.yaml',
      file_b: '/mnt/user-data/workspace/b.yaml',
      status: 'identical',
      added_lines: 0,
      removed_lines: 0,
    })
    const wrapper = mount(ToolCallCard, {
      props: {
        isExpanded: true,
        toolCall: {
          tool_call_id: 'call-diff-same',
          tool_name: 'file_diff',
          args: { file_a: '/mnt/user-data/workspace/a.yaml', file_b: '/mnt/user-data/workspace/b.yaml' },
          status: 'completed',
          result,
        },
      },
    })

    const diffButton = wrapper.findAll('button').find(b => b.text().includes('查看 Diff'))
    expect(diffButton).toBeTruthy()
    // identical 不显示 +N -M 统计
    expect(wrapper.text()).not.toContain('+0')
  })
})
