import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ToolCallDetailPanel from '../ToolCallDetailPanel.vue'

describe('ToolCallDetailPanel', () => {
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
})
