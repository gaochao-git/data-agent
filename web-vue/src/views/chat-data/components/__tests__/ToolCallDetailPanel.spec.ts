import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ToolCallDetailPanel from '../ToolCallDetailPanel.vue'

describe('ToolCallDetailPanel', () => {
  it('uses the shared display name and primary argument in the detail header', () => {
    const wrapper = mount(ToolCallDetailPanel, {
      props: {
        toolCall: {
          tool_call_id: 'call-search',
          tool_name: 'search_dataset',
          args: { query: '产品分类模型' },
          status: 'completed',
          result: '{"success": true}',
        },
      },
    })

    expect(wrapper.text()).toContain('检索语义模型')
    expect(wrapper.text()).toContain('产品分类模型')
    expect(wrapper.text()).not.toContain('search_dataset')
  })
})
