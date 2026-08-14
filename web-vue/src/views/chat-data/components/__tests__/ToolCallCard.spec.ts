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
})
