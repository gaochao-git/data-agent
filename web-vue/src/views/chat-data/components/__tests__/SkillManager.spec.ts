import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { defineComponent, h, type PropType, type VNode } from 'vue'

import SkillManager from '../SkillManager.vue'

// ---- API mock：必须覆盖组件 import 的全部函数（缺 key 会是 undefined，调用即报错）----
// 注意：技能固定目录只有 references 与 assets，scripts 已整体移除，mock 数据勿再回退。
const apiMocks = vi.hoisted(() => ({
  copySkill: vi.fn(),
  createSkill: vi.fn(),
  deleteSkill: vi.fn(),
  deleteSkillVersionFile: vi.fn(),
  favoriteSkill: vi.fn(),
  getSkill: vi.fn(),
  getSkillVersion: vi.fn(),
  getSkillVersionFile: vi.fn(),
  getSkillVersionFiles: vi.fn().mockResolvedValue({ data: { references: [], assets: [] } }),
  getSkillVersionOfficeHtmlPreview: vi.fn(),
  getSkillVersionRawFile: vi.fn(),
  getSkillVersions: vi.fn().mockResolvedValue({ data: { versions: [] } }),
  getSkills: vi.fn().mockResolvedValue({ data: { skills: [] } }),
  publishSkill: vi.fn(),
  rollbackSkillVersion: vi.fn(),
  saveSkillVersionFile: vi.fn(),
  unfavoriteSkill: vi.fn(),
  updateSkill: vi.fn(),
  uploadSkillVersionFile: vi.fn(),
}))

vi.mock('../../api', () => apiMocks)

const messageMocks = vi.hoisted(() => ({
  error: vi.fn(),
  success: vi.fn(),
  warning: vi.fn(),
}))

vi.mock('element-plus', () => ({
  ElMessage: messageMocks,
  ElMessageBox: { confirm: vi.fn() },
}))

// jsdom 没有 URL.createObjectURL/revokeObjectURL（blob 预览链路依赖）
const createObjectURLMock = vi.fn(() => 'blob:mock-url')
const revokeObjectURLMock = vi.fn()

// el-tree 桩：递归渲染节点并回填默认作用域插槽，让模板里的文件按钮可交互
type StubTreeNode = { key: string; label: string; children?: StubTreeNode[] }
const ElTreeStubSimple = defineComponent({
  name: 'ElTree',
  props: { data: { type: Array as PropType<StubTreeNode[]>, default: () => [] } },
  setup(props, { slots }) {
    const renderNodes = (nodes: StubTreeNode[]): VNode[] =>
      nodes.flatMap(node => [
        h('div', { class: 'el-tree-node-stub', 'data-key': node.key }, slots.default?.({ node: { label: node.label }, data: node })),
        ...(node.children?.length ? renderNodes(node.children) : []),
      ])
    return () => h('div', { class: 'el-tree-stub' }, renderNodes(props.data))
  },
})

const baseSkill = {
  skill_id: 'skill-1',
  name: 'demo-skill',
  title: '演示技能',
  source: 'user',
  can_manage: true,
  draft_version_no: 2,
  current_version_no: 1,
}

const mountManager = async () => {
  const wrapper = mount(SkillManager, {
    props: { initialSkillId: 'skill-1' },
    global: { components: { ElTree: ElTreeStubSimple } },
  })
  await flushPromises()
  return wrapper
}

const clickTreeFile = async (wrapper: Awaited<ReturnType<typeof mountManager>>, label: string) => {
  const node = wrapper.findAll('.el-tree-node-stub button').find(btn => btn.text().trim() === label)
  expect(node, `文件树中应存在 ${label} 节点`).toBeTruthy()
  await node!.trigger('click')
  await flushPromises()
}

