import type { SkillInfo } from '../types'

export const getSkillCreatorLabel = (skill: SkillInfo, fallback = '用户技能') => {
  if (skill.source === 'system') return '系统'
  return skill.creator_name || skill.create_by_name || skill.creator_id || skill.owner_user_id || fallback
}

export const getSkillCreatorShortLabel = (skill: SkillInfo, fallback = '用户技能') => {
  const label = getSkillCreatorLabel(skill, fallback)
  return label.length > 3 ? `${label.slice(0, 3)}...` : label
}
