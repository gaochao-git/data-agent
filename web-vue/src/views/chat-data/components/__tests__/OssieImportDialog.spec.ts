import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiMocks = vi.hoisted(() => ({
  previewAdminOssieSemanticImport: vi.fn(),
  importAdminOssieSemanticConfigs: vi.fn(),
  getAdminSemanticConfigs: vi.fn(),
}))

// 提示与「替换」二次确认都走 hoisted mock：便于断言，且不必引用未类型化的真实模块
const elMocks = vi.hoisted(() => ({
  error: vi.fn(),
  success: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
  boxConfirm: vi.fn(),
}))

vi.mock('../../api', () => apiMocks)

vi.mock('element-plus', () => ({
  ElMessage: {
    error: elMocks.error,
    success: elMocks.success,
    warning: elMocks.warning,
    info: elMocks.info,
  },
  ElMessageBox: {
    confirm: elMocks.boxConfirm,
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
  warnings: ['语义模型 商品模型 缺少 CHAT_DATA 扩展的 table，按唯一 dataset 推断归属: orders'],
  importable: true,
  checklist: [
    { group: 'schema', name: '文档结构合法性', status: 'pass', message: 'OSSI 官方 schema 校验通过' },
    { group: 'schema', name: 'datatype 枚举合法性', status: 'pass', message: '全部字段类型为 OSI 合法枚举' },
    { group: 'mapping', name: '表归属确定', status: 'warn', message: '1 个对象未声明归属表，按唯一 dataset 推断' },
    { group: 'mapping', name: '派生 / 复合指标还原', status: 'pass', message: '模型内无派生/复合指标' },
    { group: 'physical', name: '物理字段校验', status: 'pass', message: '全部命中数据源物理元数据' },
    { group: 'quality', name: '质量规则核对', status: 'pass', message: '20 条质量规则全部未触发' },
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

/** 文件表里的文件行（收起态）：处置控件（替换/忽略/导入）应落在这里，展开区只放详情 */
const findFileRow = (wrapper: DialogWrapper, fileName: string) =>
  wrapper.findAll('tr').find(row => row.text().includes(fileName))!

/**
 * 等文件行的 DOM 稳定再继续。
 * FileReader 的 load 落在哪个任务队列不确定，固定 `setTimeout(0)` 在跑全量用例时会偶发抢跑：
 * 点「模型校验」时 files 还是空的（该按钮此时禁用）→ 点击静默失效，表现为用例偶发失败。
 */
const waitForFileRows = async (wrapper: DialogWrapper) => {
  let lastCount = -1
  let stableRounds = 0
  for (let i = 0; i < 20 && stableRounds < 2; i += 1) {
    await new Promise(resolve => setTimeout(resolve, 0))
    await flushPromises()
    const count = wrapper.findAll('i.fa-trash-can').length
    stableRounds = count > 0 && count === lastCount ? stableRounds + 1 : 0
    lastCount = count
  }
}

/** 模拟选择文件：真实 File + FileReader（jsdom 原生支持），触发 change 后等文件行渲染出来 */
const chooseYamlFile = async (wrapper: DialogWrapper, content = yamlContent) => {
  const input = wrapper.find('input[type="file"]')
  const file = new File([content], 'model.yaml', { type: 'application/yaml' })
  Object.defineProperty(input.element, 'files', { value: [file] })
  await input.trigger('change')
  await waitForFileRows(wrapper)
}

const chooseFileAndPreview = async (wrapper: DialogWrapper) => {
  await chooseYamlFile(wrapper)
  await findButton(wrapper, '模型校验')!.trigger('click')
  await flushPromises()
}

describe('OssieImportDialog', () => {
  beforeEach(() => {
    // resetAllMocks（而非 clearAllMocks）：未消费的 mockResolvedValueOnce 会按序漏给下一条用例，
    // 导致「用例自己设的成功响应」被上一条残留的错误响应用掉（实测踩坑：单文件导入用例拿到 error）
    vi.resetAllMocks()
    // 确认框默认「确认」：真实 ElMessageBox.confirm 恒返回 Promise，不设默认实现会在 .then 上炸掉
    elMocks.boxConfirm.mockResolvedValue('confirm')
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
    // 「来源配置：sc_xxx（将创建新草稿，不更新原配置）」按产品口径不再展示
    expect(wrapper.text()).not.toContain('来源配置：')
    expect(wrapper.text()).not.toContain('将创建新草稿')
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
    await waitForFileRows(wrapper)

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

    // 冲突提示收敛为收起行的图标 title（完整说明由下方「模型名冲突」清单行承载）
    expect(wrapper.find('[title="已存在同名配置（启用中）"]').exists()).toBe(true)
    // 处置控件在收起态的文件行：替换 / 忽略 / 单文件导入
    const fileRow = findFileRow(wrapper, 'model.yaml')
    expect(fileRow.findAll('input[type="radio"]')).toHaveLength(2)
    expect(fileRow.text()).toContain('替换')
    expect(fileRow.text()).toContain('忽略')
    expect(fileRow.text()).toContain('导入')
    // 展开后的模型卡片不重复处置控件，只保留差异对比入口（展开=看详情）
    const modelCard = wrapper.findAll('span').find(node => node.text() === '商品模型')!.element.closest('.rounded-md')
    expect(modelCard!.querySelectorAll('input[type="radio"]')).toHaveLength(0)
    expect(modelCard!.textContent).toContain('查看与现有配置的差异')
    // 默认选中「替换」，footer 汇总展示替换数量
    expect(wrapper.text()).toContain('将新建 0 · 替换 1')
    // 替换会覆盖现有配置并强制禁用，属不可逆动作：先走「取消」分支，必须一个请求都不发
    elMocks.boxConfirm.mockRejectedValue('cancel')
    await findButton(wrapper, '确认导入')!.trigger('click')
    await flushPromises()

    expect(elMocks.boxConfirm).toHaveBeenCalledTimes(1)
    expect(elMocks.boxConfirm.mock.calls[0]![0]).toContain('覆盖 1 个同名现有配置')
    expect(apiMocks.importAdminOssieSemanticConfigs).not.toHaveBeenCalled()

    elMocks.boxConfirm.mockResolvedValue('confirm')
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

    const skipRadio = findFileRow(wrapper, 'model.yaml').findAll('input[type="radio"]')[1]
    await skipRadio!.setValue()
    expect(wrapper.text()).toContain('将新建 0 · 忽略 1')

    await findButton(wrapper, '确认导入')!.trigger('click')
    await flushPromises()
    expect(apiMocks.importAdminOssieSemanticConfigs).toHaveBeenCalledWith(
      'ds_a',
      yamlContent,
      { 商品模型: 'skip' },
    )
    expect(apiMocks.importAdminOssieSemanticConfigs).toHaveBeenCalledTimes(1)
  })

  it('row-level import imports only that file and keeps the dialog for the rest', async () => {
    apiMocks.previewAdminOssieSemanticImport
      .mockResolvedValueOnce({ status: 'success', data: { models: [importableModel] } })
      .mockResolvedValueOnce({ status: 'success', data: { models: [{ ...importableModel, name: '订单模型' }] } })
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
    await waitForFileRows(wrapper)
    await findButton(wrapper, '模型校验')!.trigger('click')
    await flushPromises()

    // 收起行「导入」只请求该文件，用该文件自身的 YAML 内容
    await findFileRow(wrapper, 'a.yaml').findAll('button').find(button => button.text() === '导入')!.trigger('click')
    await flushPromises()

    expect(apiMocks.importAdminOssieSemanticConfigs).toHaveBeenCalledTimes(1)
    expect(apiMocks.importAdminOssieSemanticConfigs).toHaveBeenCalledWith('ds_a', yamlContent, {})
    // 已导入的文件从列表移除，弹窗保留给剩余文件；父组件按次数刷新配置列表
    expect(wrapper.text()).not.toContain('a.yaml')
    expect(wrapper.text()).toContain('b.yaml')
    expect(wrapper.emitted('imported')).toEqual([[1]])
  })

  it('locked conflict (others private config) is skipped without replace option', async () => {
    const lockedModel = { ...importableModel, conflict_locked: true }
    apiMocks.previewAdminOssieSemanticImport.mockResolvedValue({
      status: 'success',
      data: { models: [lockedModel] },
    })
    apiMocks.importAdminOssieSemanticConfigs.mockResolvedValue({
      status: 'success',
      data: { created: [], replaced: [], skipped: [{ name: '商品模型', reason: '存在同名配置且无操作权限，已跳过' }] },
    })
    const wrapper = await mountDialog()
    await chooseFileAndPreview(wrapper)

    expect(wrapper.text()).toContain('已存在同名配置但无操作权限，导入时将忽略该模型')
    // 无操作权限的同名配置不允许 replace：不渲染替换/忽略单选，也不计入「新建」
    expect(wrapper.findAll('input[type="radio"]')).toHaveLength(0)
    expect(wrapper.text()).toContain('将新建 0 · 忽略 1')

    await findButton(wrapper, '确认导入')!.trigger('click')
    await flushPromises()
    expect(apiMocks.importAdminOssieSemanticConfigs).toHaveBeenCalledWith('ds_a', yamlContent, {})
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
    await waitForFileRows(wrapper)

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
    await waitForFileRows(wrapper)
    Object.defineProperty(input.element, 'files', {
      value: [new File([yamlContent], 'b.yaml', { type: 'application/yaml' })],
      configurable: true,
    })
    await input.trigger('change')
    await waitForFileRows(wrapper)

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
    await waitForFileRows(wrapper)
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

  it('files added after the last preview block the batch import until re-validated', async () => {
    apiMocks.previewAdminOssieSemanticImport.mockResolvedValue({
      status: 'success',
      data: { models: [importableModel] },
    })
    const wrapper = await mountDialog()
    const input = wrapper.find('input[type="file"]')
    Object.defineProperty(input.element, 'files', {
      value: [new File([yamlContent], 'a.yaml', { type: 'application/yaml' })],
      configurable: true,
    })
    await input.trigger('change')
    await waitForFileRows(wrapper)
    await findButton(wrapper, '模型校验')!.trigger('click')
    await flushPromises()
    expect(findButton(wrapper, '确认导入')!.attributes('disabled')).toBeUndefined()

    // 追加一个没参与本轮校验的文件：它会被导入循环跳过，必须整体拦住而不是静默丢掉
    Object.defineProperty(input.element, 'files', {
      value: [new File([yamlContent], 'b.yaml', { type: 'application/yaml' })],
      configurable: true,
    })
    await input.trigger('change')
    await waitForFileRows(wrapper)

    expect(findButton(wrapper, '确认导入')!.attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('1 个文件尚未校验，请点「模型校验」')
    const pendingRow = findFileRow(wrapper, 'b.yaml')
    expect(pendingRow.text()).toContain('未校验')
    const pendingImport = pendingRow.findAll('button').find(button => button.text() === '导入')!
    expect(pendingImport.attributes('disabled')).toBeDefined()
    expect(pendingImport.attributes('title')).toBe('请先点击「模型校验」')
  })

  it('file-level preview failure shows error status instead of unvalidated', async () => {
    apiMocks.previewAdminOssieSemanticImport.mockResolvedValue({
      status: 'error',
      msg: 'Ossie 文档结构校验失败（semantic_model.0.name）: Field required',
    })
    const wrapper = await mountDialog()
    await chooseFileAndPreview(wrapper)

    const row = findFileRow(wrapper, 'model.yaml')
    expect(row.text()).toContain('错误')
    expect(row.text()).not.toContain('未校验')
    // 失败原因挂在状态徽标 / 行内导入按钮的 title 上
    expect(row.find('[title*="文档结构校验失败"]').exists()).toBe(true)
    expect(findButton(wrapper, '确认导入')!.attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('1 个文件校验失败，请查看文件行提示')
  })

  it('batch import drops each file as it succeeds so a retry cannot re-import it', async () => {
    apiMocks.previewAdminOssieSemanticImport
      .mockResolvedValueOnce({ status: 'success', data: { models: [importableModel] } })
      .mockResolvedValueOnce({ status: 'success', data: { models: [{ ...importableModel, name: '订单模型' }] } })
    apiMocks.importAdminOssieSemanticConfigs
      .mockResolvedValueOnce({ status: 'success', data: { created: [{ config_id: 'sc_1', name: '商品模型' }] } })
      .mockResolvedValueOnce({ status: 'error', msg: '存在无法导入的模型' })

    const wrapper = await mountDialog()
    const input = wrapper.find('input[type="file"]')
    Object.defineProperty(input.element, 'files', {
      value: [
        new File([yamlContent], 'a.yaml', { type: 'application/yaml' }),
        new File([yamlContent], 'b.yaml', { type: 'application/yaml' }),
      ],
    })
    await input.trigger('change')
    await waitForFileRows(wrapper)
    await findButton(wrapper, '模型校验')!.trigger('click')
    await flushPromises()

    await findButton(wrapper, '确认导入')!.trigger('click')
    await flushPromises()

    expect(apiMocks.importAdminOssieSemanticConfigs).toHaveBeenCalledTimes(2)
    // 成功的 a.yaml 已从文件表移除：重试只会再请求 b.yaml
    expect(wrapper.text()).not.toContain('a.yaml')
    expect(wrapper.text()).toContain('b.yaml')
    expect(wrapper.emitted('imported')).toEqual([[1]])
    // 中途失败不关弹窗，留给用户改完问题再重试
    expect(wrapper.emitted('update:visible')).toBeUndefined()
  })

  it('freezes validation and conflict controls while an import is in flight', async () => {
    apiMocks.previewAdminOssieSemanticImport.mockResolvedValue({
      status: 'success',
      data: { models: [{ ...importableModel, existing_config: { config_id: 'sc_old', name: '商品模型', enabled: true } }] },
    })
    let finishImport: (value: unknown) => void = () => {}
    apiMocks.importAdminOssieSemanticConfigs.mockReturnValue(new Promise(resolve => { finishImport = resolve }))

    const wrapper = await mountDialog()
    await chooseFileAndPreview(wrapper)
    elMocks.boxConfirm.mockResolvedValue('confirm')
    await findButton(wrapper, '确认导入')!.trigger('click')
    await flushPromises()

    // 在途期间：重新校验会重建 models、删文件会抽走正在导入的数据，都必须冻结
    expect(findButton(wrapper, '模型校验')!.attributes('disabled')).toBeDefined()
    const row = findFileRow(wrapper, 'model.yaml')
    expect(row.findAll('input[type="radio"]')[0]!.attributes('disabled')).toBeDefined()
    const removeButton = row.find('i.fa-trash-can').element.closest('button') as HTMLButtonElement
    expect(removeButton.disabled).toBe(true)

    finishImport({ status: 'success', data: { created: [{ config_id: 'sc_1', name: '商品模型' }] } })
    await flushPromises()
    expect(wrapper.emitted('update:visible')).toEqual([[false]])
  })
})
