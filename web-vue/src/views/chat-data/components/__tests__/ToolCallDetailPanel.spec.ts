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

    expect(wrapper.text()).toContain('数据分析')
    expect(wrapper.text()).toContain('sc_a')
    expect(wrapper.text()).not.toContain('data_analyzing_tool')
  })
})
