/**
 * 后台管理「技能总览」展示口径：来源/归属/可见性/状态/搜索筛选。
 * 面板与单测共用，避免页面里散落一套字段映射（与用户侧技能列表字段口径一致）。
 */
import type { SkillInfo } from '../types'

export type SkillSource = 'system' | 'user' | 'plugin'
export type SkillTagType = 'success' | 'info' | 'warning' | 'danger' | 'primary'

/** 管理端列表行：插件技能额外带所属插件名，供目录树按「插件 → 技能」分组。 */
export type AdminSkillInfo = SkillInfo & { plugin_name?: string }

export interface SkillSourceMeta {
  label: string
  tagType: SkillTagType
}

export const SKILL_SOURCE_META: Record<SkillSource, SkillSourceMeta> = {
  system: { label: '系统', tagType: 'primary' },
  user: { label: '用户', tagType: 'success' },
  plugin: { label: '插件', tagType: 'warning' }
}

export const skillSource = (item: SkillInfo): SkillSource => item.source || 'user'

export const skillSourceMeta = (item: SkillInfo): SkillSourceMeta => SKILL_SOURCE_META[skillSource(item)]

/** 归属：系统/插件技能没有个人归属，用户技能按创建人展示。 */
export const skillOwnerText = (item: SkillInfo): string => {
  const source = skillSource(item)
  if (source === 'system') return '系统内置'
  if (source === 'plugin') return '插件自带'
  return item.creator_name || item.create_by_name || item.owner_user_id || '-'
}

export const skillVisibilityText = (item: SkillInfo): string =>
  item.visibility_type === 'global' ? '全局' : '私有'

export interface SkillStatusView {
  label: string
  tagType: SkillTagType
}

/** 状态：停用优先；其次草稿/已发布，与后端 enabled_flag + publish_status 口径一致。 */
export const skillStatus = (item: SkillInfo): SkillStatusView => {
  if (item.enabled === false || item.publish_status === 'disabled') return { label: '已停用', tagType: 'info' }
  if (item.publish_status === 'draft') return { label: '草稿', tagType: 'warning' }
  return { label: '已发布', tagType: 'success' }
}

export const skillVersionText = (item: SkillInfo): string =>
  item.current_version_no ? `v${item.current_version_no}` : '-'

/** 总览搜索：名称/标题/描述/创建人一视同仁。 */
export const skillMatchesKeyword = (item: SkillInfo, keyword: string): boolean => {
  const word = keyword.trim().toLowerCase()
  if (!word) return true
  return [item.name, item.title, item.description, item.creator_name, item.create_by_name, item.owner_user_id]
    .filter(Boolean)
    .join(' ')
    .toLowerCase()
    .includes(word)
}

export interface AdminSkillFilter {
  keyword?: string
  source?: string
}

/** 来源筛选 + 关键词筛选；source 为 all/空表示不过滤。 */
export const filterAdminSkills = (skills: SkillInfo[], filter: AdminSkillFilter = {}): SkillInfo[] => {
  const source = filter.source && filter.source !== 'all' ? filter.source : ''
  return skills.filter((item) => {
    if (source && skillSource(item) !== source) return false
    return skillMatchesKeyword(item, filter.keyword || '')
  })
}

export const countSkillsBySource = (skills: SkillInfo[]): Record<string, number> => {
  const counts: Record<string, number> = { all: skills.length, system: 0, user: 0, plugin: 0 }
  for (const item of skills) {
    const key = skillSource(item)
    counts[key] = (counts[key] || 0) + 1
  }
  return counts
}
