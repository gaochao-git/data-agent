import { describe, expect, it } from 'vitest'
import { ref } from 'vue'
import { useSkillCommandMenu } from '../useSkillCommandMenu'
import type { SkillInfo } from '../../types'

const makeSkill = (name: string, title = name): SkillInfo => ({ name, title, description: `desc-${name}` })

const setup = () => useSkillCommandMenu({
  input: ref(''),
  enabled: ref(true),
})

describe('useSkillCommandMenu multi-select', () => {
  it('appends skills and dedupes by name', () => {
    const { selectedSkills, selectCommandSkill } = setup()
    selectCommandSkill(makeSkill('office', 'Office 文档处理'))
    selectCommandSkill(makeSkill('report', '报告生成'))
    selectCommandSkill(makeSkill('office', 'Office 文档处理'))

    expect(selectedSkills.value.map(skill => skill.name)).toEqual(['office', 'report'])
    expect(selectedSkills.value[0]).toEqual({ name: 'office', title: 'Office 文档处理', description: 'desc-office' })
  })

  it('strips the slash command prefix from input after selecting', () => {
    const input = ref('/off 分析结论')
    const { selectCommandSkill } = useSkillCommandMenu({ input, enabled: ref(true) })

    selectCommandSkill(makeSkill('office'))

    expect(input.value).toBe('分析结论')
  })

  it('removes a single skill by name and clears all', () => {
    const { selectedSkills, selectCommandSkill, removeSelectedSkill, clearSelectedSkills, selectedSkillTitles } = setup()
    selectCommandSkill(makeSkill('a'))
    selectCommandSkill(makeSkill('b', 'B 技能'))

    removeSelectedSkill('a')
    expect(selectedSkillTitles.value).toEqual(['B 技能'])

    clearSelectedSkills()
    expect(selectedSkills.value).toEqual([])
  })

  it('replaces the whole selection for pinned single-skill flows', () => {
    const { selectedSkills, selectCommandSkill, replaceSelectedSkill } = setup()
    selectCommandSkill(makeSkill('a'))
    selectCommandSkill(makeSkill('b'))

    replaceSelectedSkill(makeSkill('skill-creator'))

    expect(selectedSkills.value.map(skill => skill.name)).toEqual(['skill-creator'])
  })
})
