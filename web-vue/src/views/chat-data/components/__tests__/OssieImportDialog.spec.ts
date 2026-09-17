import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiMocks = vi.hoisted(() => ({
  previewAdminOssieSemanticImport: vi.fn(),
  importAdminOssieSemanticConfigs: vi.fn(),
  getAdminSemanticConfigs: vi.fn(),
}))

vi.mock('../../api', () => apiMocks)

vi.mock('element-plus', () => ({
  ElMessage: {
    error: vi.fn(),
    success: vi.fn(),
    warning: vi.fn(),
  },
}))

import OssieImportDialog from '../semantic/OssieImportDialog.vue'

// 注意：组件调用 API 时会 trim 内容，这里不要带尾随换行
const yamlContent = 'version: "1.0"\nsemantic_model:\n  - name: 商品模型'

const importableModel = {
  name: '商品模型',
  description: '商品',
  business_notes: '',
  metrics: [],
  dimensions: [],
  relationships: [],
  source_config_id: 'sc_old',
  errors: [],
  warnings: ['来源配置 sc_old：将创建新草稿，不更新原配置'],
  importable: true,
  checklist: [
    { group: 'schema', name: '文档结构合法性', status: 'pass', message: 'OSSI 官方 schema 校验通过' },
    { group: 'schema', name: 'datatype 枚举合法性', status: 'pass', message: '全部字段类型为 OSI 合法枚举' },
    { group: 'mapping', name: '表归属确定', status: 'warn', message: '1 个对象未声明归属表，按唯一 dataset 推断' },
    { group: 'mapping', name: '派生 / 复合指标还原', status: 'pass', message: '模型内无派生/复合指标' },
    { group: 'physical', name: '物理字段校验', status: 'pass', message: '全部命中数据源物理元数据' },
    { group: 'quality', name: '质量规则核对', status: 'pass', message: '26 条质量规则全部未触发' },
  ],
}

const blockedModel = {
  name: '残缺模型',
  metrics: [],
  dimensions: [],
  relationships: [],
  errors: ['指标 A 声明为派生指标但缺少扩展中的 base_metric，无法还原口径'],
  warnings: [],
  importable: false,
}

const mountDialog = async () => {
  const wrapper = mount(OssieImportDialog, {
    props: {
      visible: false,
      datasourceId: 'ds_a',
    },
    global: {
      stubs: {
        'el-dialog': {
          template: '<div><slot /><slot name="footer" /></div>',
        },
        'el-button': {
          props: ['disabled', 'loading', 'type', 'size'],
          emits: ['click'],
          template: '<button :disabled="disabled || loading" @click="$emit(\'click\')"><slot /></button>',
        },
        // jsdom 下不真跑 CodeMirror MergeView，只断言挂载与入参
        OssieMergeDiff: {
          props: ['existing', 'imported'],
          template: '<div class="merge-diff-stub">MERGE_DIFF:{{ existing.id }}</div>',
        },
      },
    },
  })
  // 模拟真实打开路径，验证 visible=false → true 的关闭 emits
  await wrapper.setProps({ visible: true })
  return wrapper
}

type DialogWrapper = Awaited<ReturnType<typeof mountDialog>>

const findButton = (wrapper: DialogWrapper, text: string) =>
  wrapper.findAll('button').find(button => button.text().includes(text))

/** 模拟选择文件：真实 File + FileReader（jsdom 原生支持），触发 change 后等 onload 完成 */
const chooseYamlFile = async (wrapper: DialogWrapper, content = yamlContent) => {
  const input = wrapper.find('input[type="file"]')
  const file = new File([content], 'model.yaml', { type: 'application/yaml' })
  Object.defineProperty(input.element, 'files', { value: [file] })
  await input.trigger('change')
  // FileReader 的 load 回调是宏任务，flushPromises 等不到，需显式让出事件循环
  await new Promise(resolve => setTimeout(resolve, 0))
  await flushPromises()
}

const chooseFileAndPreview = async (wrapper: DialogWrapper) => {
  await chooseYamlFile(wrapper)
  await findButton(wrapper, '模型校验')!.trigger('click')
  await flushPromises()
}

