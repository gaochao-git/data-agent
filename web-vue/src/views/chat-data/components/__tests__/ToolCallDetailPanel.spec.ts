import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ToolCallDetailPanel from '../conversation/ToolCallDetailPanel.vue'

describe('ToolCallDetailPanel', () => {
  it('recognizes the file early and streams formatted content while generating', async () => {
    const wrapper = mount(ToolCallDetailPanel, {
      props: {
        toolCall: {
          tool_call_id: 'call-streaming',
          tool_name: 'file_write',
          args: {},
          status: 'generating',
          argsStreaming: true,
          argsText: '{"file_path": "/mnt/user-data/outputs/report.html", "content": "<html>',
        },
      },
    })

    expect(wrapper.find('[data-file-write-preview]').exists()).toBe(true)
    expect(wrapper.text()).toContain('report.html')
    expect(wrapper.text()).toContain('HTML')
    expect(wrapper.text()).toContain('生成中')
    expect(wrapper.text()).toContain('<html>')

    await wrapper.find('[data-file-write-view-params]').trigger('click')

    expect(wrapper.find('[data-file-write-preview]').exists()).toBe(false)
    expect(wrapper.text()).toContain('"content": "<html>')
    expect(wrapper.text()).not.toContain('参数生成中')
    expect(wrapper.text()).not.toContain('{ }')
    expect(wrapper.text()).not.toContain('执行中...')
  })

  it('uses the shared display name and primary argument in the detail header', () => {
    const wrapper = mount(ToolCallDetailPanel, {
      props: {
        toolCall: {
          tool_call_id: 'call-search',
          tool_name: 'data_analyzing_tool',
          args: {
            action: 'execute_query',
            action_payload: { semantic_config_id: 'sc_a' },
          },
          status: 'completed',
          result: '{"success": true}',
        },
      },
    })

    expect(wrapper.text()).toContain('数据分析 · 查询数据')
    expect(wrapper.text()).toContain('sc_a')
    expect(wrapper.text()).not.toContain('data_analyzing_tool')
  })

  it('appends the Chinese action name for merged data-analyzing tools only', () => {
    const analyzing = mount(ToolCallDetailPanel, {
      props: {
        toolCall: {
          tool_call_id: 'call-check',
          tool_name: 'data_analyzing_tool',
          args: { action: 'check_yaml', action_payload: { file_path: '/mnt/user-data/workspace/m.yaml' } },
          status: 'completed',
          result: '{"success": true}',
        },
      },
    })
    expect(analyzing.text()).toContain('数据分析 · 语义模型自检')

    const plain = mount(ToolCallDetailPanel, {
      props: {
        toolCall: {
          tool_call_id: 'call-read',
          tool_name: 'file_read',
          args: { file_path: '/mnt/user-data/workspace/m.txt' },
          status: 'completed',
          result: 'ok',
        },
      },
    })
    expect(plain.text()).toContain('读取文件')
    expect(plain.text()).not.toContain(' · ')
  })

  it('pretty-prints the result when it is a JSON object/array string', () => {
    const wrapper = mount(ToolCallDetailPanel, {
      props: {
        toolCall: {
          tool_call_id: 'call-json',
          tool_name: 'sql_query',
          args: { sql: 'SELECT 1' },
          status: 'completed',
          result: '{"type":"query_result","status":"succeeded","total_count":1}',
        },
      },
    })
    expect(wrapper.findAll('pre')[1]?.text()).toContain('{\n  "type": "query_result",\n  "status": "succeeded",\n  "total_count": 1\n}')
  })

  it('keeps non-JSON and malformed JSON results as raw text', () => {
    const rawText = mount(ToolCallDetailPanel, {
      props: {
        toolCall: {
          tool_call_id: 'call-text',
          tool_name: 'file_read',
          args: { file_path: '/mnt/user-data/workspace/a.txt' },
          status: 'completed',
          result: '普通文本结果\n第二行',
        },
      },
    })
    expect(rawText.findAll('pre')[1]?.text()).toBe('普通文本结果\n第二行')

    const malformed = mount(ToolCallDetailPanel, {
      props: {
        toolCall: {
          tool_call_id: 'call-bad',
          tool_name: 'file_read',
          args: { file_path: '/mnt/user-data/workspace/b.txt' },
          status: 'completed',
          result: '{"broken": json',
        },
      },
    })
    expect(malformed.findAll('pre')[1]?.text()).toBe('{"broken": json')
  })

  it('shows formatted write content and switches back to the untouched raw params', async () => {
    const wrapper = mount(ToolCallDetailPanel, {
      props: {
        toolCall: {
          tool_call_id: 'call-write-json',
          tool_name: 'file_write',
          args: {
            file_path: '/mnt/user-data/outputs/result.json',
            content: '{"ok":true,"count":2}',
          },
          status: 'completed',
          result: '{"success": true}',
        },
      },
    })

    expect(wrapper.find('[data-file-write-preview]').exists()).toBe(true)
    expect(wrapper.text()).toContain('JSON')
    expect(wrapper.findAll('.cm-line').map(line => line.text()).join('\n')).toContain('"ok": true')

    await wrapper.find('[data-file-write-view-params]').trigger('click')

    expect(wrapper.find('[data-file-write-preview]').exists()).toBe(false)
    expect(wrapper.text()).toContain('"content": "{\\"ok\\":true,\\"count\\":2}"')
  })
})