describe('SkillManager 新建按钮（对话式创建）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    Object.defineProperty(URL, 'createObjectURL', { value: createObjectURLMock, configurable: true, writable: true })
    Object.defineProperty(URL, 'revokeObjectURL', { value: revokeObjectURLMock, configurable: true, writable: true })
    apiMocks.getSkills.mockResolvedValue({ data: { skills: [baseSkill] } })
    apiMocks.getSkill.mockResolvedValue({ data: { ...baseSkill } })
    apiMocks.getSkillVersions.mockResolvedValue({ data: { versions: [{ skill_version_no: 2, version_publish_status: 'draft' }] } })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('点击新建不再打开手写表单，而是发出 createWithAssistant 事件', async () => {
    const wrapper = await mountManager()
    const createButton = wrapper.findAll('button').find(node => node.text().trim() === '新建')
    expect(createButton).toBeTruthy()
    await createButton!.trigger('click')

    expect(wrapper.emitted('createWithAssistant')).toHaveLength(1)
    // 手写新建面板保持关闭（表单仅保留给「编辑」场景）
    expect(wrapper.text()).not.toContain('新建用户技能')
  })

  it('点击图片文件走 raw 端点并以 blob img 预览', async () => {
    apiMocks.getSkillVersionFiles.mockResolvedValue({ data: { references: ['guide.md'], assets: ['logo.png'] } })
    apiMocks.getSkillVersionRawFile.mockResolvedValue(new Blob([new Uint8Array([137, 80, 78, 71])], { type: 'image/png' }))

    const wrapper = await mountManager()
    await clickTreeFile(wrapper, 'logo.png')

    expect(apiMocks.getSkillVersionRawFile).toHaveBeenCalledWith('skill-1', 2, { dir_name: 'assets', file_path: 'logo.png' })
    expect(apiMocks.getSkillVersionFile).not.toHaveBeenCalled() // 二进制不走文本读取链路
    const img = wrapper.find('img[src="blob:mock-url"]')
    expect(img.exists()).toBe(true)
    expect(createObjectURLMock).toHaveBeenCalledTimes(1)
  })

  it('raw 端点返回 JSON 错误体时不渲染破图，提示后端 msg', async () => {
    apiMocks.getSkillVersionFiles.mockResolvedValue({ data: { references: [], assets: ['logo.png'] } })
    // jsdom 的 Blob 无 text()（浏览器环境有），这里用结构等价的 fake 验证组件分支逻辑
    const jsonErrorBlob = {
      type: 'application/json',
      text: async () => JSON.stringify({ code: 1, msg: '文件过大' }),
    } as unknown as Blob
    apiMocks.getSkillVersionRawFile.mockResolvedValue(jsonErrorBlob)

    const wrapper = await mountManager()
    await clickTreeFile(wrapper, 'logo.png')

    expect(messageMocks.error).toHaveBeenCalledWith('文件过大')
    expect(wrapper.find('img[src^="blob:"]').exists()).toBe(false)
  })

  it('点击 Office 文件走 office-preview 端点并以 iframe 渲染服务端 HTML', async () => {
    apiMocks.getSkillVersionFiles.mockResolvedValue({ data: { references: ['report.docx'], assets: [] } })
    apiMocks.getSkillVersionOfficeHtmlPreview.mockResolvedValue({
      data: { available: true, renderer: 'officecli-html', html: '<div class="docx-preview">表格内容</div>', truncated: false },
    })

    const wrapper = await mountManager()
    await clickTreeFile(wrapper, 'report.docx')

    expect(apiMocks.getSkillVersionOfficeHtmlPreview).toHaveBeenCalledWith('skill-1', 2, { dir_name: 'references', file_path: 'report.docx' })
    const iframe = wrapper.find('iframe')
    expect(iframe.exists()).toBe(true)
    expect(iframe.attributes('srcdoc')).toContain('表格内容')
  })

  it('上传文件调用 uploadSkillVersionFile 并刷新技能与文件列表', async () => {
    apiMocks.getSkillVersionFiles.mockResolvedValue({ data: { references: [], assets: [] } })
    apiMocks.uploadSkillVersionFile.mockResolvedValue({ data: { uploaded: true, dir_name: 'references' } })

    const wrapper = await mountManager()
    // 点击 references 目录的「上传」按钮，再从隐藏 input 触发 change
    const uploadButton = wrapper.findAll('.el-tree-node-stub button').find(btn => btn.text().trim() === '上传')
    expect(uploadButton).toBeTruthy()
    await uploadButton!.trigger('click')

    const fileInput = wrapper.find('input[type="file"]')
    expect(fileInput.exists()).toBe(true)
    const file = new File([new Uint8Array([1, 2, 3])], 'notes.txt', { type: 'text/plain' })
    Object.defineProperty(fileInput.element, 'files', { value: [file] })
    await fileInput.trigger('change')
    await flushPromises()

    expect(apiMocks.uploadSkillVersionFile).toHaveBeenCalledWith('skill-1', 2, { dir_name: 'references', file })
    expect(messageMocks.success).toHaveBeenCalledWith('已上传 notes.txt')
    // 上传后刷新链路：重拉详情 + 文件列表
    expect(apiMocks.getSkill).toHaveBeenCalledTimes(2)
    expect(apiMocks.getSkillVersionFiles).toHaveBeenCalledTimes(2)
  })
})
