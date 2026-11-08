import { describe, expect, it } from 'vitest'
import type { SkillInfo } from '../../types'
import {
  countSkillsBySource,
  filterAdminSkills,
  skillMatchesKeyword,
  skillOwnerText,
  skillSourceMeta,
  skillStatus,
  skillVersionText,
  skillVisibilityText
} from '../skillAdmin'

const userSkill: SkillInfo = {
  skill_id: 's1',
  name: 'weekly-report',
  title: '周报生成',
  description: '按周汇总经营数据',
  source: 'user',
  enabled: true,
  publish_status: 'published',
  visibility_type: 'private',
  current_version_no: 3,
  creator_name: '张三'
}

const disabledSkill: SkillInfo = {
  skill_id: 's2',
  name: 'legacy-report',
  title: '旧版报表',
  source: 'user',
  enabled: false,
  publish_status: 'published'
}

const systemSkill: SkillInfo = { name: 'office', title: 'Office 技能', source: 'system' }
const pluginSkill: SkillInfo = { name: 'fault-analysis', title: '故障分析', source: 'plugin' }

describe('后台技能总览展示口径', () => {
  it('来源标签：系统/用户/插件各不相同', () => {
    expect(skillSourceMeta(systemSkill)).toEqual({ label: '系统', tagType: 'primary' })
    expect(skillSourceMeta(userSkill)).toEqual({ label: '用户', tagType: 'success' })
    expect(skillSourceMeta(pluginSkill)).toEqual({ label: '插件', tagType: 'warning' })
  })

  it('归属：系统内置/插件自带/用户按创建人', () => {
    expect(skillOwnerText(systemSkill)).toBe('系统内置')
    expect(skillOwnerText(pluginSkill)).toBe('插件自带')
    expect(skillOwnerText(userSkill)).toBe('张三')
    expect(skillOwnerText({ name: 'x', source: 'user' })).toBe('-')
  })

  it('可见性/版本/状态口径', () => {
    expect(skillVisibilityText(userSkill)).toBe('私有')
    expect(skillVisibilityText({ name: 'x', visibility_type: 'global' })).toBe('全局')
    expect(skillVersionText(userSkill)).toBe('v3')
    expect(skillVersionText({ name: 'x' })).toBe('-')
    expect(skillStatus(userSkill)).toEqual({ label: '已发布', tagType: 'success' })
    expect(skillStatus({ name: 'x', publish_status: 'draft' })).toEqual({ label: '草稿', tagType: 'warning' })
    expect(skillStatus(disabledSkill)).toEqual({ label: '已停用', tagType: 'info' })
  })

  it('搜索命中名称/标题/描述/创建人，空关键词全命中', () => {
    expect(skillMatchesKeyword(userSkill, '周报')).toBe(true)
    expect(skillMatchesKeyword(userSkill, '经营数据')).toBe(true)
    expect(skillMatchesKeyword(userSkill, '张三')).toBe(true)
    expect(skillMatchesKeyword(userSkill, '不存在')).toBe(false)
    expect(skillMatchesKeyword(userSkill, '  ')).toBe(true)
  })

  it('来源筛选与关键词筛选可叠加', () => {
    const skills = [userSkill, disabledSkill, systemSkill, pluginSkill]
    expect(filterAdminSkills(skills, { source: 'user' }).map(item => item.name)).toEqual([
      'weekly-report',
      'legacy-report'
    ])
    expect(filterAdminSkills(skills, { source: 'all', keyword: '故障' }).map(item => item.name)).toEqual([
      'fault-analysis'
    ])
    expect(filterAdminSkills(skills, { source: 'plugin', keyword: '周报' })).toEqual([])
    expect(countSkillsBySource(skills)).toEqual({ all: 4, system: 1, user: 2, plugin: 1 })
  })
})
