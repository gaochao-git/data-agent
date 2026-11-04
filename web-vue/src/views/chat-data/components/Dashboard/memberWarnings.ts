import type { MemberQueryResult } from '../../types'

/** 汇总部分成员失败：顶层 payload 仍可能 succeeded，错误只挂在 results[] 成员上。 */
export const buildMemberWarningLines = (results?: MemberQueryResult[] | null): string[] => (
  (results || [])
    .filter(item => item.status === 'failed' || item.status === 'cancelled')
    .map(item => {
      const name = item.member_name || item.member_datasource_id || '成员'
      const reason = String(item.error || (item.status === 'cancelled' ? '已取消' : '执行失败'))
      return `${name}：${reason}`
    })
)
