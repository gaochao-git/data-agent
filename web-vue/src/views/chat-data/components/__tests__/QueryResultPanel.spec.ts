import { describe, expect, it } from 'vitest'
import { shallowMount } from '@vue/test-utils'
import QueryResultPanel from '../QueryResultPanel.vue'
import ResultTabs from '../ResultTabs.vue'

describe('QueryResultPanel', () => {
  it('keeps identical SQL errors as separate member result tabs', () => {
    const wrapper = shallowMount(QueryResultPanel, {
      props: {
        resultData: JSON.stringify({
          status: 'failed',
          sql: 'select * from orders limit1 100',
          results: [
            {
              member_datasource_id: 'ORG_A',
              member_name: '机构A',
              status: 'failed',
              sql: 'select * from orders limit1 100',
              error: 'SQL 语法错误',
              columns: [],
              data: [],
              total_count: 0,
            },
            {
              member_datasource_id: 'ORG_B',
              member_name: '机构B',
              status: 'failed',
              sql: 'select * from orders limit1 100',
              error: 'SQL 语法错误',
              columns: [],
              data: [],
              total_count: 0,
            },
          ],
        }),
      },
    })

    const tabs = wrapper.getComponent(ResultTabs).props('tabs') as Array<{ label: string; status: string }>

    expect(tabs).toEqual([
      expect.objectContaining({ label: '机构A', status: 'error' }),
      expect.objectContaining({ label: '机构B', status: 'error' }),
    ])
  })
})
