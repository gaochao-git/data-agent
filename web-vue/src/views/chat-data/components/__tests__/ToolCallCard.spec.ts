import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ToolCallCard from '../ToolCallCard.vue'

describe('ToolCallCard', () => {
  it('renders the raw argument preview while args are streaming', () => {
    const wrapper = mount(ToolCallCard, {
      props: {
        isExpanded: true,
        toolCall: {
          tool_call_id: 'call-streaming',
          tool_name: 'file_read',
          args: {},
          status: 'generating',
          argsStreaming: true,
          argsText: '{"file_path": "/mnt/user-data/workspace/report.md"',
        },
      },
    })

    expect(wrapper.text()).toContain('{"file_path": "/mnt/user-data/workspace/report.md"')
    expect(wrapper.text()).not.toContain('生成参数中')
    expect(wrapper.text()).not.toContain('执行中...')
  })

  it('keeps non-subagent streaming args out of compact chat rows', () => {
    const wrapper = mount(ToolCallCard, {
      props: {
        compact: true,
        toolCall: {
          tool_call_id: 'call-compact-streaming',
          tool_name: 'sql_query',
          args: {},
          status: 'generating',
          argsStreaming: true,
          argsText: '{"sql": "select count(*) from orders',
        },
      },
    })

    expect(wrapper.text()).not.toContain('select count(*) from orders')
    expect(wrapper.find('.tool-args-preview').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('生成参数中')
  })

  it('shows only the subagent description markdown in compact chat rows', () => {
    const wrapper = mount(ToolCallCard, {
      props: {
        compact: true,
        toolCall: {
          tool_call_id: 'call-subagent-streaming',
          tool_name: 'subagent',
          args: {},
          status: 'generating',
          argsStreaming: true,
          argsText: '{"dispatch":{"description":"## 任务\\n先看工作区","subagent_type":"general"}}',
        },
      },
    })

    expect(wrapper.text()).toContain('任务')
    expect(wrapper.text()).toContain('先看工作区')
    expect(wrapper.find('h2').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('subagent_type')
  })

  it('shows only the task description in the compact subagent row', () => {
    const wrapper = mount(ToolCallCard, {
      props: {
        compact: true,
        toolCall: {
          tool_call_id: 'call-subagent-done',
          tool_name: 'subagent',
          args: { dispatch: { description: '盘点语义模型', subagent_type: 'general' } },
          status: 'running',
        },
      },
    })

    expect(wrapper.text()).toContain('盘点语义模型')
    expect(wrapper.text()).not.toContain('general')
    expect(wrapper.text()).not.toContain('subagent_type')
  })

  it('shows the follow-up message rather than only the intent in the compact subagent row', () => {
    const wrapper = mount(ToolCallCard, {
      props: {
        compact: true,
        toolCall: {
          tool_call_id: 'call-subagent-send',
          tool_name: 'subagent',
          args: {
            action: 'send',
            task_id: 'task-1',
            message: '请补齐渠道拆分并核对第三个页面',
            intent: '续写失联的子任务',
          },
          status: 'completed',
        },
      },
    })

    expect(wrapper.text()).toContain('向子智能体发送消息')
    expect(wrapper.text()).toContain('请补齐渠道拆分并核对第三个页面')
    expect(wrapper.text()).not.toContain('续写失联的子任务')
    expect(wrapper.text()).not.toContain('task-1')
  })

  it('shows file edit line deltas in compact mode', () => {
    const wrapper = mount(ToolCallCard, {
      props: {
        compact: true,
        toolCall: {
          tool_call_id: 'call-edit',
          tool_name: 'file_edit',
          args: { file_path: '/mnt/user-data/workspace/a.html' },
          status: 'completed',
          contentLines: 12,
          removedLines: 3,
        },
      },
    })

    expect(wrapper.text()).toContain('+12')
    expect(wrapper.text()).toContain('-3')
  })

  it('streams the follow-up message body in the compact subagent row', () => {
    const wrapper = mount(ToolCallCard, {
      props: {
        compact: true,
        toolCall: {
          tool_call_id: 'call-subagent-send-streaming',
          tool_name: 'subagent',
          args: {},
          status: 'generating',
          argsStreaming: true,
          argsText: '{"action":"send","task_id":"task-1","message":"继续补充渠道拆分',
        },
      },
    })

    expect(wrapper.text()).toContain('继续补充渠道拆分')
    expect(wrapper.text()).not.toContain('task_id')
  })

  it('keeps long non-subagent args out of compact mode', () => {
    const wrapper = mount(ToolCallCard, {
      props: {
        compact: true,
        toolCall: {
          tool_call_id: 'call-compact-long',
          tool_name: 'file_write',
          args: {},
          status: 'generating',
          argsStreaming: true,
          argsText: `${'x'.repeat(2_000)}LATEST`,
        },
      },
    })

    expect(wrapper.text()).not.toContain('LATEST')
    expect(wrapper.find('.tool-args-preview').exists()).toBe(false)
  })

  it('falls back to structured params after args streaming ends', () => {
    const wrapper = mount(ToolCallCard, {
      props: {
        isExpanded: true,
        toolCall: {
          tool_call_id: 'call-final',
          tool_name: 'file_read',
          args: { file_path: '/mnt/user-data/workspace/report.md' },
          status: 'running',
          argsStreaming: false,
          argsText: '{"file_path": "/mnt/user-data/workspace/report.md"}',
        },
      },
    })

    expect(wrapper.text()).toContain('执行中...')
    expect(wrapper.text()).not.toContain('生成参数中')
    expect(wrapper.text()).toContain('/mnt/user-data/workspace/report.md')
  })

  it('renders a stopped tool neutrally instead of an error', () => {
    const wrapper = mount(ToolCallCard, {
      props: {
        isExpanded: true,
        toolCall: {
          tool_call_id: 'call-stopped',
          tool_name: 'sql_query',
          args: { sql: 'select 1' },
          status: 'cancelled',
          result: '任务已停止，工具未执行完成。',
        },
      },
    })

    expect(wrapper.text()).toContain('任务已停止，工具未执行完成。')
    expect(wrapper.text()).not.toContain('错误')
    expect(wrapper.text()).not.toContain('执行中...')
  })

  it('marks a stopped tool in compact chat rows', () => {
    const wrapper = mount(ToolCallCard, {
      props: {
        compact: true,
        toolCall: {
          tool_call_id: 'call-compact-stopped',
          tool_name: 'sql_query',
          args: { sql: 'select 1' },
          status: 'cancelled',
          result: '任务已停止，工具未执行完成。',
        },
      },
    })

    expect(wrapper.text()).toContain('已停止')
    expect(wrapper.text()).not.toContain('错误')
    expect(wrapper.find('.fa-spinner').exists()).toBe(false)
  })

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

  it('shows task management actions without the subagent dispatch label', () => {
    const wrapper = mount(ToolCallCard, {
      props: {
        compact: true,
        toolCall: {
          tool_call_id: 'call-task-check',
          tool_name: 'subagent',
          args: { manage: { action: 'check', task_id: 'task-1' } },
          status: 'completed',
        },
      },
    })

    expect(wrapper.text()).toContain('查看子智能体任务')
    expect(wrapper.text()).toContain('task-1')
    expect(wrapper.text()).not.toContain('派发子智能体')
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
          tool_name: 'subagent',
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

  it('renders nested dispatch args with the subagent icon', () => {
    const wrapper = mount(ToolCallCard, {
      props: {
        isExpanded: true,
        toolCall: {
          tool_call_id: 'call-dispatch',
          tool_name: 'subagent',
          args: { dispatch: { description: '历史任务书', subagent_type: 'explore' } },
          status: 'running',
        },
      },
    })

    expect(wrapper.text()).toContain('派发子智能体')
    expect(wrapper.text()).toContain('历史任务书')
    expect(wrapper.find('.fa-robot').exists()).toBe(true)
  })

  it('keeps legacy task history on the subagent card path', () => {
    const wrapper = mount(ToolCallCard, {
      props: {
        isExpanded: true,
        toolCall: {
          tool_call_id: 'call-legacy-task',
          tool_name: 'task',
          args: { description: '旧任务书', subagent_type: 'explore' },
          status: 'running',
        },
      },
    })

    expect(wrapper.text()).toContain('派发子智能体')
    expect(wrapper.text()).toContain('旧任务书')
    expect(wrapper.find('.fa-robot').exists()).toBe(true)
  })

  it('shows streaming intent while args are not yet parsed', () => {
    const wrapper = mount(ToolCallCard, {
      props: {
        isExpanded: false,
        toolCall: {
          tool_call_id: 'call-officecli',
          tool_name: 'officecli_run',
          args: {},
          streamingIntent: '生成转存文本用于对比',
          status: 'running',
        },
      },
    })

    expect(wrapper.text()).toContain('生成转存文本用于对比')
  })

  it('prefers parsed args.intent over streamingIntent after completion', () => {
    const wrapper = mount(ToolCallCard, {
      props: {
        isExpanded: false,
        toolCall: {
          tool_call_id: 'call-officecli',
          tool_name: 'officecli_run',
          args: { intent: '导出修改后单元格清单' },
          streamingIntent: '生成转存文本用于对比',
          status: 'completed',
        },
      },
    })

    expect(wrapper.text()).toContain('导出修改后单元格清单')
    expect(wrapper.text()).not.toContain('生成转存文本用于对比')
  })

  it('keeps file_diff raw json as result and offers a separate diff viewer button', () => {
    const result = '[file_diff]\n' + JSON.stringify({
      file_a: '/mnt/user-data/workspace/.system/semantic/orders__a1.yaml',
      file_b: '/mnt/user-data/outputs/orders__a1.yaml',
      status: 'changed',
      added_lines: 2,
      removed_lines: 1,
      diff: '--- /mnt/user-data/workspace/.system/semantic/orders__a1.yaml\n+++ /mnt/user-data/outputs/orders__a1.yaml\n@@ -1,3 +1,4 @@\n version: \'1\'\n-name: orders\n+name: orders_v2\n+metrics: []\n',
    })
    const wrapper = mount(ToolCallCard, {
      props: {
        isExpanded: true,
        toolCall: {
          tool_call_id: 'call-diff',
          tool_name: 'file_diff',
          args: { file_a: '/mnt/user-data/workspace/.system/semantic/orders__a1.yaml', file_b: '/mnt/user-data/outputs/orders__a1.yaml' },
          status: 'completed',
          result,
        },
      },
    })

    // 结果区保持与其他工具一致的原始 JSON 展示
    expect(wrapper.text()).toContain('added_lines')
    // 时间线卡片不显示 +N -M 统计（只在弹层内展示），提供独立「查看 Diff」入口
    const text = wrapper.text()
    const beforeViewer = text.slice(0, text.indexOf('查看 Diff'))
    expect(beforeViewer).not.toContain('+2')
    expect(beforeViewer).not.toContain('-1')
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

  it('renders write content by file type and keeps the raw params view', async () => {
    const wrapper = mount(ToolCallCard, {
      props: {
        isExpanded: true,
        toolCall: {
          tool_call_id: 'call-write-markdown',
          tool_name: 'file_write',
          args: {
            file_path: '/mnt/user-data/outputs/report.md',
            content: '# 月度报告\n\n正文内容',
          },
          status: 'completed',
        },
      },
    })

    expect(wrapper.find('[data-file-write-preview]').exists()).toBe(true)
    expect(wrapper.find('h1').text()).toContain('月度报告')
    expect(wrapper.text()).toContain('Markdown')

    await wrapper.find('[data-file-write-view-params]').trigger('click')

    expect(wrapper.find('[data-file-write-preview]').exists()).toBe(false)
    expect(wrapper.text()).toContain('文件路径')
    expect(wrapper.text()).toContain('/mnt/user-data/outputs/report.md')
    expect(wrapper.text()).toContain('正文内容')
  })

  it('renders code writes with line numbers instead of a raw JSON string', async () => {
    const wrapper = mount(ToolCallCard, {
      props: {
        isExpanded: true,
        toolCall: {
          tool_call_id: 'call-write-python',
          tool_name: 'file_write',
          args: {
            file_path: '/mnt/user-data/outputs/analyze.py',
            content: 'import pandas as pd\n\nprint(pd)',
          },
          status: 'completed',
        },
      },
    })
    await wrapper.vm.$nextTick()

    expect(wrapper.text()).toContain('Python')
    expect(wrapper.find('.cm-editor').exists()).toBe(true)
    expect(wrapper.findAll('.cm-line').length).toBeGreaterThan(0)
  })

  it('recognizes the file name early and streams formatted content before args finish', async () => {
    const wrapper = mount(ToolCallCard, {
      props: {
        isExpanded: true,
        toolCall: {
          tool_call_id: 'call-write-streaming-preview',
          tool_name: 'file_write',
          args: {},
          status: 'generating',
          argsStreaming: true,
          argsText: '{"file_path":"/mnt/user-data/outputs/notes.md","content":"# 标题\\n正文还在生成',
        },
      },
    })

    expect(wrapper.find('[data-file-write-preview]').exists()).toBe(true)
    expect(wrapper.text()).toContain('notes.md')
    expect(wrapper.text()).toContain('Markdown')
    expect(wrapper.text()).toContain('生成中')
    expect(wrapper.text()).toContain('标题')
    expect(wrapper.text()).toContain('正文还在生成')

    await wrapper.find('[data-file-write-view-params]').trigger('click')

    expect(wrapper.find('[data-file-write-preview]').exists()).toBe(false)
    expect(wrapper.text()).toContain('"file_path":"/mnt/user-data/outputs/notes.md"')
  })

  it('shows the target file path in compact rows before args finish', () => {
    const wrapper = mount(ToolCallCard, {
      props: {
        compact: true,
        toolCall: {
          tool_call_id: 'call-write-streaming-path',
          tool_name: 'file_write',
          args: {},
          status: 'generating',
          argsStreaming: true,
          argsText: '{"file_path":"/mnt/user-data/workspace/draft.txt","content":"第一行',
        },
      },
    })

    expect(wrapper.text()).toContain('写入文件')
    expect(wrapper.text()).toContain('draft.txt')
  })

  it('still streams readable content when content arrives before file_path', () => {
    const wrapper = mount(ToolCallCard, {
      props: {
        isExpanded: true,
        toolCall: {
          tool_call_id: 'call-write-streaming-content-first',
          tool_name: 'file_write',
          args: {},
          status: 'generating',
          argsStreaming: true,
          argsText: '{"content":"先写正文，文件路径还没出现',
        },
      },
    })

    expect(wrapper.find('[data-file-write-preview]').exists()).toBe(true)
    expect(wrapper.text()).toContain('先写正文，文件路径还没出现')
    expect(wrapper.text()).toContain('文件路径生成中')
    expect(wrapper.text()).not.toContain('"content":')
  })
})