describe('OssieImportDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('empty yaml blocks preview and does not call the API', async () => {
    const wrapper = await mountDialog()
    await findButton(wrapper, '模型校验')!.trigger('click')
    await flushPromises()

    expect(apiMocks.previewAdminOssieSemanticImport).not.toHaveBeenCalled()
  })

  it('file selection loads content and preview renders importable and blocked models', async () => {
    apiMocks.previewAdminOssieSemanticImport.mockResolvedValue({
      status: 'success',
      data: { models: [importableModel, blockedModel] },
    })
    const wrapper = await mountDialog()
    await chooseFileAndPreview(wrapper)

    expect(wrapper.text()).toContain('model.yaml')
    expect(apiMocks.previewAdminOssieSemanticImport).toHaveBeenCalledWith('ds_a', yamlContent)
    expect(wrapper.text()).toContain('商品模型')
    expect(wrapper.text()).toContain('可导入')
    expect(wrapper.text()).toContain('0 维度 · 0 指标 · 0 关系')
    expect(wrapper.text()).toContain('来源配置：sc_old（将创建新草稿，不更新原配置）')
    expect(wrapper.text()).toContain('残缺模型')
    expect(wrapper.text()).toContain('存在问题')
    expect(wrapper.text()).toContain('缺少扩展中的 base_metric')
    // 校验清单渲染：组头 + 状态标签
    expect(wrapper.text()).toContain('② 结构映射')
    expect(wrapper.text()).toContain('物理字段校验')
    // 存在不可导入模型时，确认导入必须禁用
    expect(findButton(wrapper, '确认导入')!.attributes('disabled')).toBeDefined()
  })

  it('preview failure shows the backend message and keeps no models', async () => {
    apiMocks.previewAdminOssieSemanticImport.mockResolvedValue({
      status: 'error',
      msg: 'Ossie 文档结构校验失败（semantic_model.0.name）: Field required',
    })
    const wrapper = await mountDialog()
    await chooseFileAndPreview(wrapper)

    expect(wrapper.text()).not.toContain('可导入')
    expect(findButton(wrapper, '确认导入')!.attributes('disabled')).toBeDefined()
  })

  it('import calls the API, emits imported and closes on success', async () => {
    apiMocks.previewAdminOssieSemanticImport.mockResolvedValue({
      status: 'success',
      data: { models: [importableModel] },
    })
    apiMocks.importAdminOssieSemanticConfigs.mockResolvedValue({
      status: 'success',
      data: { created: [{ config_id: 'sc_new', name: '商品模型' }], models: [importableModel] },
    })
    const wrapper = await mountDialog()
    await chooseFileAndPreview(wrapper)

    expect(findButton(wrapper, '确认导入')!.attributes('disabled')).toBeUndefined()
    await findButton(wrapper, '确认导入')!.trigger('click')
    await flushPromises()

    expect(apiMocks.importAdminOssieSemanticConfigs).toHaveBeenCalledWith('ds_a', yamlContent, {})
    expect(wrapper.emitted('imported')).toEqual([[1]])
    expect(wrapper.emitted('update:visible')).toEqual([[false]])
  })

  it('import failure refreshes the model list with blocking issues', async () => {
    apiMocks.previewAdminOssieSemanticImport.mockResolvedValue({
      status: 'success',
      data: { models: [importableModel] },
    })
    apiMocks.importAdminOssieSemanticConfigs.mockResolvedValue({
      status: 'error',
      msg: '导入失败',
      data: { created: [], models: [blockedModel] },
    })
    const wrapper = await mountDialog()
    await chooseFileAndPreview(wrapper)

    await findButton(wrapper, '确认导入')!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('残缺模型')
    expect(wrapper.emitted('imported')).toBeUndefined()
  })

  it('preview renders the checklist table and triggered findings without score', async () => {
    const modelWithQuality = {
      ...importableModel,
      quality: {
        findings: [
          {
            code: 'COV_UNMODELED_COLUMNS',
            severity: 'info',
            category: 'coverage',
            message: '已建模表存在未纳入任何维度/指标的物理列: orders.id 等 2 处',
            targets: ['orders.id', 'orders.status'],
          },
          {
            code: 'COV_NO_METRICS',
            severity: 'warning',
            category: 'coverage',
            message: '配置只有维度没有指标，无法回答聚合类问题: 空指标模型',
            targets: ['空指标模型'],
          },
        ],
      },
    }
    apiMocks.previewAdminOssieSemanticImport.mockResolvedValue({
      status: 'success',
      data: { models: [modelWithQuality] },
    })
    const wrapper = await mountDialog()
    await chooseFileAndPreview(wrapper)

    // 清单四组组头与状态标签；评分不再透出
    expect(wrapper.text()).toContain('① OSSI 校验')
    expect(wrapper.text()).toContain('② 结构映射')
    expect(wrapper.text()).toContain('③ 物理校验')
    expect(wrapper.text()).toContain('④ 质量规则')
    expect(wrapper.text()).toContain('警告')
    expect(wrapper.text()).toContain('触发的质量规则')
    expect(wrapper.text()).toContain('[COV_NO_METRICS]')
    expect(wrapper.text()).not.toContain('分 · ')
    // 质量问题不阻断导入（与四道闸门的 errors 语义分离）
    expect(findButton(wrapper, '确认导入')!.attributes('disabled')).toBeUndefined()
  })

  it('blocked models without quality field render no findings block', async () => {
    apiMocks.previewAdminOssieSemanticImport.mockResolvedValue({
      status: 'success',
      data: { models: [blockedModel] },
    })
    const wrapper = await mountDialog()
    await chooseFileAndPreview(wrapper)

    expect(wrapper.text()).not.toContain('触发的质量规则')
    expect(wrapper.text()).toContain('存在问题')
  })

  it('multiple files are previewed per file and imported per file', async () => {
    apiMocks.previewAdminOssieSemanticImport
      .mockResolvedValueOnce({ status: 'success', data: { models: [importableModel] } })
      .mockResolvedValueOnce({ status: 'success', data: { models: [blockedModel] } })
    apiMocks.importAdminOssieSemanticConfigs
      .mockResolvedValueOnce({ status: 'success', data: { created: [{ config_id: 'sc_1', name: '商品模型' }] } })
      .mockResolvedValueOnce({ status: 'error', msg: '存在无法导入的模型' })

    const wrapper = await mountDialog()
    const input = wrapper.find('input[type="file"]')
    const fileA = new File([yamlContent], 'a.yaml', { type: 'application/yaml' })
    const fileB = new File([yamlContent], 'b.yaml', { type: 'application/yaml' })
    Object.defineProperty(input.element, 'files', { value: [fileA, fileB] })
    await input.trigger('change')
    await new Promise(resolve => setTimeout(resolve, 0))
    await flushPromises()

    expect(wrapper.text()).toContain('已选择 2 个文件')
    await findButton(wrapper, '模型校验')!.trigger('click')
    await flushPromises()

    // 逐文件预览：两个文件各请求一次
    expect(apiMocks.previewAdminOssieSemanticImport).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('商品模型')
    expect(wrapper.text()).toContain('残缺模型')
    // 多文件时标注来源文件
    expect(wrapper.text()).toContain('来源文件：a.yaml')

    // b.yaml 存在阻断问题：整体确认按钮禁用
    expect(findButton(wrapper, '确认导入')!.attributes('disabled')).toBeDefined()
  })

  it('conflicted model defaults to replace and sends conflict_actions on import', async () => {
    const conflictedModel = { ...importableModel, existing_config: { config_id: 'sc_old', name: '商品模型', enabled: true } }
    apiMocks.previewAdminOssieSemanticImport.mockResolvedValue({
      status: 'success',
      data: { models: [conflictedModel] },
    })
    apiMocks.importAdminOssieSemanticConfigs.mockResolvedValue({
      status: 'success',
      data: { created: [], replaced: [{ config_id: 'sc_old', name: '商品模型' }] },
    })
    const wrapper = await mountDialog()
    await chooseFileAndPreview(wrapper)

    expect(wrapper.text()).toContain('已存在同名配置（启用中）')
    // 默认选中「替换」，footer 汇总展示替换数量
    expect(wrapper.text()).toContain('将新建 0 · 替换 1')
    await findButton(wrapper, '确认导入')!.trigger('click')
    await flushPromises()

    expect(apiMocks.importAdminOssieSemanticConfigs).toHaveBeenCalledWith(
      'ds_a',
      yamlContent,
      { 商品模型: 'replace' },
    )
  })

  it('choosing skip sends skip action and reports no-change import', async () => {
    const conflictedModel = { ...importableModel, existing_config: { config_id: 'sc_old', name: '商品模型', enabled: false } }
    apiMocks.previewAdminOssieSemanticImport.mockResolvedValue({
      status: 'success',
      data: { models: [conflictedModel] },
    })
    apiMocks.importAdminOssieSemanticConfigs.mockResolvedValue({
      status: 'success',
      data: { created: [], replaced: [], skipped: [{ name: '商品模型', config_id: 'sc_old', reason: '同名配置已存在，未导入' }] },
    })
    const wrapper = await mountDialog()
    await chooseFileAndPreview(wrapper)

    const skipRadio = wrapper.findAll('input[type="radio"]')[1]
    await skipRadio!.setValue()
    expect(wrapper.text()).toContain('将新建 0 · 跳过 1')

    await findButton(wrapper, '确认导入')!.trigger('click')
    await flushPromises()
    expect(apiMocks.importAdminOssieSemanticConfigs).toHaveBeenCalledWith(
      'ds_a',
      yamlContent,
      { 商品模型: 'skip' },
    )
    expect(apiMocks.importAdminOssieSemanticConfigs).toHaveBeenCalledTimes(1)
  })

  it('diff toggle loads existing configs and renders self-built diff lines', async () => {
    const conflictedModel = {
      ...importableModel,
      metrics: [{ name: '总额', expression: 'SUM(amount)', table: 'orders' }],
      existing_config: { config_id: 'sc_old', name: '商品模型', enabled: true },
    }
    apiMocks.previewAdminOssieSemanticImport.mockResolvedValue({
      status: 'success',
      data: { models: [conflictedModel] },
    })
    apiMocks.getAdminSemanticConfigs.mockResolvedValue({
      status: 'success',
      data: {
        configs: [{
          id: 'sc_old',
          name: '商品模型',
          description: '旧描述',
          metrics: [
            { name: '总额', expression: 'SUM(amount)', table: 'orders' },
            { name: '旧指标', expression: 'COUNT(1)', table: 'orders' },
          ],
          dimensions: [],
          relationships: [],
        }],
      },
    })
    const wrapper = await mountDialog()
    await chooseFileAndPreview(wrapper)

    await wrapper.findAll('button').find(button => button.text().includes('查看与现有配置的差异'))!.trigger('click')
    await flushPromises()

    expect(apiMocks.getAdminSemanticConfigs).toHaveBeenCalledWith('ds_a')
    // CodeMirror MergeView 挂载（stub），左栏为按 config_id 解析出的现有配置
    expect(wrapper.find('.merge-diff-stub').exists()).toBe(true)
    expect(wrapper.find('.merge-diff-stub').text()).toContain('MERGE_DIFF:sc_old')
  })

  it('diff shows fallback message when existing config is missing', async () => {
    const conflictedModel = {
      ...importableModel,
      existing_config: { config_id: 'sc_gone', name: '商品模型', enabled: true },
    }
    apiMocks.previewAdminOssieSemanticImport.mockResolvedValue({
      status: 'success',
      data: { models: [conflictedModel] },
    })
    apiMocks.getAdminSemanticConfigs.mockResolvedValue({
      status: 'success',
      data: { configs: [] },
    })
    const wrapper = await mountDialog()
    await chooseFileAndPreview(wrapper)

    await wrapper.findAll('button').find(button => button.text().includes('查看与现有配置的差异'))!.trigger('click')
    await flushPromises()

    expect(wrapper.find('.merge-diff-stub').exists()).toBe(false)
    expect(wrapper.text()).toContain('未找到现有配置详情，无法对比')
  })

  it('unchecked files are excluded from preview and import', async () => {
    apiMocks.previewAdminOssieSemanticImport.mockResolvedValue({
      status: 'success',
      data: { models: [importableModel] },
    })
    apiMocks.importAdminOssieSemanticConfigs.mockResolvedValue({
      status: 'success',
      data: { created: [{ config_id: 'sc_1', name: '商品模型' }] },
    })
    const wrapper = await mountDialog()
    const input = wrapper.find('input[type="file"]')
    const fileA = new File([yamlContent], 'a.yaml', { type: 'application/yaml' })
    const fileB = new File([yamlContent], 'b.yaml', { type: 'application/yaml' })
    Object.defineProperty(input.element, 'files', { value: [fileA, fileB] })
    await input.trigger('change')
    await new Promise(resolve => setTimeout(resolve, 0))
    await flushPromises()

    // 取消勾选第二个文件（checkbox 顺序：表头全选、文件A、文件B）
    const checkboxes = wrapper.findAll('input[type="checkbox"]')
    expect(checkboxes.length).toBe(3)
    await checkboxes[2]!.setValue(false)

    await findButton(wrapper, '模型校验')!.trigger('click')
    await flushPromises()

    // 只校验勾选的文件 A
    expect(apiMocks.previewAdminOssieSemanticImport).toHaveBeenCalledTimes(1)
    expect(apiMocks.previewAdminOssieSemanticImport).toHaveBeenCalledWith('ds_a', yamlContent)
    expect(wrapper.text()).not.toContain('来源文件：b.yaml')

    await findButton(wrapper, '确认导入')!.trigger('click')
    await flushPromises()
    // 只导入文件 A 的模型
    expect(apiMocks.importAdminOssieSemanticConfigs).toHaveBeenCalledTimes(1)
    expect(apiMocks.importAdminOssieSemanticConfigs).toHaveBeenCalledWith('ds_a', yamlContent, {})
  })

  it('selecting more files appends instead of replacing', async () => {
    const wrapper = await mountDialog()
    const input = wrapper.find('input[type="file"]')
    // configurable: true —— 同一 input 需要两次重定义 files（追加上传）
    Object.defineProperty(input.element, 'files', {
      value: [new File([yamlContent], 'a.yaml', { type: 'application/yaml' })],
      configurable: true,
    })
    await input.trigger('change')
    await new Promise(resolve => setTimeout(resolve, 0))
    Object.defineProperty(input.element, 'files', {
      value: [new File([yamlContent], 'b.yaml', { type: 'application/yaml' })],
      configurable: true,
    })
    await input.trigger('change')
    await new Promise(resolve => setTimeout(resolve, 0))
    await flushPromises()

    expect(wrapper.text()).toContain('已选择 2 个文件')
    expect(wrapper.text()).toContain('a.yaml')
    expect(wrapper.text()).toContain('b.yaml')
  })

  it('removed file drops its models and conflict state', async () => {
    apiMocks.previewAdminOssieSemanticImport.mockResolvedValue({
      status: 'success',
      data: { models: [importableModel] },
    })
    const wrapper = await mountDialog()
    const input = wrapper.find('input[type="file"]')
    const fileA = new File([yamlContent], 'a.yaml', { type: 'application/yaml' })
    const fileB = new File([yamlContent], 'b.yaml', { type: 'application/yaml' })
    Object.defineProperty(input.element, 'files', { value: [fileA, fileB] })
    await input.trigger('change')
    await new Promise(resolve => setTimeout(resolve, 0))
    await flushPromises()
    await findButton(wrapper, '模型校验')!.trigger('click')
    await flushPromises()

    // 移除文件 B（含阻断模型）：确认导入恢复可用
    const removeButtons = wrapper.findAll('button').filter(button => button.find('i.fa-trash-can').exists())
    await removeButtons[1]!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).not.toContain('残缺模型')
    expect(findButton(wrapper, '确认导入')!.attributes('disabled')).toBeUndefined()
    // 文件表只剩一行（清单表也在 tbody 里，按删除按钮所在行过滤）
    const fileRows = wrapper.findAll('tbody tr').filter(tr => tr.find('i.fa-trash-can').exists())
    expect(fileRows.length).toBe(1)
  })
})
