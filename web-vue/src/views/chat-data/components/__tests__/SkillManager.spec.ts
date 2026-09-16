import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

import SkillManager from '../SkillManager.vue'

// 组件 onMounted 会拉技能列表；其余 API 仅在对应交互里调用，给空实现即可。
vi.mock('../../api', () => ({
  copySkill: vi.fn(),
  createSkill: vi.fn(),
  deleteSkill: vi.fn(),
  deleteSkillVersionFile: vi.fn(),
  favoriteSkill: vi.fn(),
  getSkill: vi.fn(),
  getSkillVersion: vi.fn(),
  getSkillVersionFile: vi.fn(),
  getSkillVersionFiles: vi.fn().mockResolvedValue({ data: { references: [], assets: [], scripts: [] } }),
  getSkillVersions: vi.fn().mockResolvedValue({ data: { versions: [] } }),
  getSkills: vi.fn().mockResolvedValue({ data: { skills: [] } }),
  publishSkill: vi.fn(),
  rollbackSkillVersion: vi.fn(),
  saveSkillVersionFile: vi.fn(),
  unfavoriteSkill: vi.fn(),
  updateSkill: vi.fn(),
}))

const mountManager = () => mount(SkillManager, { props: { initialSkillId: null } })

describe('SkillManager 新建按钮（对话式创建）', () => {
  it('点击新建不再打开手写表单，而是发出 createWithAssistant 事件', async () => {
    const wrapper = mountManager()
    const createButton = wrapper.findAll('button').find(node => node.text().trim() === '新建')
    expect(createButton).toBeTruthy()
    await createButton!.trigger('click')

    expect(wrapper.emitted('createWithAssistant')).toHaveLength(1)
    // 手写新建面板保持关闭（表单仅保留给「编辑」场景）
    expect(wrapper.text()).not.toContain('新建用户技能')
  })
})
